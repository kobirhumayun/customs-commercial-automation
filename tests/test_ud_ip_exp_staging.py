from __future__ import annotations

from decimal import Decimal
import unittest

from project.workbook import WorkbookHeader, WorkbookRow, WorkbookSnapshot
from project.workflows.ud_ip_exp import (
    DocumentExtractionField,
    EXPDocumentPayload,
    IPDocumentPayload,
    UDAllocationCandidate,
    UDAllocationResult,
    UDDocumentPayload,
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
        allocation = _selected_allocation_result([11, 19])

        result = stage_ud_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            ud_document=_ud_document("BGMEA/DHK/UD/2026/5483/003"),
            allocation_result=allocation,
            workbook_snapshot=snapshot,
        )

        self.assertEqual(result.discrepancies, [])
        self.assertEqual([operation.row_index for operation in result.staged_write_operations], [11, 19])
        self.assertEqual(
            [operation.expected_post_write_value for operation in result.staged_write_operations],
            ["BGMEA/DHK/UD/2026/5483/003", "BGMEA/DHK/UD/2026/5483/003"],
        )
        self.assertTrue(
            all(
                operation.row_eligibility_checks == ["target_cell_blank_by_construction"]
                for operation in result.staged_write_operations
            )
        )

    def test_stage_structured_ud_operations_writes_ud_and_receive_dates(self) -> None:
        snapshot = _structured_snapshot(
            rows=[
                WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000", 3: "", 4: "", 5: "", 6: "1000", 7: "", 8: ""}),
            ]
        )
        allocation = _selected_allocation_result([11])
        ud_document = UDDocumentPayload(
            document_number=DocumentExtractionField("BGMEA/DHK/UD/2026/5483/003"),
            document_date=DocumentExtractionField("2026-03-31"),
            lc_sc_number=DocumentExtractionField("LC-0043"),
            lc_sc_value=DocumentExtractionField("1000"),
            quantity_by_unit={"YDS": Decimal("1050")},
        )

        result = stage_ud_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            ud_document=ud_document,
            allocation_result=allocation,
            workbook_snapshot=snapshot,
            ud_receive_date="2026-04-22",
        )

        self.assertEqual(result.discrepancies, [])
        self.assertEqual(
            [(operation.column_key, operation.expected_post_write_value) for operation in result.staged_write_operations],
            [
                ("ud_ip_shared", "BGMEA/DHK/UD/2026/5483/003"),
                ("ud_ip_date", "31/03/2026"),
                ("ud_recv_date", "22/04/2026"),
            ],
        )
        self.assertEqual(
            [(operation.column_key, operation.number_format) for operation in result.staged_write_operations],
            [
                ("ud_ip_shared", None),
                ("ud_ip_date", "dd/mm/yyyy"),
                ("ud_recv_date", "dd/mm/yyyy"),
            ],
        )

    def test_stage_structured_ud_operations_blocks_existing_date_values_even_when_shared_cell_blank(self) -> None:
        snapshot = _structured_snapshot(
            rows=[
                WorkbookRow(
                    row_index=11,
                    values={1: "LC-0043", 2: "1000", 3: "", 4: "", 5: "", 6: "1000", 7: "31/03/2026", 8: "27/04/2026"},
                ),
            ]
        )
        allocation = _selected_allocation_result([11])
        ud_document = UDDocumentPayload(
            document_number=DocumentExtractionField("BGMEA/DHK/UD/2026/5483/003"),
            document_date=DocumentExtractionField("2026-03-31"),
            lc_sc_number=DocumentExtractionField("LC-0043"),
            lc_sc_value=DocumentExtractionField("1000"),
            quantity_by_unit={"YDS": Decimal("1000")},
        )

        result = stage_ud_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            ud_document=ud_document,
            allocation_result=allocation,
            workbook_snapshot=snapshot,
            ud_receive_date="2026-04-28",
        )

        self.assertEqual(result.staged_write_operations, [])
        self.assertEqual(result.discrepancies[0].code, "ud_shared_column_nonblank_policy_unresolved")
        self.assertEqual(
            [item["column_key"] for item in result.discrepancies[0].details["target_rows"]],
            ["ud_ip_date", "ud_recv_date"],
        )

    def test_stage_structured_ud_operations_noops_when_already_recorded(self) -> None:
        snapshot = _structured_snapshot(
            rows=[
                WorkbookRow(
                    row_index=11,
                    values={1: "LC-0043", 2: "1000", 3: "BGMEA/DHK/UD/2026/5483/003", 4: "", 5: "", 6: "1000", 7: "31/03/2026", 8: ""},
                ),
            ]
        )
        allocation = _already_recorded_allocation_result([11])
        ud_document = UDDocumentPayload(
            document_number=DocumentExtractionField("BGMEA/DHK/UD/2026/5483/003"),
            document_date=DocumentExtractionField("2026-03-31"),
            lc_sc_number=DocumentExtractionField("LC-0043"),
            lc_sc_value=DocumentExtractionField("1000"),
            quantity_by_unit={"YDS": Decimal("1000")},
        )

        result = stage_ud_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            ud_document=ud_document,
            allocation_result=allocation,
            workbook_snapshot=snapshot,
            ud_receive_date="2026-04-22",
        )

        self.assertEqual(result.staged_write_operations, [])
        self.assertEqual(result.discrepancies, [])
        self.assertEqual(
            result.decision_reasons,
            [
                "Skipped UD shared-column write for BGMEA/DHK/UD/2026/5483/003 because it is already recorded in the workbook."
            ],
        )

    def test_stage_ud_shared_column_operations_blocks_unselected_allocation(self) -> None:
        allocation = _hard_block_allocation_result(
            reason="quantity_excess_below_threshold",
        )

        result = stage_ud_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            ud_document=_ud_document("BGMEA/DHK/UD/2026/5483/003"),
            allocation_result=allocation,
            workbook_snapshot=_snapshot(rows=[]),
        )

        self.assertEqual(result.staged_write_operations, [])
        self.assertEqual(result.discrepancies[0].code, "ud_allocation_unresolved")
        self.assertEqual(result.discrepancies[0].details["final_decision_reason"], "quantity_excess_below_threshold")

    def test_stage_ud_shared_column_operations_preserves_tie_code(self) -> None:
        allocation = _hard_block_allocation_result(
            reason="ud_candidate_tie_after_full_tiebreak",
            code="ud_candidate_tie_after_full_tiebreak",
        )

        result = stage_ud_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            ud_document=_ud_document("BGMEA/DHK/UD/2026/5483/003"),
            allocation_result=allocation,
            workbook_snapshot=_snapshot(rows=[]),
        )

        self.assertEqual(result.staged_write_operations, [])
        self.assertEqual(result.discrepancies[0].code, "ud_candidate_tie_after_full_tiebreak")

    def test_stage_ud_shared_column_operations_blocks_conflicting_shared_column_with_row_conflict(self) -> None:
        snapshot = _snapshot(
            rows=[
                WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000", 3: "UD-OLD", 4: "", 5: ""}),
            ]
        )
        allocation = _selected_allocation_result([11])

        result = stage_ud_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            ud_document=_ud_document("BGMEA/DHK/UD/2026/5483/003"),
            allocation_result=allocation,
            workbook_snapshot=snapshot,
        )

        self.assertEqual(result.staged_write_operations, [])
        self.assertEqual(result.discrepancies[0].code, "ud_target_row_conflict")
        self.assertEqual(result.discrepancies[0].details["target_rows"][0]["observed_value"], "UD-OLD")

    def test_stage_ud_shared_column_operations_blocks_filename_style_ud_number(self) -> None:
        allocation = _selected_allocation_result([11])

        result = stage_ud_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            ud_document=_ud_document("UD-LC-0127-COTTONEX FASHIONS LTD"),
            allocation_result=allocation,
            workbook_snapshot=_snapshot(rows=[WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000", 3: "", 4: "", 5: ""})]),
        )

        self.assertEqual(result.staged_write_operations, [])
        self.assertEqual(result.discrepancies[0].code, "ud_required_field_invalid")
        self.assertEqual(result.discrepancies[0].details["invalid_fields"], ["document_number"])

    def test_stage_structured_ud_operations_blocks_unparseable_dates(self) -> None:
        snapshot = _structured_snapshot(
            rows=[
                WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000", 3: "", 4: "", 5: "", 6: "1000", 7: "", 8: ""}),
            ]
        )
        allocation = _selected_allocation_result([11])
        ud_document = UDDocumentPayload(
            document_number=DocumentExtractionField("BGMEA/DHK/UD/2026/5483/003"),
            document_date=DocumentExtractionField("2026-99-99"),
            lc_sc_number=DocumentExtractionField("LC-0043"),
            lc_sc_value=DocumentExtractionField("1000"),
            quantity_by_unit={"YDS": Decimal("1050")},
        )

        result = stage_ud_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            ud_document=ud_document,
            allocation_result=allocation,
            workbook_snapshot=snapshot,
            ud_receive_date="not a date",
        )

        self.assertEqual(result.staged_write_operations, [])
        self.assertEqual(result.discrepancies[0].code, "ud_required_field_invalid")
        self.assertEqual(
            result.discrepancies[0].details["invalid_fields"],
            ["document_date", "ud_receive_date"],
        )

    def test_stage_ud_shared_column_operations_blocks_invalid_header_mapping(self) -> None:
        snapshot = WorkbookSnapshot(
            sheet_name="Sheet1",
            headers=[
                WorkbookHeader(column_index=1, text="L/C & S/C No."),
                WorkbookHeader(column_index=2, text="Quantity of Fabrics (Yds/Mtr)"),
            ],
            rows=[],
        )
        allocation = _selected_allocation_result([11])

        result = stage_ud_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            ud_document=_ud_document("BGMEA/DHK/UD/2026/5483/003"),
            allocation_result=allocation,
            workbook_snapshot=snapshot,
        )

        self.assertEqual(result.staged_write_operations, [])
        self.assertEqual(result.discrepancies[0].code, "workbook_header_mapping_invalid")

    def test_stage_ip_exp_shared_column_operations_stages_family_wide_rows(self) -> None:
        result = stage_ip_exp_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            documents=[
                IPDocumentPayload(
                    document_number=DocumentExtractionField("IP-002"),
                    document_date=DocumentExtractionField("2026-04-02"),
                    lc_sc_number=DocumentExtractionField("LC-0043"),
                ),
                EXPDocumentPayload(
                    document_number=DocumentExtractionField("EXP-001"),
                    document_date=DocumentExtractionField("2026-04-02"),
                    lc_sc_number=DocumentExtractionField("LC-0043"),
                ),
            ],
            workbook_snapshot=_structured_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000", 3: "", 4: "", 5: "", 6: "1000", 7: "", 8: ""}),
                    WorkbookRow(row_index=12, values={1: "LC-0043", 2: "500", 3: "", 4: "", 5: "", 6: "500", 7: "", 8: ""}),
                ]
            ),
            target_row_indexes=[11, 12],
            ip_exp_receive_date="2026-04-22",
        )

        self.assertEqual(result.discrepancies, [])
        self.assertEqual(
            [
                (operation.row_index, operation.column_key, operation.expected_post_write_value)
                for operation in result.staged_write_operations
            ],
            [
                (11, "ud_ip_shared", "EXP: EXP-001\nIP: IP-002"),
                (11, "ud_ip_date", "02/04/2026"),
                (11, "ud_recv_date", "22/04/2026"),
                (12, "ud_ip_shared", "EXP: EXP-001\nIP: IP-002"),
                (12, "ud_ip_date", "02/04/2026"),
                (12, "ud_recv_date", "22/04/2026"),
            ],
        )

    def test_stage_ip_exp_shared_column_operations_noops_when_family_already_recorded(self) -> None:
        result = stage_ip_exp_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            documents=[
                EXPDocumentPayload(
                    document_number=DocumentExtractionField("EXP-001"),
                    document_date=DocumentExtractionField("2026-04-02"),
                    lc_sc_number=DocumentExtractionField("LC-0043"),
                )
            ],
            workbook_snapshot=_structured_snapshot(
                rows=[
                    WorkbookRow(
                        row_index=11,
                        values={1: "LC-0043", 2: "1000", 3: "EXP: EXP-001", 4: "", 5: "", 6: "1000", 7: "02/04/2026", 8: "20/04/2026"},
                    ),
                ]
            ),
            target_row_indexes=[11],
            ip_exp_receive_date="2026-04-22",
        )

        self.assertEqual(result.staged_write_operations, [])
        self.assertEqual(result.discrepancies, [])
        self.assertEqual(
            result.decision_reasons,
            ["Skipped IP/EXP family-wide write because the requested shared-column value is already recorded in the workbook."],
        )

    def test_stage_ip_exp_shared_column_operations_noops_when_no_ip_exp_documents(self) -> None:
        result = stage_ip_exp_shared_column_operations(
            run_id="run-1",
            mail_id="mail-1",
            documents=[_ud_document("BGMEA/DHK/UD/2026/5483/003")],
            workbook_snapshot=_snapshot(rows=[]),
        )

        self.assertEqual(result.staged_write_operations, [])
        self.assertEqual(result.discrepancies, [])
        self.assertEqual(
            result.decision_reasons,
            ["No IP/EXP document payloads supplied; no IP/EXP staging needed."],
        )


