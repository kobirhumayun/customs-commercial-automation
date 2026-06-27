from __future__ import annotations

import unittest

from project.erp import ERPRegisterRow
from project.workbook import WorkbookHeader, WorkbookSnapshot
from project.workflows.export_lc_sc.payloads import ExportFileNumberMatch, ExportMailPayload
from project.workflows.export_lc_sc.staging import stage_export_append_operations


class ExportLCSCStagingTests(unittest.TestCase):
    def test_stages_amendment_defaults_master_recv_date_and_trimmed_bb_ref(self) -> None:
        result = stage_export_append_operations(
            run_id="run-1",
            mail_id="mail-1",
            payload=_payload(
                ERPRegisterRow(
                    file_number="P/26/0042",
                    lc_sc_number="LC-0038",
                    buyer_name="ANANTA GARMENTS LTD",
                    lc_sc_date="2026-01-10",
                    source_row_index=5,
                    current_lc_value="10000",
                    lc_qty="5000",
                    amd_no="",
                    amd_date=" ",
                    ship_remarks=" 1234567890 ",
                )
            ),
            workbook_snapshot=_snapshot(),
        )

        operations_by_column = {
            operation.column_key: operation.expected_post_write_value
            for operation in result.staged_write_operations
        }

        self.assertEqual(operations_by_column["lc_amnd_no"], "-")
        self.assertEqual(operations_by_column["lc_amnd_date"], "-")
        self.assertEqual(operations_by_column["master_lc_recv_date"], "-")
        self.assertEqual(operations_by_column["bangladesh_bank_ref"], "1234567890")

    def test_skips_bangladesh_bank_ref_when_ship_remarks_is_invalid(self) -> None:
        invalid_values = ["", "123456789", "12345 67890", "BB1234567890", "12345-67890"]

        for invalid_value in invalid_values:
            with self.subTest(invalid_value=invalid_value):
                result = stage_export_append_operations(
                    run_id="run-1",
                    mail_id="mail-1",
                    payload=_payload(
                        ERPRegisterRow(
                            file_number="P/26/0042",
                            lc_sc_number="LC-0038",
                            buyer_name="ANANTA GARMENTS LTD",
                            lc_sc_date="2026-01-10",
                            source_row_index=5,
                            current_lc_value="10000",
                            lc_qty="5000",
                            ship_remarks=invalid_value,
                        )
                    ),
                    workbook_snapshot=_snapshot(),
                )

                column_keys = {operation.column_key for operation in result.staged_write_operations}
                self.assertNotIn("bangladesh_bank_ref", column_keys)
                self.assertIn("master_lc_recv_date", column_keys)


def _payload(row: ERPRegisterRow) -> ExportMailPayload:
    return ExportMailPayload(
        parsed_subject=None,
        file_numbers=[row.file_number],
        erp_matches=[
            ExportFileNumberMatch(
                file_number=row.file_number,
                canonical_row=row,
                matched_rows=[row],
            )
        ],
        verified_family=row.family,
        attachments_in_order=[],
    )


def _snapshot() -> WorkbookSnapshot:
    return WorkbookSnapshot(
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
            WorkbookHeader(column_index=15, text="Master L/C Recv. Date"),
            WorkbookHeader(column_index=22, text="Amount"),
            WorkbookHeader(column_index=33, text="Bangladesh Bank Ref."),
        ],
        rows=[],
    )


if __name__ == "__main__":
    unittest.main()
