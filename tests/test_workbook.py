from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project.models import WorkflowId, WriteOperation
from project.workbook import (
    JsonManifestWorkbookSnapshotProvider,
    WorkbookHeader,
    WorkbookRow,
    WorkbookSnapshot,
    XLWingsWorkbookSnapshotProvider,
    XLWingsWorkbookWriteSessionProvider,
    prevalidate_staged_write_plan,
    resolve_header_mapping,
)
from project.workbook.mapping import EXPORT_HEADER_SPECS


class WorkbookTests(unittest.TestCase):
    def test_xlwings_provider_extracts_headers_and_rows_from_live_sheet(self) -> None:
        class FakeCell:
            def __init__(self, row: int, column: int) -> None:
                self.row = row
                self.column = column

        class FakeUsedRange:
            last_cell = FakeCell(4, 3)

        class FakeRange:
            def __init__(self, value):
                self.value = value

        class FakeSheet:
            name = "Sheet1"
            used_range = FakeUsedRange()

            def range(self, start, end):
                if start == (2, 1) and end == (2, 3):
                    return FakeRange(["File No.", "L/C No.", "Buyer Name"])
                if start == (3, 1) and end == (4, 3):
                    return FakeRange(
                        [
                            ["P/26/0042", "LC-0038", "ANANTA GARMENTS LTD"],
                            ["P/26/0007", "SC-010-PDL-8", "ZYTA APPARELS LTD"],
                        ]
                    )
                raise AssertionError(f"Unexpected range request: {start} -> {end}")

        class FakeBook:
            def __init__(self) -> None:
                self.sheets = [FakeSheet()]

            def close(self) -> None:
                self.closed = True

        class FakeBooks:
            def open(self, *_args, **_kwargs):
                return FakeBook()

        class FakeApp:
            def __init__(self, **_kwargs) -> None:
                self.books = FakeBooks()

            def quit(self) -> None:
                self.quit_called = True

        class FakeXLWings:
            App = FakeApp

        with patch("project.workbook.providers._load_xlwings_module", return_value=FakeXLWings()):
            snapshot = XLWingsWorkbookSnapshotProvider(Path("C:/fake.xlsx")).load_snapshot()

        self.assertEqual(snapshot.sheet_name, "Sheet1")
        self.assertEqual([header.text for header in snapshot.headers], ["File No.", "L/C No.", "Buyer Name"])
        self.assertEqual(snapshot.rows[0].values[1], "P/26/0042")
        self.assertEqual(snapshot.rows[1].row_index, 4)

    def test_json_manifest_workbook_provider_loads_snapshot_and_resolves_export_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "workbook.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "sheet_name": "Sheet1",
                        "headers": [
                            {"column_index": 1, "text": "File No."},
                            {"column_index": 2, "text": "L/C No."},
                            {"column_index": 3, "text": "Buyer Name"},
                            {"column_index": 4, "text": "L/C Issuing Bank"},
                            {"column_index": 5, "text": "LC Issue Date"},
                            {"column_index": 6, "text": "Amount"},
                            {"column_index": 7, "text": "Shipment Date"},
                            {"column_index": 8, "text": "Expiry Date"},
                            {"column_index": 9, "text": "Quantity of Fabrics (Yds/Mtr)"},
                            {"column_index": 10, "text": "L/C Amnd No."},
                            {"column_index": 11, "text": "L/C Amnd Date"},
                            {"column_index": 12, "text": "Lien Bank"},
                            {"column_index": 13, "text": "Master L/C No."},
                            {"column_index": 14, "text": "Master L/C Issue Dt."},
                            {"column_index": 22, "text": "Amount"},
                            {"column_index": 33, "text": "Bangladesh Bank Ref."},
                        ],
                        "rows": [
                            {"row_index": 3, "values": {"1": "P/26/0042"}},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            snapshot = JsonManifestWorkbookSnapshotProvider(manifest_path).load_snapshot()

        mapping = resolve_header_mapping(snapshot, EXPORT_HEADER_SPECS)

        self.assertEqual(snapshot.sheet_name, "Sheet1")
        self.assertEqual(mapping["export_amount"], 6)
        self.assertEqual(mapping["file_no"], 1)

    def test_xlwings_write_session_provider_reports_read_only_conflict(self) -> None:
        class FakeCell:
            def __init__(self, row: int, column: int) -> None:
                self.row = row
                self.column = column

        class FakeUsedRange:
            last_cell = FakeCell(2, 2)

        class FakeSheet:
            name = "Sheet1"
            used_range = FakeUsedRange()

            def range(self, *_args, **_kwargs):
                raise AssertionError("Snapshot capture should not run for read-only conflict")

        class FakeBook:
            def __init__(self) -> None:
                self.sheets = [FakeSheet()]
                self.api = type("FakeApi", (), {"ReadOnly": True})()

            def close(self) -> None:
                self.closed = True

        class FakeBooks:
            def open(self, *_args, **_kwargs):
                return FakeBook()

        class FakeApp:
            def __init__(self, **_kwargs) -> None:
                self.books = FakeBooks()

            def quit(self) -> None:
                self.quit_called = True

        class FakeXLWings:
            App = FakeApp

        with patch("project.workbook.providers._load_xlwings_module", return_value=FakeXLWings()):
            with patch("project.workbook.session._load_xlwings_module", return_value=FakeXLWings()):
                result = XLWingsWorkbookWriteSessionProvider(Path("C:/fake.xlsx")).open_preflight_session(
                    operator_context=None
                )

        self.assertEqual(result.discrepancy_code, "workbook_open_readonly")
        self.assertIsNone(result.snapshot)
        self.assertEqual(result.preflight.status, "read_only_conflict")
        self.assertFalse(result.preflight.save_capable)

    def test_prevalidate_staged_write_plan_matches_append_targets(self) -> None:
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
            ),
            WriteOperation(
                write_operation_id="op-2",
                run_id="run-1",
                mail_id="mail-1",
                operation_index_within_mail=1,
                sheet_name="Sheet1",
                row_index=3,
                column_key="lc_sc_no",
                expected_pre_write_value=None,
                expected_post_write_value="LC-0038",
                row_eligibility_checks=["append_target_row_is_new", "target_cell_blank_by_construction"],
            ),
        ]

        result = prevalidate_staged_write_plan(
            workflow_id=WorkflowId.EXPORT_LC_SC,
            run_id="run-1",
            workbook_snapshot=snapshot,
            staged_write_plan=staged_write_plan,
        )

        self.assertEqual(result.summary.status, "passed")
        self.assertEqual(result.summary.matches_pre_write, 2)
        self.assertEqual(result.discrepancy_reports, [])
        self.assertEqual([probe.classification for probe in result.probes], ["matches_pre_write", "matches_pre_write"])

    def test_prevalidate_staged_write_plan_hard_blocks_existing_append_target(self) -> None:
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

        result = prevalidate_staged_write_plan(
            workflow_id=WorkflowId.EXPORT_LC_SC,
            run_id="run-1",
            workbook_snapshot=snapshot,
            staged_write_plan=staged_write_plan,
        )

        self.assertEqual(result.summary.status, "hard_blocked")
        self.assertEqual(result.probes[0].classification, "mismatch_unknown")
        self.assertEqual(result.discrepancy_reports[0].code, "workbook_target_prevalidation_failed")

    def test_prevalidate_staged_write_plan_allows_first_blank_buyer_name_row(self) -> None:
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
            rows=[
                WorkbookRow(row_index=3, values={1: "P/26/0001", 3: "FILLED BUYER"}),
                WorkbookRow(row_index=4, values={1: "", 2: "", 3: ""}),
            ],
        )
        staged_write_plan = [
            WriteOperation(
                write_operation_id="op-1",
                run_id="run-1",
                mail_id="mail-1",
                operation_index_within_mail=0,
                sheet_name="Sheet1",
                row_index=4,
                column_key="file_no",
                expected_pre_write_value=None,
                expected_post_write_value="P/26/0042",
                row_eligibility_checks=[
                    "append_target_row_has_blank_buyer_name_or_is_new",
                    "target_cell_blank_by_construction",
                ],
            ),
            WriteOperation(
                write_operation_id="op-2",
                run_id="run-1",
                mail_id="mail-1",
                operation_index_within_mail=1,
                sheet_name="Sheet1",
                row_index=4,
                column_key="buyer_name",
                expected_pre_write_value=None,
                expected_post_write_value="ANANTA GARMENTS LTD",
                row_eligibility_checks=[
                    "append_target_row_has_blank_buyer_name_or_is_new",
                    "target_cell_blank_by_construction",
                ],
            ),
        ]

        result = prevalidate_staged_write_plan(
            workflow_id=WorkflowId.EXPORT_LC_SC,
            run_id="run-1",
            workbook_snapshot=snapshot,
            staged_write_plan=staged_write_plan,
        )

        self.assertEqual(result.summary.status, "passed")
        self.assertEqual(result.discrepancy_reports, [])
        self.assertEqual([probe.classification for probe in result.probes], ["matches_pre_write", "matches_pre_write"])

    def test_prevalidate_staged_write_plan_treats_blank_optional_cells_as_pre_write_matches(self) -> None:
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
            rows=[WorkbookRow(row_index=3, values={3: "", 10: "", 11: ""})],
        )
        staged_write_plan = [
            WriteOperation(
                write_operation_id="op-1",
                run_id="run-1",
                mail_id="mail-1",
                operation_index_within_mail=0,
                sheet_name="Sheet1",
                row_index=3,
                column_key="lc_amnd_no",
                expected_pre_write_value=None,
                expected_post_write_value="",
                row_eligibility_checks=[
                    "append_target_row_has_blank_buyer_name_or_is_new",
                    "target_cell_blank_by_construction",
                ],
            ),
            WriteOperation(
                write_operation_id="op-2",
                run_id="run-1",
                mail_id="mail-1",
                operation_index_within_mail=1,
                sheet_name="Sheet1",
                row_index=3,
                column_key="lc_amnd_date",
                expected_pre_write_value=None,
                expected_post_write_value="",
                row_eligibility_checks=[
                    "append_target_row_has_blank_buyer_name_or_is_new",
                    "target_cell_blank_by_construction",
                ],
            ),
        ]

        result = prevalidate_staged_write_plan(
            workflow_id=WorkflowId.EXPORT_LC_SC,
            run_id="run-1",
            workbook_snapshot=snapshot,
            staged_write_plan=staged_write_plan,
        )

        self.assertEqual(result.summary.status, "passed")
        self.assertEqual(result.discrepancy_reports, [])
        self.assertEqual([probe.classification for probe in result.probes], ["matches_pre_write", "matches_pre_write"])


if __name__ == "__main__":
    unittest.main()
