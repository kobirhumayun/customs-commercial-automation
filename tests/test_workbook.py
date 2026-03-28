from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project.workbook import (
    JsonManifestWorkbookSnapshotProvider,
    XLWingsWorkbookSnapshotProvider,
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


if __name__ == "__main__":
    unittest.main()
