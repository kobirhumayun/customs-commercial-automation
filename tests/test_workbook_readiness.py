from __future__ import annotations

import unittest

from project.models import WorkflowId, WriteOperation
from project.workbook import WorkbookHeader, WorkbookRow, WorkbookSnapshot
from project.workflows.workbook_readiness import summarize_workbook_readiness


class WorkbookReadinessTests(unittest.TestCase):
    def test_summarize_workbook_readiness_resolves_real_export_master_headers(self) -> None:
        snapshot = WorkbookSnapshot(
            sheet_name="UP Issuing Status # 2026-2027",
            headers=[
                WorkbookHeader(column_index=1, text="SL.No."),
                WorkbookHeader(column_index=2, text="Name of Buyers"),
                WorkbookHeader(column_index=3, text="L/C Issuing Bank"),
                WorkbookHeader(column_index=4, text="L/C & S/C No."),
                WorkbookHeader(column_index=5, text="LC Issue Date"),
                WorkbookHeader(column_index=6, text="Amount"),
                WorkbookHeader(column_index=7, text="Shipment Date"),
                WorkbookHeader(column_index=8, text="Expiry Date"),
                WorkbookHeader(column_index=9, text="Quantity of Fabrics (Yds/Mtr)"),
                WorkbookHeader(column_index=10, text="L/C Amnd No."),
                WorkbookHeader(column_index=11, text="L/C Amnd Date"),
                WorkbookHeader(column_index=13, text="Lien Bank"),
                WorkbookHeader(column_index=14, text="Master L/C No."),
                WorkbookHeader(column_index=15, text="Master L/C Issue Dt."),
                WorkbookHeader(column_index=22, text="Amount"),
                WorkbookHeader(column_index=29, text="Commercial File No."),
                WorkbookHeader(column_index=33, text="Bangladesh Bank Ref."),
            ],
            rows=[WorkbookRow(row_index=3, values={29: "P/26/0042"})],
        )

        payload = summarize_workbook_readiness(
            workflow_id=WorkflowId.EXPORT_LC_SC,
            workbook_snapshot=snapshot,
        )

        self.assertEqual(payload["header_mapping_status"], "resolved")
        self.assertEqual(payload["header_mapping"]["file_no"], 29)
        self.assertEqual(payload["header_mapping"]["buyer_name"], 2)
        self.assertEqual(payload["header_mapping"]["lc_sc_no"], 4)
        self.assertEqual(payload["header_mapping"]["export_amount"], 6)

    def test_summarize_workbook_readiness_reports_resolved_export_mapping(self) -> None:
        snapshot = WorkbookSnapshot(
            sheet_name="Sheet1",
            headers=[
                WorkbookHeader(column_index=1, text="File No."),
                WorkbookHeader(column_index=2, text="L/C No."),
                WorkbookHeader(column_index=3, text="Buyer Name"),
                WorkbookHeader(column_index=4, text="L/C Issuing Bank"),
                WorkbookHeader(column_index=5, text="LC Issue Date"),
                WorkbookHeader(column_index=6, text="Amount"),
                WorkbookHeader(column_index=7, text="Shipment Date"),
                WorkbookHeader(column_index=8, text="Expiry Date"),
                WorkbookHeader(column_index=9, text="Quantity of Fabrics (Yds/Mtr)"),
                WorkbookHeader(column_index=10, text="L/C Amnd No."),
                WorkbookHeader(column_index=11, text="L/C Amnd Date"),
                WorkbookHeader(column_index=12, text="Lien Bank"),
                WorkbookHeader(column_index=13, text="Master L/C No."),
                WorkbookHeader(column_index=14, text="Master L/C Issue Dt."),
                WorkbookHeader(column_index=22, text="Amount"),
                WorkbookHeader(column_index=33, text="Bangladesh Bank Ref."),
            ],
            rows=[WorkbookRow(row_index=3, values={1: "P/26/0042"})],
        )

        payload = summarize_workbook_readiness(
            workflow_id=WorkflowId.EXPORT_LC_SC,
            workbook_snapshot=snapshot,
        )

        self.assertEqual(payload["workbook_available"], True)
        self.assertEqual(payload["header_mapping_status"], "resolved")
        self.assertEqual(payload["header_mapping"]["export_amount"], 6)
        self.assertEqual(payload["row_count"], 1)

    def test_summarize_workbook_readiness_reports_target_prevalidation(self) -> None:
        snapshot = WorkbookSnapshot(
            sheet_name="Sheet1",
            headers=[
                WorkbookHeader(column_index=1, text="File No."),
                WorkbookHeader(column_index=2, text="L/C No."),
                WorkbookHeader(column_index=3, text="Buyer Name"),
                WorkbookHeader(column_index=4, text="L/C Issuing Bank"),
                WorkbookHeader(column_index=5, text="LC Issue Date"),
                WorkbookHeader(column_index=6, text="Amount"),
                WorkbookHeader(column_index=7, text="Shipment Date"),
                WorkbookHeader(column_index=8, text="Expiry Date"),
                WorkbookHeader(column_index=9, text="Quantity of Fabrics (Yds/Mtr)"),
                WorkbookHeader(column_index=10, text="L/C Amnd No."),
                WorkbookHeader(column_index=11, text="L/C Amnd Date"),
                WorkbookHeader(column_index=12, text="Lien Bank"),
                WorkbookHeader(column_index=13, text="Master L/C No."),
                WorkbookHeader(column_index=14, text="Master L/C Issue Dt."),
                WorkbookHeader(column_index=22, text="Amount"),
                WorkbookHeader(column_index=33, text="Bangladesh Bank Ref."),
            ],
            rows=[],
        )
        staged_write_plan = [
            WriteOperation(
                write_operation_id="op-1",
                run_id="run-1",
                mail_id="mail-1",
                operation_index_within_mail=0,
                sheet_name="Sheet1",
                row_index=3,
                column_key="file_no",
                expected_pre_write_value=None,
                expected_post_write_value="P/26/0042",
                row_eligibility_checks=["append_target_row_is_new", "target_cell_blank_by_construction"],
            )
        ]

        payload = summarize_workbook_readiness(
            workflow_id=WorkflowId.EXPORT_LC_SC,
            workbook_snapshot=snapshot,
            staged_write_plan=staged_write_plan,
            run_id="run-1",
        )

        self.assertEqual(payload["staged_write_operation_count"], 1)
        self.assertEqual(payload["target_prevalidation"]["status"], "passed")
        self.assertEqual(payload["target_prevalidation"]["summary"]["matches_pre_write"], 1)


if __name__ == "__main__":
    unittest.main()