def _selected_allocation_result(row_indexes: list[int]) -> UDAllocationResult:
    candidates = [
        UDAllocationCandidate(
            candidate_id="-".join(str(row_index) for row_index in row_indexes),
            row_indexes=list(row_indexes),
            matched_quantities=[],
            quantity_sum="",
            ignored_excess_quantity="",
            score_keys={
                "row_index_key": list(row_indexes),
                "amendment_recency_key": [],
                "blank_field_priority_key": {
                    "blank_target_count_desc": -len(row_indexes),
                    "nonblank_optional_count_asc": 0,
                },
                "stable_candidate_id_key": "-".join(str(row_index) for row_index in row_indexes),
            },
            prewrite_blank_targets_count=len(row_indexes),
            prewrite_nonblank_optional_count=0,
            selected=True,
        )
    ]
    return UDAllocationResult(
        required_quantity="",
        quantity_unit="MULTI",
        candidate_count=1,
        candidates=candidates,
        final_decision="selected",
        final_decision_reason="selected_structured_lc_value_and_quantity",
        selected_candidate_id=candidates[0].candidate_id,
    )


def _already_recorded_allocation_result(row_indexes: list[int]) -> UDAllocationResult:
    selected = _selected_allocation_result(row_indexes)
    return UDAllocationResult(
        required_quantity=selected.required_quantity,
        quantity_unit=selected.quantity_unit,
        candidate_count=selected.candidate_count,
        candidates=selected.candidates,
        final_decision="already_recorded",
        final_decision_reason="ud_already_recorded",
        selected_candidate_id=selected.selected_candidate_id,
    )


def _hard_block_allocation_result(*, reason: str, code: str | None = None) -> UDAllocationResult:
    return UDAllocationResult(
        required_quantity="",
        quantity_unit="MULTI",
        candidate_count=0,
        candidates=[],
        final_decision="hard_block",
        final_decision_reason=reason,
        discrepancy_code=code,
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


def _structured_snapshot(*, rows: list[WorkbookRow]) -> WorkbookSnapshot:
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
