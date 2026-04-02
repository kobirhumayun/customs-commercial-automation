from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project.erp import (
    DelimitedERPExportRowProvider,
    inspect_playwright_report_download,
    JsonManifestERPRowProvider,
    PlaywrightERPRowProvider,
)
from project.workflows.erp_inspection import inspect_erp_rows


class ERPProviderTests(unittest.TestCase):
    def test_inspect_playwright_report_download_saves_debug_artifacts_and_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "erp-debug"

            class FakeDownload:
                suggested_filename = "report.csv"

                def save_as(self, path: str) -> None:
                    Path(path).write_text("downloaded", encoding="utf-8")

            class FakeDownloadContext:
                def __init__(self) -> None:
                    self.value = FakeDownload()

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

            class FakeLocator:
                def __init__(self, page, selector: str) -> None:
                    self.page = page
                    self.selector = selector
                    self.value = None

                @property
                def first(self):
                    return self

                def count(self) -> int:
                    return 1

                def fill(self, value: str) -> None:
                    self.page.fills.append((self.selector, value))
                    self.value = value

                def click(self) -> None:
                    self.page.clicks.append(self.selector)

                def wait_for(self, state: str, timeout: int) -> None:
                    self.page.waits.append((self.selector, state, timeout))

                def input_value(self) -> str:
                    return self.value or ""

            class FakePage:
                def __init__(self) -> None:
                    self.url = "https://erp.local/final"
                    self.fills: list[tuple[str, str]] = []
                    self.clicks: list[str] = []
                    self.waits: list[tuple[str, str, int]] = []
                    self.locators: dict[str, FakeLocator] = {}

                def goto(self, url: str, wait_until: str, timeout: int) -> None:
                    self.goto_call = (url, wait_until, timeout)

                def wait_for_load_state(self, state: str, timeout: int) -> None:
                    self.load_state_call = (state, timeout)

                def locator(self, selector: str):
                    if selector not in self.locators:
                        self.locators[selector] = FakeLocator(self, selector)
                    return self.locators[selector]

                def expect_download(self, timeout: int):
                    self.download_timeout = timeout
                    return FakeDownloadContext()

                def title(self) -> str:
                    return "ERP Report"

                def content(self) -> str:
                    return "<html>report</html>"

                def screenshot(self, path: str, full_page: bool) -> None:
                    Path(path).write_bytes(b"png")

            class FakeContext:
                def __init__(self) -> None:
                    self.page = FakePage()

                def new_page(self):
                    return self.page

                def close(self) -> None:
                    return None

            class FakeBrowser:
                def new_context(self, **_kwargs):
                    return FakeContext()

                def close(self) -> None:
                    return None

            class FakeChromium:
                def launch(self, **_kwargs):
                    return FakeBrowser()

            class FakePlaywright:
                chromium = FakeChromium()

            class FakeSyncPlaywright:
                def __call__(self):
                    return self

                def __enter__(self):
                    return FakePlaywright()

                def __exit__(self, exc_type, exc, tb):
                    return False

            with patch("project.erp.providers._load_playwright_sync_api", return_value=FakeSyncPlaywright()):
                payload = inspect_playwright_report_download(
                    base_url="https://erp.local",
                    report_relative_url="/report",
                    browser_channel="msedge",
                    storage_state_path=None,
                    timeout_ms=30_000,
                    headless=False,
                    output_dir=output_dir,
                    field_values=[("#fromDate", "2026-03-01"), ("#toDate", "2026-03-31")],
                    submit_selector="#show",
                    post_submit_wait_selector="#downloadDropdown",
                    download_menu_selector="#downloadDropdown",
                    download_format_selector="text=CSV",
                )

            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["page_title"], "ERP Report")
            self.assertTrue(Path(str(payload["html_path"])).exists())
            self.assertTrue(Path(str(payload["screenshot_path"])).exists())
            self.assertTrue(Path(str(payload["downloaded_file_path"])).exists())
            self.assertEqual(len(payload["field_readbacks"]), 2)
            self.assertTrue(payload["field_readbacks"][0]["matched"])
            self.assertIsNotNone(payload["download_receipt"])
            self.assertEqual(payload["download_receipt"]["saved_filename"], "report.csv")
            self.assertGreater(payload["download_receipt"]["size_bytes"], 0)
            self.assertEqual(payload["download_receipt"]["content_kind"], "delimited_text")

    def test_playwright_provider_defaults_to_live_documents_report_path(self) -> None:
        tables = [
            [
                ["DateWiseLCRegisterForDocuments"],
                ["File No.", "L/C No.", "Buyer Name", "LC DT."],
                ["P/26/42", "LC-0038", "Ananta Garments Ltd.\\Dhaka.", "2026-01-10"],
            ],
        ]

        with patch("project.erp.providers._fetch_playwright_report_tables", return_value=tables) as fetch_mock:
            provider = PlaywrightERPRowProvider(base_url="https://erp.local")
            provider.lookup_rows(file_numbers=["P/26/0042"])

        self.assertEqual(
            fetch_mock.call_args.kwargs["report_relative_url"],
            "/RptCommercialExport/DateWiseLCRegisterForDocuments",
        )

    def test_playwright_provider_can_download_and_parse_export_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "report.csv"
            export_path.write_text(
                "\n".join(
                    [
                        "rptDateWiseLCRegister",
                        "File No.,L/C No.,Buyer Name,LC DT.,Current LC Value",
                        "P/26/42,LC-0038,Ananta Garments Ltd.\\Dhaka.,2026-01-10,12345.00",
                    ]
                ),
                encoding="utf-8",
            )

            with patch(
                "project.erp.providers.inspect_playwright_report_download",
                return_value={
                    "status": "ready",
                    "downloaded_file_path": str(export_path),
                    "field_readbacks": [
                        {"selector": "#fromDate", "matched": True},
                        {"selector": "#toDate", "matched": True},
                    ],
                    "download_receipt": {
                        "exists": True,
                        "is_empty": False,
                        "looks_like_html": False,
                        "has_required_erp_headers": True,
                    },
                },
            ) as inspect_mock:
                provider = PlaywrightERPRowProvider(
                    base_url="https://erp.local",
                    field_values=(("#fromDate", "01-Apr-2025"), ("#toDate", "31-Mar-2026")),
                    submit_selector="#show",
                    post_submit_wait_selector="#downloadDropdown",
                    download_menu_selector="#downloadDropdown",
                    download_format_selector="text=CSV",
                    browser_channel="msedge",
                )
                rows = provider.lookup_rows(file_numbers=["P/26/0042"])

        self.assertEqual(len(rows["P/26/0042"]), 1)
        self.assertEqual(rows["P/26/0042"][0].lc_sc_number, "LC-0038")
        self.assertEqual(rows["P/26/0042"][0].current_lc_value, "12345.00")
        self.assertEqual(
            inspect_mock.call_args.kwargs["field_values"],
            [("#fromDate", "01-Apr-2025"), ("#toDate", "31-Mar-2026")],
        )
        self.assertEqual(inspect_mock.call_args.kwargs["download_format_selector"], "text=CSV")

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

    def test_delimited_export_provider_accepts_live_erp_style_lc_numbers_and_dates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "erp.csv"
            export_path.write_text(
                "\n".join(
                    [
                        "L/C Register For Documents",
                        "File No.,LC No.,Buyer Name,LC DT.,Current LC Value",
                        "P/26/0624,DPCBD1175392,CUTTING EDGE INDUSTRIES LTD.\\1612,30-Mar-26,36467.20",
                    ]
                ),
                encoding="utf-8",
            )

            provider = DelimitedERPExportRowProvider(export_path)
            rows = provider.lookup_rows(file_numbers=["P/26/0624"])

        self.assertEqual(len(rows["P/26/0624"]), 1)
        self.assertEqual(rows["P/26/0624"][0].lc_sc_number, "DPCBD1175392")
        self.assertEqual(rows["P/26/0624"][0].buyer_name, "CUTTING EDGE INDUSTRIES LTD")
        self.assertEqual(rows["P/26/0624"][0].lc_sc_date, "2026-03-30")

    def test_delimited_export_provider_falls_back_to_windows_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "erp.csv"
            export_path.write_bytes(
                "\n".join(
                    [
                        "rptDateWiseLCRegister",
                        "File No.,L/C No.,Buyer Name,LC DT.",
                        "P/26/42,LC-0038,Ananta Garments “Test”,2026-01-10",
                    ]
                ).encode("cp1252")
            )

            provider = DelimitedERPExportRowProvider(export_path)
            rows = provider.lookup_rows(file_numbers=["P/26/0042"])

        self.assertEqual(len(rows["P/26/0042"]), 1)
        self.assertIn("ANANTA GARMENTS", rows["P/26/0042"][0].buyer_name)

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
