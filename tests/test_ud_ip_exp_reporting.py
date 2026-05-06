from __future__ import annotations

from decimal import Decimal
import unittest

from project.models import WorkflowId
from project.rules import load_rule_pack
from project.workbook import WorkbookHeader, WorkbookRow, WorkbookSnapshot
from project.workflows.snapshot import SourceEmailRecord, build_email_snapshot
from project.workflows.ud_ip_exp import (
    DocumentExtractionField,
    UDAllocationCandidate,
    UDAllocationResult,
    UDDocumentPayload,
    build_ud_selection_report,
)
from project.workflows.ud_ip_exp.validation import assemble_ud_validation


class UDIPEXPReportingTests(unittest.TestCase):
    def test_build_ud_selection_report_includes_required_selected_candidate_evidence(self) -> None:
        allocation = UDAllocationResult(
            required_quantity="YDS:3000",
            quantity_unit="MULTI",
            candidate_count=1,
            candidates=[
                UDAllocationCandidate(
                    candidate_id="11-19",
                    row_indexes=[11, 19],
                    matched_quantities=["YDS:3000"],
                    quantity_sum="YDS:3000",
                    ignored_excess_quantity="YDS:0",
                    score_keys={
                        "row_index_key": [11, 19],
                        "amendment_recency_key": [("2026-01-02", 1), ("2026-02-10", 2)],
                        "blank_field_priority_key": {
                            "blank_target_count_desc": -2,
                            "nonblank_optional_count_asc": 0,
                        },
                        "stable_candidate_id_key": "11-19",
                        "lc_sc_value": "3000",
                        "workbook_value_sum": "3000",
                        "ud_quantity_by_unit": "YDS:3000",
                        "workbook_quantity_by_unit": "YDS:3000",
                    },
                    prewrite_blank_targets_count=2,
                    prewrite_nonblank_optional_count=0,
                    selected=True,
                )
            ],
            final_decision="selected",
            final_decision_reason="selected_structured_lc_value_and_quantity",
            selected_candidate_id="11-19",
        )

        report = build_ud_selection_report(allocation)

        self.assertEqual(report["required_quantity"], "YDS:3000")
        self.assertEqual(report["quantity_unit"], "MULTI")
        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(report["reported_candidate_count"], 1)
        self.assertFalse(report["candidates_truncated"])
        self.assertEqual(report["omitted_candidate_count"], 0)
        self.assertEqual(report["final_decision"], "selected")
        self.assertEqual(report["selected_candidate_id"], "11-19")
        self.assertEqual(report["candidates"][0]["candidate_id"], "11-19")
        self.assertEqual(report["candidates"][0]["row_indexes"], [11, 19])
        self.assertEqual(report["candidates"][0]["matched_quantities"], ["YDS:3000"])
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
                "lc_sc_value": "3000",
                "workbook_value_sum": "3000",
                "ud_quantity_by_unit": "YDS:3000",
                "workbook_quantity_by_unit": "YDS:3000",
            },
        )

    def test_build_ud_selection_report_maps_full_tie_to_hard_block_tie(self) -> None:
        allocation = UDAllocationResult(
            required_quantity="YDS:1000",
            quantity_unit="MULTI",
            candidate_count=2,
            candidates=[
                UDAllocationCandidate(
                    candidate_id="11",
                    row_indexes=[11],
                    matched_quantities=["YDS:1000"],
                    quantity_sum="YDS:1000",
                    ignored_excess_quantity="YDS:0",
                    score_keys={
                        "row_index_key": [11],
                        "amendment_recency_key": [],
                        "blank_field_priority_key": {
                            "blank_target_count_desc": -1,
                            "nonblank_optional_count_asc": 0,
                        },
                        "stable_candidate_id_key": "11",
                    },
                    prewrite_blank_targets_count=1,
                    prewrite_nonblank_optional_count=0,
                    selected=False,
                    rejection_reason="tied_after_full_tiebreak",
                ),
                UDAllocationCandidate(
                    candidate_id="11-dup",
                    row_indexes=[11],
                    matched_quantities=["YDS:1000"],
                    quantity_sum="YDS:1000",
                    ignored_excess_quantity="YDS:0",
                    score_keys={
                        "row_index_key": [11],
                        "amendment_recency_key": [],
                        "blank_field_priority_key": {
                            "blank_target_count_desc": -1,
                            "nonblank_optional_count_asc": 0,
                        },
                        "stable_candidate_id_key": "11-dup",
                    },
                    prewrite_blank_targets_count=1,
                    prewrite_nonblank_optional_count=0,
                    selected=False,
                    rejection_reason="tied_after_full_tiebreak",
                ),
            ],
            final_decision="hard_block",
            final_decision_reason="ud_candidate_tie_after_full_tiebreak",
            discrepancy_code="ud_candidate_tie_after_full_tiebreak",
        )

        report = build_ud_selection_report(allocation)

        self.assertEqual(report["final_decision"], "hard_block_tie")
        self.assertEqual(report["discrepancy_code"], "ud_candidate_tie_after_full_tiebreak")
        self.assertEqual([candidate["selected"] for candidate in report["candidates"]], [False, False])
        self.assertEqual(
            {candidate["rejection_reason"] for candidate in report["candidates"]},
            {"tied_after_full_tiebreak"},
        )

    def test_build_ud_selection_report_marks_truncated_candidate_sets(self) -> None:
        allocation = UDAllocationResult(
            required_quantity="700",
            quantity_unit="MULTI",
            candidate_count=350,
            candidates=[
                UDAllocationCandidate(
                    candidate_id="11-12",
                    row_indexes=[11, 12],
                    matched_quantities=["YDS:700"],
                    quantity_sum="YDS:700",
                    ignored_excess_quantity="YDS:0",
                    score_keys={
                        "row_index_key": [11, 12],
                        "amendment_recency_key": [("0001-01-01", 0), ("0001-01-01", 0)],
                        "blank_field_priority_key": {
                            "blank_target_count_desc": -2,
                            "nonblank_optional_count_asc": 0,
                        },
                        "stable_candidate_id_key": "11-12",
                    },
                    prewrite_blank_targets_count=2,
                    prewrite_nonblank_optional_count=0,
                    selected=True,
                ),
                UDAllocationCandidate(
                    candidate_id="13-14",
                    row_indexes=[13, 14],
                    matched_quantities=["YDS:700"],
                    quantity_sum="YDS:700",
                    ignored_excess_quantity="YDS:0",
                    score_keys={
                        "row_index_key": [13, 14],
                        "amendment_recency_key": [("0001-01-01", 0), ("0001-01-01", 0)],
                        "blank_field_priority_key": {
                            "blank_target_count_desc": -2,
                            "nonblank_optional_count_asc": 0,
                        },
                        "stable_candidate_id_key": "13-14",
                    },
                    prewrite_blank_targets_count=2,
                    prewrite_nonblank_optional_count=0,
                    selected=False,
                    rejection_reason="lower_priority_score",
                ),
            ],
            final_decision="selected",
            final_decision_reason="selected_structured_lc_value_and_quantity",
            selected_candidate_id="11-12",
            candidates_truncated=True,
        )

        report = build_ud_selection_report(allocation)

        self.assertEqual(report["candidate_count"], 350)
        self.assertEqual(report["reported_candidate_count"], 2)
        self.assertTrue(report["candidates_truncated"])
        self.assertEqual(report["omitted_candidate_count"], 348)

    def test_assemble_ud_validation_exposes_ud_selection_report(self) -> None:
        result = assemble_ud_validation(
            run_id="run-1",
            mail=_mail(),
            rule_pack=load_rule_pack(WorkflowId.UD_IP_EXP),
            ud_document=_ud_document(),
            workbook_snapshot=_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000 YDS", 3: "", 4: "", 5: "", 6: "1000", 7: "", 8: ""}),
                ]
            ),
            export_payload=_export_payload(),
        )

        self.assertIsNotNone(result.ud_selection)
        self.assertEqual(result.ud_selection["final_decision"], "selected")
        self.assertEqual(result.ud_selection["selected_candidate_id"], "11")


