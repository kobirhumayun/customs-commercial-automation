from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project.erp import JsonManifestERPRowProvider


class ERPProviderTests(unittest.TestCase):
    def test_json_manifest_provider_normalizes_and_sorts_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "erp.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "file_number": "P/26/42",
                            "lc_sc_number": "LC  -0038",
                            "buyer_name": "Ananta Garments Ltd.\\Dhaka.",
                            "lc_sc_date": "2026-01-10",
                            "source_row_index": 9,
                        },
                        {
                            "file_number": "P-26-0042",
                            "lc_sc_number": "LC-0038",
                            "buyer_name": "ANANTA GARMENTS LTD",
                            "lc_sc_date": "2026-01-10",
                            "source_row_index": 4,
                        },
                    ]
                ),
                encoding="utf-8",
            )

            provider = JsonManifestERPRowProvider(manifest_path)
            rows = provider.lookup_rows(file_numbers=["P/26/0042"])

        self.assertEqual([row.source_row_index for row in rows["P/26/0042"]], [4, 9])
        self.assertEqual(rows["P/26/0042"][0].lc_sc_number, "LC-0038")
        self.assertEqual(rows["P/26/0042"][0].buyer_name, "ANANTA GARMENTS LTD")


if __name__ == "__main__":
    unittest.main()
