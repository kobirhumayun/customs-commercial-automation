from __future__ import annotations

from decimal import Decimal
import unittest

from project.models import WorkflowId
from project.rules import load_rule_pack
from project.workbook import WorkbookHeader, WorkbookRow, WorkbookSnapshot
from project.workflows.snapshot import SourceEmailRecord, build_email_snapshot
from project.workflows.ud_ip_exp import (
    DocumentExtractionField,
    UDCandidateRow,
    UDDocumentPayload,
    UDIPEXPQuantity,
    allocate_ud_rows,
    build_ud_selection_report,
)
from project.workflows.ud_ip_exp.validation import assemble_ud_validation


class UDIPEXPReportingTests(unittest.TestCase):
    def test_build_ud_selection_report_includes_required_selected_candidate_evidence(self) -> None:
        allocation = allocate_ud_rows(
            required_quantity=Decimal("3000"),
            quantity_unit="YDS",
            candidate_rows=[
                UDCandidateRow(
                    row_index=11,
                    lc_sc_number="LC-0043",
                    quantity=Decimal("1000"),
                    quantity_unit="YDS",
                    lc_amnd_no="1",
                    lc_amnd_date="2026-01-02",
                ),
                UDCandidateRow(
                    row_index=19,
                    lc_sc_number="LC-0043",
                    quantity=Decimal("2000"),
                    quantity_unit="YDS",
                    lc_amnd_no="2",
                    lc_amnd_date="2026-02-10",
                ),
            ],
        )

        report = build_ud_selection_report(allocation)

        self.assertEqual(report["required_quantity"], "3000")
        self.assertEqual(report["quantity_unit"], "YDS")
        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(report["final_decision"], "selected")
        self.assertEqual(report["selected_candidate_id"], "11-19")
        self.assertEqual(report["candidates"][0]["candidate_id"], "11-19")
        self.assertEqual(report["candidates"][0]["row_indexes"], [11, 19])
        self.assertEqual(report["candidates"][0]["matched_quantities"], ["1000", "2000"])
        self.assertEqual(
            report["candidates"][0]["score_keys"],
            {
                "row_index_key": [11, 19],
                "amendment_recency_key": [["2026-01-02", 1], ["2026-02-10", 2]],
                "blank_field_priority_key": {
                    "blank_target_count_desc": -2,
                    "nonblank_optional_count_asc": 0,
                },
                "stable_candidate_id_key": "11-19",
            },
        )

    def test_build_ud_selection_report_maps_full_tie_to_hard_block_tie(self) -> None:
        allocation = allocate_ud_rows(
            required_quantity=Decimal("1000"),
            quantity_unit="YDS",
            candidate_rows=[
                UDCandidateRow(row_index=11, lc_sc_number="LC-0043", quantity=Decimal("1000"), quantity_unit="YDS"),
                UDCandidateRow(row_index=11, lc_sc_number="LC-0043", quantity=Decimal("1000"), quantity_unit="YDS"),
            ],
        )

        report = build_ud_selection_report(allocation)

        self.assertEqual(report["final_decision"], "hard_block_tie")
        self.assertEqual(report["discrepancy_code"], "ud_candidate_tie_after_full_tiebreak")
        self.assertEqual([candidate["selected"] for candidate in report["candidates"]], [False, False])
        self.assertEqual(
            {candidate["rejection_reason"] for candidate in report["candidates"]},
            {"tied_after_full_tiebreak"},
        )

    def test_assemble_ud_validation_exposes_ud_selection_report(self) -> None:
        result = assemble_ud_validation(
            run_id="run-1",
            mail=_mail(),
            rule_pack=load_rule_pack(WorkflowId.UD_IP_EXP),
            ud_document=_ud_document(),
            workbook_snapshot=_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000 YDS", 3: "", 4: "", 5: ""}),
                ]
            ),
        )

        self.assertIsNotNone(result.ud_selection)
        self.assertEqual(result.ud_selection["final_decision"], "selected")
        self.assertEqual(result.ud_selection["selected_candidate_id"], "11")


def _ud_document() -> UDDocumentPayload:
    return UDDocumentPayload(
        document_number=DocumentExtractionField("UD-LC-0043-ANANTA"),
        document_date=DocumentExtractionField("2026-04-01"),
        lc_sc_number=DocumentExtractionField("LC-0043"),
        quantity=UDIPEXPQuantity(amount=Decimal("1000"), unit="YDS"),
    )


def _mail():
    return build_email_snapshot(
        [
            SourceEmailRecord(
                entry_id="entry-ud-001",
                received_time="2026-04-01T03:00:00Z",
                subject_raw="UD-LC-0043-ANANTA",
                sender_address="sender@example.com",
            )
        ],
        state_timezone="Asia/Dhaka",
    )[0]


def _snapshot(*, rows: list[WorkbookRow]) -> WorkbookSnapshot:
    return WorkbookSnapshot(
        sheet_name="Sheet1",
        headers=[
            WorkbookHeader(column_index=1, text="L/C & S/C No."),
            WorkbookHeader(column_index=2, text="Quantity of Fabrics (Yds/Mtr)"),
            WorkbookHeader(column_index=3, text="UD No. & IP No."),
            WorkbookHeader(column_index=4, text="L/C Amnd No."),
            WorkbookHeader(column_index=5, text="L/C Amnd Date"),
        ],
        rows=rows,
    )


if __name__ == "__main__":
    unittest.main()