def _ud_document() -> UDDocumentPayload:
    return UDDocumentPayload(
        document_number=DocumentExtractionField("BGMEA/DHK/UD/2026/5483/003"),
        document_date=DocumentExtractionField("2026-04-01"),
        lc_sc_number=DocumentExtractionField("LC-0043"),
        lc_sc_date=DocumentExtractionField("2026-01-10"),
        lc_sc_value=DocumentExtractionField("1000"),
        quantity_by_unit={"YDS": Decimal("1000")},
    )


def _export_payload():
    from project.erp import ERPRegisterRow
    from project.workflows.export_lc_sc.payloads import ExportFileNumberMatch, ExportMailPayload

    row = ERPRegisterRow(
        file_number="P/26/0042",
        lc_sc_number="LC-0043",
        buyer_name="ANANTA GARMENTS LTD",
        lc_sc_date="2026-01-10",
        source_row_index=1,
    )
    return ExportMailPayload(
        parsed_subject=None,
        file_numbers=["P/26/0042"],
        erp_matches=[ExportFileNumberMatch(file_number="P/26/0042", canonical_row=row, matched_rows=[row])],
        verified_family=row.family,
        attachments_in_order=[],
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
            WorkbookHeader(column_index=6, text="Amount"),
            WorkbookHeader(column_index=7, text="UD & IP Date"),
            WorkbookHeader(column_index=8, text="UD Recv. Date"),
        ],
        rows=rows,
    )


if __name__ == "__main__":
    unittest.main()
