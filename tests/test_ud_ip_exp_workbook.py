from __future__ import annotations

import unittest

from project.models import WorkflowId, WriteOperation
from project.workbook import (
    WorkbookHeader,
    WorkbookRow,
    WorkbookSnapshot,
    prevalidate_staged_write_plan,
    resolve_ud_ip_exp_header_mapping,
)


class UDIPEXPWorkbookTests(unittest.TestCase):
    def test_resolve_ud_ip_exp_header_mapping_resolves_owned_shared_column(self) -> None:
        mapping = resolve_ud_ip_exp_header_mapping(_snapshot(rows=[]))

        self.assertIsNotNone(mapping)
        self.assertEqual(mapping["ud_ip_shared"], 3)
        self.assertEqual(mapping["lc_sc_no"], 1)

    def test_resolve_ud_ip_exp_header_mapping_rejects_ambiguous_shared_column(self) -> None:
        snapshot = WorkbookSnapshot(
            sheet_name="Sheet1",
            headers=[
                WorkbookHeader(column_index=1, text="L/C & S/C No."),
                WorkbookHeader(column_index=2, text="Quantity of Fabrics (Yds/Mtr)"),
                WorkbookHeader(column_index=3, text="UD No. & IP No."),
                WorkbookHeader(column_index=4, text="UD No. & IP No."),
                WorkbookHeader(column_index=5, text="L/C Amnd No."),
                WorkbookHeader(column_index=6, text="L/C Amnd Date"),
            ],
            rows=[],
        )

        self.assertIsNone(resolve_ud_ip_exp_header_mapping(snapshot))

    def test_prevalidate_ud_ip_exp_target_matches_expected_multiline_pre_write(self) -> None:
        snapshot = _snapshot(
            rows=[
                WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000", 3: "UD-OLD", 4: "", 5: ""}),
            ]
        )
        staged_write_plan = [
            WriteOperation(
                write_operation_id="op-1",
                run_id="run-1",
                mail_id="mail-1",
                operation_index_within_mail=0,
                sheet_name="Sheet1",
                row_index=11,
                column_key="ud_ip_shared",
                expected_pre_write_value="UD-OLD",
                expected_post_write_value="UD-OLD\nUD-NEW",
                row_eligibility_checks=["target_cell_matches_expected_pre_write"],
            )
        ]

        result = prevalidate_staged_write_plan(
            workflow_id=WorkflowId.UD_IP_EXP,
            run_id="run-1",
            workbook_snapshot=snapshot,
            staged_write_plan=staged_write_plan,
        )

        self.assertEqual(result.summary.status, "passed")
        self.assertEqual(result.probes[0].column_index, 3)
        self.assertEqual(result.probes[0].classification, "matches_pre_write")

    def test_prevalidate_ud_ip_exp_target_blocks_unexpected_existing_value(self) -> None:
        snapshot = _snapshot(
            rows=[
                WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000", 3: "UD-OTHER", 4: "", 5: ""}),
            ]
        )
        staged_write_plan = [
            WriteOperation(
                write_operation_id="op-1",
                run_id="run-1",
                mail_id="mail-1",
                operation_index_within_mail=0,
                sheet_name="Sheet1",
                row_index=11,
                column_key="ud_ip_shared",
                expected_pre_write_value="UD-OLD",
                expected_post_write_value="UD-OLD\nUD-NEW",
                row_eligibility_checks=["target_cell_matches_expected_pre_write"],
            )
        ]

        result = prevalidate_staged_write_plan(
            workflow_id=WorkflowId.UD_IP_EXP,
            run_id="run-1",
            workbook_snapshot=snapshot,
            staged_write_plan=staged_write_plan,
        )

        self.assertEqual(result.summary.status, "hard_blocked")
        self.assertEqual(result.probes[0].classification, "mismatch_unknown")
        self.assertEqual(result.discrepancy_reports[0].code, "workbook_target_prevalidation_failed")


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
