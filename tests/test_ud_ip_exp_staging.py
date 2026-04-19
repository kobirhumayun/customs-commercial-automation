from __future__ import annotations

from decimal import Decimal
import unittest

from project.workbook import WorkbookHeader, WorkbookRow, WorkbookSnapshot
from project.workflows.ud_ip_exp import (
    DocumentExtractionField,
    EXPDocumentPayload,
    IPDocumentPayload,
    UDCandidateRow,
    UDDocumentPayload,
    allocate_ud_rows,
    stage_ip_exp_shared_column_operations,
    stage_ud_shared_column_operations,
)


class UDIPEXPStagingTests(unittest.TestCase):
    def test_stage_ud_shared_column_operations_writes_selected_blank_rows(self) -> None:
        snapshot = _snapshot(
            rows=[
                WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000", 3: "", 4: "", 5: ""}),
                WorkbookRow(row_index=19, values={1: "LC-0043", 2: "2000", 3: "", 4: "", 5: ""}),
            ]
        )
        allocation = allocate_ud_rows(
            required_quantity=Decimal("3000"),
            quantity_unit="YDS",
            candidate_rows=[
                UDCandidateRow(row_index=11, lc_sc_number="LC-0043", quantity=Decimal("1000"), quantity_unit="YDS"),
                UDCandidateRow(row_index=19, lc_sc_number="LC-0043", quantity=Decimal("2000"), quantity_unit="YDS"),
            ],
        )

        result = stage_ud_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            ud_document=_ud_document("UD-LC-0043-ANANTA"),
            allocation_result=allocation,
            workbook_snapshot=snapshot,
        )

        self.assertEqual(result.discrepancies, [])
        self.assertEqual([operation.row_index for operation in result.staged_write_operations], [11, 19])
        self.assertEqual(
            [operation.expected_post_write_value for operation in result.staged_write_operations],
            ["UD-LC-0043-ANANTA", "UD-LC-0043-ANANTA"],
        )
        self.assertTrue(
            all(
                operation.row_eligibility_checks == ["target_cell_blank_by_construction"]
                for operation in result.staged_write_operations
            )
        )

    def test_stage_ud_shared_column_operations_blocks_unselected_allocation(self) -> None:
        allocation = allocate_ud_rows(
            required_quantity=Decimal("1000"),
            quantity_unit="YDS",
            candidate_rows=[
                UDCandidateRow(row_index=11, lc_sc_number="LC-0043", quantity=Decimal("951"), quantity_unit="YDS"),
            ],
        )

        result = stage_ud_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            ud_document=_ud_document("UD-LC-0043-ANANTA"),
            allocation_result=allocation,
            workbook_snapshot=_snapshot(rows=[]),
        )

        self.assertEqual(result.staged_write_operations, [])
        self.assertEqual(result.discrepancies[0].code, "ud_allocation_unresolved")
        self.assertEqual(result.discrepancies[0].details["final_decision_reason"], "quantity_excess_below_threshold")

    def test_stage_ud_shared_column_operations_preserves_tie_code(self) -> None:
        allocation = allocate_ud_rows(
            required_quantity=Decimal("1000"),
            quantity_unit="YDS",
            candidate_rows=[
                UDCandidateRow(row_index=11, lc_sc_number="LC-0043", quantity=Decimal("1000"), quantity_unit="YDS"),
                UDCandidateRow(row_index=11, lc_sc_number="LC-0043", quantity=Decimal("1000"), quantity_unit="YDS"),
            ],
        )

        result = stage_ud_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            ud_document=_ud_document("UD-LC-0043-ANANTA"),
            allocation_result=allocation,
            workbook_snapshot=_snapshot(rows=[]),
        )

        self.assertEqual(result.staged_write_operations, [])
        self.assertEqual(result.discrepancies[0].code, "ud_candidate_tie_after_full_tiebreak")

    def test_stage_ud_shared_column_operations_blocks_nonblank_shared_column_until_policy_confirmed(self) -> None:
        snapshot = _snapshot(
            rows=[
                WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000", 3: "UD-OLD", 4: "", 5: ""}),
            ]
        )
        allocation = allocate_ud_rows(
            required_quantity=Decimal("1000"),
            quantity_unit="YDS",
            candidate_rows=[
                UDCandidateRow(row_index=11, lc_sc_number="LC-0043", quantity=Decimal("1000"), quantity_unit="YDS"),
            ],
        )

        result = stage_ud_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            ud_document=_ud_document("UD-LC-0043-ANANTA"),
            allocation_result=allocation,
            workbook_snapshot=snapshot,
        )

        self.assertEqual(result.staged_write_operations, [])
        self.assertEqual(result.discrepancies[0].code, "ud_shared_column_nonblank_policy_unresolved")
        self.assertEqual(result.discrepancies[0].details["target_rows"][0]["observed_value"], "UD-OLD")

    def test_stage_ud_shared_column_operations_blocks_invalid_header_mapping(self) -> None:
        snapshot = WorkbookSnapshot(
            sheet_name="Sheet1",
            headers=[
                WorkbookHeader(column_index=1, text="L/C & S/C No."),
                WorkbookHeader(column_index=2, text="Quantity of Fabrics (Yds/Mtr)"),
            ],
            rows=[],
        )
        allocation = allocate_ud_rows(
            required_quantity=Decimal("1000"),
            quantity_unit="YDS",
            candidate_rows=[
                UDCandidateRow(row_index=11, lc_sc_number="LC-0043", quantity=Decimal("1000"), quantity_unit="YDS"),
            ],
        )

        result = stage_ud_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            ud_document=_ud_document("UD-LC-0043-ANANTA"),
            allocation_result=allocation,
            workbook_snapshot=snapshot,
        )

        self.assertEqual(result.staged_write_operations, [])
        self.assertEqual(result.discrepancies[0].code, "workbook_header_mapping_invalid")

    def test_stage_ip_exp_shared_column_operations_hard_blocks_until_policy_confirmed(self) -> None:
        result = stage_ip_exp_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            documents=[
                IPDocumentPayload(
                    document_number=DocumentExtractionField("IP-002"),
                    document_date=DocumentExtractionField("2026-04-03"),
                    lc_sc_number=DocumentExtractionField("LC-0043"),
                ),
                EXPDocumentPayload(
                    document_number=DocumentExtractionField("EXP-001"),
                    document_date=DocumentExtractionField("2026-04-02"),
                    lc_sc_number=DocumentExtractionField("LC-0043"),
                ),
            ],
            workbook_snapshot=_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000", 3: "", 4: "", 5: ""}),
                ]
            ),
            target_row_indexes=[11],
        )

        self.assertEqual(result.staged_write_operations, [])
        self.assertEqual(result.discrepancies[0].code, "ip_exp_policy_unresolved")
        self.assertEqual(
            result.discrepancies[0].details["proposed_shared_column_value"],
            "EXP: EXP-001\nIP: IP-002",
        )
        self.assertEqual(result.discrepancies[0].details["target_row_indexes"], [11])
        self.assertIn(
            "date column mapping",
            "\n".join(result.discrepancies[0].details["unresolved_policies"]),
        )

    def test_stage_ip_exp_shared_column_operations_noops_when_no_ip_exp_documents(self) -> None:
        result = stage_ip_exp_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            documents=[_ud_document("UD-LC-0043-ANANTA")],
            workbook_snapshot=_snapshot(rows=[]),
        )

        self.assertEqual(result.staged_write_operations, [])
        self.assertEqual(result.discrepancies, [])
        self.assertEqual(
            result.decision_reasons,
            ["No IP/EXP document payloads supplied; no IP/EXP staging needed."],
        )


def _ud_document(document_number: str) -> UDDocumentPayload:
    return UDDocumentPayload(
        document_number=DocumentExtractionField(document_number),
        document_date=DocumentExtractionField("2026-04-01"),
        lc_sc_number=DocumentExtractionField("LC-0043"),
    )


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
