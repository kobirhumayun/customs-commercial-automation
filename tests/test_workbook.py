from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project.workbook import JsonManifestWorkbookSnapshotProvider, resolve_header_mapping
from project.workbook.mapping import EXPORT_HEADER_SPECS


class WorkbookTests(unittest.TestCase):
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
