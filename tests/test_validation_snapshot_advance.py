from __future__ import annotations

import unittest

from project.models import WorkflowId, WriteOperation
from project.workbook import WorkbookHeader, WorkbookRow, WorkbookSnapshot, resolve_ud_ip_exp_header_mapping
from project.workflows.registry import get_workflow_descriptor
from project.workflows.ud_ip_exp.matching import allocate_structured_ud_rows
from project.workflows.validation import _advance_workbook_snapshot_for_staged_writes


class ValidationSnapshotAdvanceTests(unittest.TestCase):
    def test_ud_ip_exp_snapshot_advance_preserves_quantity_number_formats(self) -> None:
        snapshot = WorkbookSnapshot(
            sheet_name="UP",
            headers=[
                WorkbookHeader(4, "L/C & S/C No."),
                WorkbookHeader(6, "Amount"),
                WorkbookHeader(9, "Quantity of Fabrics (Yds/Mtr)"),
                WorkbookHeader(10, "L/C Amnd No."),
                WorkbookHeader(11, "L/C Amnd Date"),
                WorkbookHeader(17, "UD No. & IP No."),
                WorkbookHeader(18, "UD & IP Date"),
                WorkbookHeader(19, "UD Recv. Date"),
            ],
            rows=[
                WorkbookRow(
                    row_index=372,
                    values={4: "1558260300082", 6: "17270", 9: "7500", 17: ""},
                    number_formats={9: '#,###.00 "Mtr"'},
                ),
                WorkbookRow(
                    row_index=475,
                    values={4: "1550260400113", 6: "100882.4", 9: "26548", 17: ""},
                    number_formats={9: '#,###.00 "Mtr"'},
                ),
            ],
        )

        advanced = _advance_workbook_snapshot_for_staged_writes(
            descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
            workbook_snapshot=snapshot,
            staged_write_operations=[
                WriteOperation(
                    write_operation_id="write-1",
                    run_id="run-1",
                    mail_id="mail-1",
                    operation_index_within_mail=0,
                    sheet_name="UP",
                    row_index=372,
                    column_key="ud_ip_shared",
                    expected_pre_write_value=None,
                    expected_post_write_value="BGMEA/DHK/AM/2026/4148/017-018",
                    row_eligibility_checks=["target_cell_blank_by_construction"],
                )
            ],
        )

        mapping = resolve_ud_ip_exp_header_mapping(advanced)
        row_475 = next(row for row in advanced.rows if row.row_index == 475)
        self.assertEqual(row_475.number_formats[9], '#,###.00 "Mtr"')

        result = allocate_structured_ud_rows(
            workbook_snapshot=advanced,
            lc_sc_number="1550260400113",
            lc_sc_value="100882.4",
            quantity_by_unit={"MTR": "26548"},
            header_mapping=mapping,
        )

        self.assertEqual(result.final_decision, "selected")
        self.assertEqual(result.selected_candidate_id, "475")
        self.assertEqual(result.candidates[0].score_keys["workbook_quantity_by_unit"], "MTR:26548")


if __name__ == "__main__":
    unittest.main()

