from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project.erp import DelimitedERPExportRowProvider, JsonManifestERPRowProvider, PlaywrightERPRowProvider
from project.workflows.erp_inspection import inspect_erp_rows


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

    def test_delimited_export_provider_reads_row_two_headers_and_optional_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "erp.csv"
            export_path.write_text(
                "\n".join(
                    [
                        "rptDateWiseLCRegister",
                        "File No.,L/C No.,Buyer Name,LC DT.,Current LC Value,LC Qty,LC Unit,Amd No,Amd DT,Nego Bank",
                        "P/26/42,LC  -0038,Ananta Garments Ltd.\\Dhaka.,2026-01-10,12345.00,4000,MTR,05,2026-01-15,ABC Bank",
                    ]
                ),
                encoding="utf-8",
            )

            provider = DelimitedERPExportRowProvider(export_path)
            rows = provider.lookup_rows(file_numbers=["P/26/0042"])

        self.assertEqual(len(rows["P/26/0042"]), 1)
        self.assertEqual(rows["P/26/0042"][0].source_row_index, 3)
        self.assertEqual(rows["P/26/0042"][0].lc_sc_number, "LC-0038")
        self.assertEqual(rows["P/26/0042"][0].buyer_name, "ANANTA GARMENTS LTD")
        self.assertEqual(rows["P/26/0042"][0].current_lc_value, "12345.00")
        self.assertEqual(rows["P/26/0042"][0].lc_unit, "MTR")
        self.assertEqual(rows["P/26/0042"][0].amd_no, "05")

    def test_delimited_export_provider_rejects_invalid_required_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "erp.csv"
            export_path.write_text(
                "\n".join(
                    [
                        "rptDateWiseLCRegister",
                        "File No.,L/C No.,Buyer Name,LC DT.",
                        "NOT-A-FILE,LC-0038,Ananta Garments Ltd,2026-01-10",
                    ]
                ),
                encoding="utf-8",
            )

            provider = DelimitedERPExportRowProvider(export_path)
            with self.assertRaisesRegex(ValueError, "invalid canonical ERP field"):
                provider.lookup_rows(file_numbers=["P/26/0042"])

    def test_playwright_provider_uses_first_parseable_table(self) -> None:
        tables = [
            [["not", "a", "register"]],
            [
                ["rptDateWiseLCRegister"],
                ["File No.", "L/C No.", "Buyer Name", "LC DT.", "Current LC Value"],
                ["P/26/42", "LC-0038", "Ananta Garments Ltd.\\Dhaka.", "2026-01-10", "12345.00"],
            ],
        ]

        with patch("project.erp.providers._fetch_playwright_report_tables", return_value=tables) as fetch_mock:
            provider = PlaywrightERPRowProvider(
                base_url="https://erp.local",
                report_relative_url="/reports/rptDateWiseLCRegister",
                browser_channel="msedge",
            )
            rows = provider.lookup_rows(file_numbers=["P/26/0042"])

        self.assertEqual(len(rows["P/26/0042"]), 1)
        self.assertEqual(rows["P/26/0042"][0].buyer_name, "ANANTA GARMENTS LTD")
        fetch_mock.assert_called_once()
        self.assertEqual(fetch_mock.call_args.kwargs["base_url"], "https://erp.local")
        self.assertEqual(fetch_mock.call_args.kwargs["report_relative_url"], "/reports/rptDateWiseLCRegister")

    def test_inspect_erp_rows_normalizes_and_deduplicates_requested_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "erp.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "file_number": "P/26/42",
                            "lc_sc_number": "LC-0038",
                            "buyer_name": "Ananta Garments Ltd.",
                            "lc_sc_date": "2026-01-10",
                            "source_row_index": 4,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            payload = inspect_erp_rows(
                provider=JsonManifestERPRowProvider(manifest_path),
                requested_file_numbers=["P/26/42", "P-26-0042"],
            )

        self.assertEqual(payload["canonical_file_numbers"], ["P/26/0042"])
        self.assertEqual(payload["match_count"], 1)
        self.assertEqual(payload["rows_by_file_number"]["P/26/0042"][0].source_row_index, 4)


if __name__ == "__main__":
    unittest.main()
