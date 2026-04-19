from __future__ import annotations

import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from contextlib import redirect_stderr
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from project.cli import _load_erp_provider, main
from project.config import load_workflow_config
from project.models import (
    EmailAttachment,
    EmailMessage,
    FinalDecision,
    MailMovePhaseStatus,
    MailOutcomeRecord,
    MailProcessingStatus,
    PrintBatch,
    PrintPhaseStatus,
    RunReport,
    WorkflowId,
    WriteOperation,
    WritePhaseStatus,
)
from project.storage import create_run_artifact_layout
from project.workflows.document_verification import DocumentManualVerificationResult
from project.workflows.registry import WORKFLOW_REGISTRY
from project.workbook import WorkbookHeader


class CLITests(unittest.TestCase):
    def test_load_erp_provider_uses_configured_download_flow_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = datetime.datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            storage_state_path = root / "storage-state.json"
            storage_state_path.write_text("{}", encoding="utf-8")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'playwright_storage_state_path = "{storage_state_path.as_posix()}"',
                        'erp_report_fill_values = ["#fromDate=01-Apr-2025", "#toDate=31-Mar-2026"]',
                        'erp_report_submit_selector = "#show"',
                        'erp_report_post_submit_wait_selector = "#downloadDropdown"',
                        'erp_report_download_menu_selector = "#downloadDropdown"',
                        'erp_report_download_format_selector = "text=CSV"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            config = load_workflow_config(WORKFLOW_REGISTRY["export_lc_sc"], config_path)

            with patch("project.cli.PlaywrightERPRowProvider") as provider_mock:
                _load_erp_provider(
                    erp_json=None,
                    erp_export=None,
                    live_erp=True,
                    config=config,
                )

        self.assertEqual(provider_mock.call_args.kwargs["field_values"], (("#fromDate", "01-Apr-2025"), ("#toDate", "31-Mar-2026")))
        self.assertEqual(provider_mock.call_args.kwargs["submit_selector"], "#show")
        self.assertEqual(provider_mock.call_args.kwargs["post_submit_wait_selector"], "#downloadDropdown")
        self.assertEqual(provider_mock.call_args.kwargs["download_menu_selector"], "#downloadDropdown")
        self.assertEqual(provider_mock.call_args.kwargs["download_format_selector"], "text=CSV")
        self.assertEqual(provider_mock.call_args.kwargs["browser_channel"], "msedge")
        self.assertEqual(provider_mock.call_args.kwargs["storage_state_path"], storage_state_path)

    def test_load_erp_provider_defaults_to_download_flow_when_config_selectors_are_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = datetime.datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            config = load_workflow_config(WORKFLOW_REGISTRY["export_lc_sc"], config_path)

            with patch("project.cli.PlaywrightERPRowProvider") as provider_mock:
                _load_erp_provider(
                    erp_json=None,
                    erp_export=None,
                    live_erp=True,
                    config=config,
                )

        self.assertEqual(provider_mock.call_args.kwargs["submit_selector"], 'role=button[name="Submit"]')
        self.assertEqual(provider_mock.call_args.kwargs["post_submit_wait_selector"], ".dx-menu-item-popout")
        self.assertEqual(provider_mock.call_args.kwargs["download_menu_selector"], ".dx-menu-item-popout")
        self.assertEqual(
            provider_mock.call_args.kwargs["download_format_selector"],
            '.dxrd-preview-export-item-text:text-is("CSV")',
        )
        self.assertEqual(
            provider_mock.call_args.kwargs["field_values"][0][0],
            ":nth-match(.dx-texteditor-input, 3)",
        )
        self.assertEqual(
            provider_mock.call_args.kwargs["field_values"][1][0],
            ":nth-match(.dx-texteditor-input, 4)",
        )

    def test_inspect_document_text_command_writes_json_audit_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            document_path = root / "saved.pdf"
            document_path.write_bytes(b"%PDF-1.4\nfake\n")

            buffer = io.StringIO()
            with patch(
                "project.cli.extract_saved_document_raw_report",
                return_value={
                    "mode": "text",
                    "document_path": str(document_path),
                    "page_count": 1,
                    "combined_text": "raw extracted text",
                    "pages": [{"page_number": 1, "text": "raw extracted text"}],
                },
            ):
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "inspect-document-text",
                            "--document-path",
                            str(document_path),
                            "--mode",
                            "text",
                        ]
                    )

            payload = json.loads(buffer.getvalue())
            output_path = Path(payload["output_json"])
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["mode"], "text")
            self.assertTrue(output_path.name.endswith(".pdf.extraction.text.json"))
            self.assertEqual(report["combined_text"], "raw extracted text")
            self.assertEqual(report["pages"][0]["page_number"], 1)

    def test_inspect_document_text_command_passes_search_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            document_path = root / "saved.pdf"
            document_path.write_bytes(b"%PDF-1.4\nfake\n")

            buffer = io.StringIO()
            with patch(
                "project.cli.extract_saved_document_raw_report",
                return_value={
                    "mode": "layered",
                    "document_path": str(document_path),
                    "page_count": 2,
                    "combined_text": "target",
                    "pages": [],
                    "search": {
                        "search_text": "target",
                        "page_from": 2,
                        "page_to": 2,
                        "match_count": 1,
                        "matches": [{"page_number": 2, "count": 1, "excerpts": ["target"]}],
                    },
                },
            ) as extract_mock:
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "inspect-document-text",
                            "--document-path",
                            str(document_path),
                            "--mode",
                            "layered",
                            "--search-text",
                            "target",
                            "--page-from",
                            "2",
                            "--page-to",
                            "2",
                        ]
                    )

            payload = json.loads(buffer.getvalue())
            report = json.loads(Path(payload["output_json"]).read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(report["search"]["match_count"], 1)
            self.assertEqual(extract_mock.call_args.kwargs["search_text"], "target")
            self.assertEqual(extract_mock.call_args.kwargs["page_from"], 2)
            self.assertEqual(extract_mock.call_args.kwargs["page_to"], 2)

    def test_inspect_document_text_command_accepts_img2table_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            document_path = root / "saved.pdf"
            document_path.write_bytes(b"%PDF-1.4\nfake\n")

            buffer = io.StringIO()
            with patch(
                "project.cli.extract_saved_document_raw_report",
                return_value={
                    "mode": "img2table",
                    "document_path": str(document_path),
                    "page_count": 1,
                    "combined_text": "L/C No. | LC-0038",
                    "pages": [{"page_number": 1, "tables": []}],
                },
            ) as extract_mock:
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "inspect-document-text",
                            "--document-path",
                            str(document_path),
                            "--mode",
                            "img2table",
                        ]
                    )

            payload = json.loads(buffer.getvalue())
            report = json.loads(Path(payload["output_json"]).read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["mode"], "img2table")
            self.assertTrue(Path(payload["output_json"]).name.endswith(".pdf.extraction.img2table.json"))
            self.assertEqual(report["mode"], "img2table")
            self.assertEqual(extract_mock.call_args.kwargs["mode"], "img2table")

    def test_inspect_document_analysis_command_prints_layered_provider_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            document_path = root / "saved.pdf"
            document_path.write_bytes(b"%PDF-1.4\nfake\n")

            class FakeProvider:
                def analyze(self, *, saved_document):
                    from project.documents import SavedDocumentAnalysis

                    self.last_saved_document = saved_document
                    return SavedDocumentAnalysis(
                        analysis_basis="pymupdf_text+pdfplumber_table",
                        extracted_lc_sc_number="LC-0038",
                        extracted_pi_number="PDL-26-0042",
                        extracted_amendment_number="5",
                    )

            provider = FakeProvider()
            buffer = io.StringIO()
            with patch("project.cli.LayeredSavedDocumentAnalysisProvider", return_value=provider):
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "inspect-document-analysis",
                            "--document-path",
                            str(document_path),
                        ]
                    )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["normalized_filename"], "saved.pdf")
        self.assertEqual(payload["analysis"]["analysis_basis"], "pymupdf_text+pdfplumber_table")
        self.assertEqual(payload["analysis"]["extracted_lc_sc_number"], "LC-0038")
        self.assertEqual(payload["analysis"]["extracted_pi_number"], "PDL-26-0042")
        self.assertEqual(payload["analysis"]["extracted_amendment_number"], "5")

    def test_inspect_document_analysis_command_uses_manifest_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            document_path = root / "saved.pdf"
            document_path.write_bytes(b"%PDF-1.4\nfake\n")
            manifest_path = root / "analysis.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "destination_path": str(document_path),
                            "extracted_pi_number": "PDL-26-0042",
                            "extracted_amendment_number": "05",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "inspect-document-analysis",
                        "--document-path",
                        str(document_path),
                        "--document-analysis-json",
                        str(manifest_path),
                    ]
                )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["analysis"]["analysis_basis"], "json_manifest")
        self.assertEqual(payload["analysis"]["extracted_pi_number"], "PDL-26-0042")
        self.assertEqual(payload["analysis"]["extracted_amendment_number"], "5")

    def test_inspect_outlook_folders_command_prints_folder_catalog(self) -> None:
        class FakeProvider:
            def list_folders(self, *, contains=None, max_depth=None):
                self.contains = contains
                self.max_depth = max_depth
                return [
                    {
                        "entry_id": "working-1",
                        "display_name": "Working",
                        "folder_path": "Mailbox - outlook / Inbox / Export / Working",
                        "depth": 3,
                        "store_name": "Mailbox - outlook",
                        "parent_entry_id": "export-1",
                    }
                ]

        buffer = io.StringIO()
        with patch("project.cli.Win32ComOutlookFolderCatalogProvider", return_value=FakeProvider()) as provider_mock:
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "inspect-outlook-folders",
                        "--outlook-profile",
                        "outlook",
                        "--contains",
                        "working",
                        "--max-depth",
                        "4",
                    ]
                )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["outlook_profile"], "outlook")
        self.assertEqual(payload["contains"], "working")
        self.assertEqual(payload["max_depth"], 4)
        self.assertEqual(payload["folder_count"], 1)
        self.assertEqual(payload["folders"][0]["entry_id"], "working-1")
        provider_mock.assert_called_once_with(outlook_profile="outlook")

    def test_inspect_mail_snapshot_command_prints_ordered_snapshot_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_json_path = root / "snapshot.json"
            snapshot_json_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "B",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "Second",
                            "sender_address": "b@example.com",
                            "attachments": [{"attachment_name": "b.pdf"}],
                        },
                        {
                            "entry_id": "A",
                            "received_time": "2026-03-28T02:59:59Z",
                            "subject_raw": "First",
                            "sender_address": "a@example.com",
                            "attachments": [{"attachment_name": "a.pdf"}],
                        },
                    ]
                ),
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "inspect-mail-snapshot",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                        "--snapshot-json",
                        str(snapshot_json_path),
                    ]
                )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["snapshot_source"], "json_manifest")
        self.assertEqual(payload["snapshot_count"], 2)
        self.assertEqual(payload["entry_id_order"], ["A", "B"])
        self.assertEqual(payload["attachment_count"], 2)
        self.assertEqual(payload["mails"][0]["attachments"][0]["attachment_name"], "a.pdf")

    def test_inspect_erp_command_prints_canonical_rows_for_requested_file_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            erp_json_path = root / "erp.json"
            erp_json_path.write_text(
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

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "inspect-erp",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                        "--erp-json",
                        str(erp_json_path),
                        "--file-number",
                        "P/26/42",
                    ]
                )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["erp_source"], "json_manifest")
        self.assertEqual(payload["canonical_file_numbers"], ["P/26/0042"])
        self.assertEqual(payload["match_count"], 1)
        self.assertEqual(payload["rows_by_file_number"]["P/26/0042"][0]["source_row_index"], 4)

    def test_inspect_erp_download_command_prints_debug_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with patch(
                "project.cli.inspect_playwright_report_download",
                return_value={
                    "status": "ready",
                    "output_dir": str(root / "reports" / "erp_debug" / "bundle"),
                    "downloaded_file_path": str(root / "reports" / "erp_debug" / "bundle" / "report.csv"),
                    "html_path": str(root / "reports" / "erp_debug" / "bundle" / "erp-page.html"),
                    "screenshot_path": str(root / "reports" / "erp_debug" / "bundle" / "erp-page.png"),
                    "error": None,
                },
            ) as inspect_mock:
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "inspect-erp-download",
                            "export_lc_sc",
                            "--config",
                            str(config_path),
                            "--fill",
                            "#fromDate=2026-03-01",
                            "--fill",
                            "#toDate=2026-03-31",
                            "--submit-selector",
                            "#show",
                            "--download-format-selector",
                            "text=CSV",
                            "--headed",
                        ]
                    )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(
            payload["downloaded_file_path"],
            str(root / "reports" / "erp_debug" / "bundle" / "report.csv"),
        )
        self.assertEqual(
            inspect_mock.call_args.kwargs["field_values"],
            [("#fromDate", "2026-03-01"), ("#toDate", "2026-03-31")],
        )
        self.assertEqual(inspect_mock.call_args.kwargs["submit_selector"], "#show")
        self.assertEqual(inspect_mock.call_args.kwargs["download_format_selector"], "text=CSV")
        self.assertEqual(inspect_mock.call_args.kwargs["headless"], False)

    def test_inspect_erp_download_command_uses_configured_fill_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        'erp_report_fill_values = ["#fromDate=2026-03-01", "#toDate=2026-03-31"]',
                        'erp_report_submit_selector = "#show"',
                        'erp_report_download_format_selector = "text=CSV"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with patch(
                "project.cli.inspect_playwright_report_download",
                return_value={"status": "ready", "error": None},
            ) as inspect_mock:
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "inspect-erp-download",
                            "export_lc_sc",
                            "--config",
                            str(config_path),
                        ]
                    )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(
            inspect_mock.call_args.kwargs["field_values"],
            [("#fromDate", "2026-03-01"), ("#toDate", "2026-03-31")],
        )
        self.assertEqual(inspect_mock.call_args.kwargs["submit_selector"], "#show")
        self.assertEqual(inspect_mock.call_args.kwargs["download_format_selector"], "text=CSV")

    def test_inspect_erp_download_command_defaults_to_live_download_flow_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with patch(
                "project.cli.inspect_playwright_report_download",
                return_value={"status": "ready", "error": None},
            ) as inspect_mock:
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "inspect-erp-download",
                            "export_lc_sc",
                            "--config",
                            str(config_path),
                        ]
                    )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(inspect_mock.call_args.kwargs["submit_selector"], 'role=button[name="Submit"]')
        self.assertEqual(inspect_mock.call_args.kwargs["post_submit_wait_selector"], ".dx-menu-item-popout")
        self.assertEqual(inspect_mock.call_args.kwargs["download_menu_selector"], ".dx-menu-item-popout")
        self.assertEqual(
            inspect_mock.call_args.kwargs["download_format_selector"],
            '.dxrd-preview-export-item-text:text-is("CSV")',
        )
        self.assertEqual(
            inspect_mock.call_args.kwargs["field_values"][0][0],
            ":nth-match(.dx-texteditor-input, 3)",
        )
        self.assertEqual(
            inspect_mock.call_args.kwargs["field_values"][1][0],
            ":nth-match(.dx-texteditor-input, 4)",
        )

    def test_prepare_document_verification_command_prints_bundle_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            run_report = RunReport(
                run_id="run-123",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                tool_version="0.1.0",
                rule_pack_id="export_lc_sc.default",
                rule_pack_version="1.0.0",
                started_at_utc="2026-03-29T00:00:00Z",
                completed_at_utc=None,
                state_timezone="Asia/Dhaka",
                mail_iteration_order=[],
                print_group_order=[],
                write_phase_status=WritePhaseStatus.NOT_STARTED,
                print_phase_status=PrintPhaseStatus.NOT_STARTED,
                mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
                hash_algorithm="sha256",
                run_start_backup_hash="a" * 64,
                current_workbook_hash="b" * 64,
                staged_write_plan_hash="c" * 64,
                summary={"pass": 0, "warning": 0, "hard_block": 0},
            )
            buffer = io.StringIO()
            with patch("project.cli.load_print_planning_bundle", return_value=(run_report, [], [])):
                with patch(
                    "project.cli.build_document_manual_verification_bundle",
                    return_value=DocumentManualVerificationResult(
                        bundle_path=str(root / "runs" / "export_lc_sc" / "run-123" / "document_manual_verification.json"),
                        audit_directory=str(root / "runs" / "export_lc_sc" / "run-123" / "document_audits"),
                        document_count=2,
                        audit_ready_count=2,
                        audit_error_count=0,
                        payload={"document_count": 2},
                    ),
                ):
                    with patch("project.cli.write_manual_document_verification") as write_mock:
                        with redirect_stdout(buffer):
                            exit_code = main(
                                [
                                    "prepare-document-verification",
                                    "export_lc_sc",
                                    "--config",
                                    str(config_path),
                                    "--run-id",
                                    "run-123",
                                    "--mode",
                                    "layered",
                                ]
                            )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["run_id"], "run-123")
        self.assertEqual(payload["document_count"], 2)
        self.assertEqual(payload["manual_verification_required"], True)
        self.assertEqual(write_mock.call_count, 1)

    def test_acknowledge_document_verification_command_prints_ack_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            buffer = io.StringIO()
            with patch(
                "project.cli.acknowledge_document_manual_verification",
                return_value=type(
                    "AckResult",
                    (),
                    {
                        "bundle_path": str(root / "runs" / "export_lc_sc" / "run-123" / "document_manual_verification.json"),
                        "acknowledged_document_count": 2,
                        "verified_document_count": 2,
                        "pending_document_count": 0,
                        "manual_verification_complete": True,
                        "payload": {"verified_document_count": 2},
                    },
                )(),
            ):
                with patch("project.cli.write_manual_document_verification") as write_mock:
                    with redirect_stdout(buffer):
                        exit_code = main(
                            [
                                "acknowledge-document-verification",
                                "export_lc_sc",
                                "--config",
                                str(config_path),
                                "--run-id",
                                "run-123",
                                "--saved-document-id",
                                "doc-1",
                                "--notes",
                                "Checked",
                            ]
                        )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["acknowledged_document_count"], 2)
        self.assertEqual(payload["manual_verification_complete"], True)
        self.assertEqual(write_mock.call_count, 1)

    def test_report_manual_verification_command_prints_run_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-123",
            )
            artifact_paths.manual_document_verification_path.write_text(
                json.dumps(
                    {
                        "run_id": "run-123",
                        "workflow_id": "export_lc_sc",
                        "manual_verification_required": True,
                        "document_count": 1,
                        "documents": [
                            {
                                "saved_document": {"saved_document_id": "doc-1"},
                                "manual_verification_status": "verified",
                                "audit_status": "ready",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            run_report = RunReport(
                run_id="run-123",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                tool_version="0.1.0",
                rule_pack_id="export_lc_sc.default",
                rule_pack_version="1.0.0",
                started_at_utc="2026-03-29T00:00:00Z",
                completed_at_utc=None,
                state_timezone="Asia/Dhaka",
                mail_iteration_order=["mail-1"],
                print_group_order=["group-1"],
                write_phase_status=WritePhaseStatus.COMMITTED,
                print_phase_status=PrintPhaseStatus.COMPLETED,
                mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
                hash_algorithm="sha256",
                run_start_backup_hash="a" * 64,
                current_workbook_hash="b" * 64,
                staged_write_plan_hash="c" * 64,
                summary={"pass": 1, "warning": 0, "hard_block": 0},
            )
            mail_outcomes = [
                MailOutcomeRecord(
                    run_id="run-123",
                    mail_id="mail-1",
                    workflow_id=WorkflowId.EXPORT_LC_SC,
                    snapshot_index=0,
                    processing_status=MailProcessingStatus.PRINTED,
                    final_decision=FinalDecision.PASS,
                    decision_reasons=[
                        "Skipped workbook append for P/26/0042 because the file number already exists in the workbook."
                    ],
                    eligible_for_write=False,
                    eligible_for_print=False,
                    eligible_for_mail_move=True,
                    source_entry_id="entry-1",
                    subject_raw="subject",
                    sender_address="a@example.com",
                    print_group_id="group-1",
                    manual_document_verification_summary={
                        "document_count": 1,
                        "verified_count": 1,
                        "pending_count": 0,
                        "untracked_count": 0,
                    },
                )
            ]

            buffer = io.StringIO()
            with patch(
                "project.cli.load_print_planning_bundle",
                return_value=(run_report, mail_outcomes, []),
            ):
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "report-manual-verification",
                            "export_lc_sc",
                            "--config",
                            str(config_path),
                            "--run-id",
                            "run-123",
                        ]
                    )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["run_id"], "run-123")
        self.assertEqual(payload["bundle"]["verified_document_count"], 1)
        self.assertEqual(payload["phases"]["planning"]["mail_count"], 1)
        self.assertEqual(payload["phases"]["printing"]["verified_count"], 1)

    def test_report_run_status_command_prints_compact_run_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-123",
            )
            artifact_paths.manual_document_verification_path.write_text(
                json.dumps(
                    {
                        "run_id": "run-123",
                        "workflow_id": "export_lc_sc",
                        "manual_verification_required": True,
                        "document_count": 1,
                        "documents": [
                            {
                                "saved_document": {"saved_document_id": "doc-1"},
                                "manual_verification_status": "verified",
                                "audit_status": "ready",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            artifact_paths.commit_marker_path.write_text('{"committed":true}\n', encoding="utf-8")
            artifact_paths.discrepancies_path.write_text('{"code":"x"}\n', encoding="utf-8")
            run_report = RunReport(
                run_id="run-123",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                tool_version="0.1.0",
                rule_pack_id="export_lc_sc.default",
                rule_pack_version="1.0.0",
                started_at_utc="2026-03-29T00:00:00Z",
                completed_at_utc=None,
                state_timezone="Asia/Dhaka",
                mail_iteration_order=["mail-1"],
                print_group_order=["group-1"],
                write_phase_status=WritePhaseStatus.COMMITTED,
                print_phase_status=PrintPhaseStatus.COMPLETED,
                mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
                hash_algorithm="sha256",
                run_start_backup_hash="a" * 64,
                current_workbook_hash="b" * 64,
                staged_write_plan_hash="c" * 64,
                summary={"pass": 1, "warning": 0, "hard_block": 0},
            )
            mail_outcomes = [
                MailOutcomeRecord(
                    run_id="run-123",
                    mail_id="mail-1",
                    workflow_id=WorkflowId.EXPORT_LC_SC,
                    snapshot_index=0,
                    processing_status=MailProcessingStatus.PRINTED,
                    final_decision=FinalDecision.PASS,
                    decision_reasons=[
                        "Skipped workbook append for P/26/0042 because the file number already exists in the workbook."
                    ],
                    eligible_for_write=False,
                    eligible_for_print=False,
                    eligible_for_mail_move=True,
                    source_entry_id="entry-1",
                    subject_raw="subject",
                    sender_address="a@example.com",
                    print_group_id="group-1",
                    staged_write_operations=[{"write_operation_id": "op-1"}],
                    manual_document_verification_summary={
                        "document_count": 1,
                        "verified_count": 1,
                        "pending_count": 0,
                        "untracked_count": 0,
                    },
                )
            ]
            staged_write_plan = [
                WriteOperation(
                    write_operation_id="op-1",
                    run_id="run-123",
                    mail_id="mail-1",
                    operation_index_within_mail=0,
                    sheet_name="Sheet1",
                    row_index=3,
                    column_key="file_no",
                    expected_pre_write_value=None,
                    expected_post_write_value="P/26/0042",
                    row_eligibility_checks=[],
                )
            ]

            buffer = io.StringIO()
            with patch(
                "project.cli.load_print_planning_bundle",
                return_value=(run_report, mail_outcomes, staged_write_plan),
            ):
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "report-run-status",
                            "export_lc_sc",
                            "--config",
                            str(config_path),
                            "--run-id",
                            "run-123",
                        ]
                    )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["run_id"], "run-123")
        self.assertEqual(payload["phases"]["write"]["status"], "committed")
        self.assertEqual(payload["phases"]["write"]["staged_write_operation_count"], 1)
        self.assertTrue(payload["phases"]["write"]["commit_marker_present"])
        self.assertEqual(payload["phases"]["print"]["planned_group_count"], 1)
        self.assertEqual(payload["manual_verification"]["bundle"]["verified_document_count"], 1)
        self.assertEqual(payload["duplicate_summary"]["duplicate_file_skip_count"], 1)
        self.assertEqual(payload["duplicate_summary"]["duplicate_in_workbook_file_count"], 1)
        self.assertEqual(payload["write_disposition_counts"]["mixed_duplicate_and_new_writes"], 1)

    def test_explain_run_failure_command_prints_primary_causes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-123",
            )
            artifact_paths.discrepancies_path.write_text(
                json.dumps(
                    {
                        "run_id": "run-123",
                        "workflow_id": "export_lc_sc",
                        "severity": "hard_block",
                        "code": "workbook_target_prevalidation_failed",
                        "message": "Staged workbook target failed prevalidation against the live workbook snapshot.",
                        "mail_id": "mail-1",
                        "details": {
                            "row_index": 17,
                            "column_key": "bangladesh_bank_ref",
                            "column_index": 33,
                            "observed_value": "2300050126270133",
                            "expected_post_write_value": "2300050126270133",
                            "failure_reason": "row_eligibility_failed",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            artifact_paths.target_probes_path.write_text("", encoding="utf-8")
            run_report = RunReport(
                run_id="run-123",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                tool_version="0.1.0",
                rule_pack_id="export_lc_sc.default",
                rule_pack_version="1.0.0",
                started_at_utc="2026-03-29T00:00:00Z",
                completed_at_utc=None,
                state_timezone="Asia/Dhaka",
                mail_iteration_order=["mail-1"],
                print_group_order=[],
                write_phase_status=WritePhaseStatus.HARD_BLOCKED_NO_WRITE,
                print_phase_status=PrintPhaseStatus.NOT_STARTED,
                mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
                hash_algorithm="sha256",
                run_start_backup_hash="a" * 64,
                current_workbook_hash="b" * 64,
                staged_write_plan_hash="c" * 64,
                summary={"pass": 1, "warning": 0, "hard_block": 0},
            )
            mail_outcomes = [
                MailOutcomeRecord(
                    run_id="run-123",
                    mail_id="mail-1",
                    workflow_id=WorkflowId.EXPORT_LC_SC,
                    snapshot_index=0,
                    processing_status=MailProcessingStatus.VALIDATED,
                    final_decision=FinalDecision.PASS,
                    decision_reasons=[],
                    eligible_for_write=False,
                    eligible_for_print=False,
                    eligible_for_mail_move=False,
                    source_entry_id="entry-1",
                    subject_raw="IP-LC-2925-L-KENPARK BANGLADESH APPAREL PRIVATE LTD",
                    sender_address="a@example.com",
                    write_disposition="new_writes_staged",
                )
            ]
            staged_write_plan = [
                WriteOperation(
                    write_operation_id="op-file",
                    run_id="run-123",
                    mail_id="mail-1",
                    operation_index_within_mail=0,
                    sheet_name="Sheet1",
                    row_index=17,
                    column_key="file_no",
                    expected_pre_write_value=None,
                    expected_post_write_value="P/26/0669",
                    row_eligibility_checks=[],
                )
            ]

            buffer = io.StringIO()
            with patch(
                "project.cli.load_print_planning_bundle",
                return_value=(run_report, mail_outcomes, staged_write_plan),
            ):
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "explain-run-failure",
                            "export_lc_sc",
                            "--config",
                            str(config_path),
                            "--run-id",
                            "run-123",
                        ]
                    )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["overall_status"], "attention_required")
        self.assertEqual(payload["primary_cause_count"], 1)
        cause = payload["primary_causes"][0]
        self.assertEqual(cause["category"], "workbook_prevalidation")
        self.assertEqual(cause["workbook_target"]["row_index"], 17)
        self.assertEqual(cause["workbook_target"]["file_number"], "P/26/0669")
        self.assertIn("append row", cause["operator_hint"])

    def test_list_runs_command_prints_recent_run_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            run_dir = root / "runs" / "export_lc_sc" / "run-123"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-123",
                        "started_at_utc": "2026-03-30T00:00:00Z",
                        "completed_at_utc": None,
                        "write_phase_status": "committed",
                        "print_phase_status": "planned",
                        "mail_move_phase_status": "not_started",
                        "summary": {"pass": 1, "warning": 0, "hard_block": 0},
                        "print_group_order": ["group-1"],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "document_manual_verification.json").write_text(
                json.dumps({"pending_document_count": 0, "manual_verification_complete": True}),
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "list-runs",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                        "--limit",
                        "5",
                    ]
                )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["run_count"], 1)
        self.assertEqual(payload["runs"][0]["run_id"], "run-123")
        self.assertEqual(payload["runs"][0]["write_phase_status"], "committed")
        self.assertEqual(payload["runs"][0]["manual_verification_complete"], True)

    def test_report_run_artifacts_command_prints_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-123",
            )
            artifact_paths.run_metadata_path.write_text('{"run_id":"run-123"}\n', encoding="utf-8")
            artifact_paths.commit_marker_path.write_text('{"committed":true}\n', encoding="utf-8")
            (artifact_paths.print_markers_dir / "group-1.json").write_text("{}", encoding="utf-8")

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "report-run-artifacts",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                        "--run-id",
                        "run-123",
                    ]
                )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["run_id"], "run-123")
        self.assertTrue(payload["artifacts"]["core_files"]["run_metadata"]["exists"])
        self.assertTrue(payload["artifacts"]["core_files"]["commit_marker"]["nonempty"])
        self.assertEqual(payload["artifacts"]["directories"]["print_markers"]["file_count"], 1)

    def test_report_print_markers_command_prints_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-123",
            )
            (artifact_paths.print_markers_dir / "group-1.json").write_text(
                """
                {
                  "print_group_id": "group-1",
                  "mail_id": "mail-1",
                  "completion_marker_id": "completion-1",
                  "printed_at_utc": "2026-03-30T00:00:00Z",
                  "manual_verification_summary": {"verified_count": 1},
                  "print_execution_receipt": {
                    "adapter_name": "acrobat",
                    "acknowledgment_mode": "ole_silent_submission",
                    "executed_command_count": 2,
                    "blank_separator_printed": true,
                    "command_receipts": [
                      {"acknowledgment_mode": "ole_avdoc_silent_submission"}
                    ]
                  }
                }
                """,
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "report-print-markers",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                        "--run-id",
                        "run-123",
                    ]
                )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["run_id"], "run-123")
        self.assertEqual(payload["print_markers"]["marker_count"], 1)
        self.assertEqual(payload["print_markers"]["markers"][0]["adapter_name"], "acrobat")
        self.assertEqual(
            payload["print_markers"]["markers"][0]["acknowledgment_mode"],
            "ole_silent_submission",
        )
        self.assertEqual(
            payload["print_markers"]["markers"][0]["submission_modes"],
            ["ole_avdoc_silent_submission"],
        )
        self.assertEqual(
            payload["print_markers"]["submission_modes"],
            ["ole_avdoc_silent_submission"],
        )

    def test_report_mail_move_markers_command_prints_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-123",
            )
            (
                artifact_paths.mail_move_markers_dir / "move-1.json"
            ).write_text(
                """
                {
                  "mail_move_operation_id": "move-1",
                  "mail_id": "mail-1",
                  "entry_id": "entry-1",
                  "source_folder": "src-folder",
                  "destination_folder": "dst-folder",
                  "move_status": "moved",
                  "moved_at_utc": "2026-03-30T00:00:00Z",
                  "manual_verification_summary": {"verified_count": 1},
                  "move_execution_receipt": {
                    "adapter_name": "win32com_outlook",
                    "acknowledgment_mode": "parent_folder_entry_id_verification",
                    "acknowledged_source_folder": "src-folder",
                    "acknowledged_destination_folder": "dst-folder"
                  }
                }
                """,
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "report-mail-move-markers",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                        "--run-id",
                        "run-123",
                    ]
                )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["run_id"], "run-123")
        self.assertEqual(payload["mail_move_markers"]["marker_count"], 1)
        self.assertEqual(
            payload["mail_move_markers"]["markers"][0]["adapter_name"],
            "win32com_outlook",
        )
        self.assertEqual(
            payload["mail_move_markers"]["markers"][0]["acknowledgment_mode"],
            "parent_folder_entry_id_verification",
        )

    def test_report_transport_execution_command_prints_combined_phase_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-123",
            )
            (artifact_paths.print_markers_dir / "group-1.json").write_text(
                """
                {
                  "print_group_id": "group-1",
                  "mail_id": "mail-1",
                  "completion_marker_id": "completion-1",
                  "printed_at_utc": "2026-03-30T00:00:00Z",
                  "manual_verification_summary": {"verified_count": 1},
                  "print_execution_receipt": {
                    "adapter_name": "acrobat",
                    "acknowledgment_mode": "process_exit_zero",
                    "executed_command_count": 2,
                    "blank_separator_printed": true
                  }
                }
                """,
                encoding="utf-8",
            )
            (artifact_paths.mail_move_markers_dir / "move-1.json").write_text(
                """
                {
                  "mail_move_operation_id": "move-1",
                  "mail_id": "mail-1",
                  "entry_id": "entry-1",
                  "source_folder": "src-folder",
                  "destination_folder": "dst-folder",
                  "move_status": "moved",
                  "moved_at_utc": "2026-03-30T00:05:00Z",
                  "manual_verification_summary": {"verified_count": 1},
                  "move_execution_receipt": {
                    "adapter_name": "win32com_outlook",
                    "acknowledgment_mode": "parent_folder_entry_id_verification",
                    "acknowledged_source_folder": "src-folder",
                    "acknowledged_destination_folder": "dst-folder"
                  }
                }
                """,
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "report-transport-execution",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                        "--run-id",
                        "run-123",
                    ]
                )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["run_id"], "run-123")
        self.assertEqual(payload["transport_execution"]["summary_counts"]["print_marker_count"], 1)
        self.assertEqual(payload["transport_execution"]["summary_counts"]["mail_move_marker_count"], 1)
        self.assertEqual(payload["transport_execution"]["adapter_summary"]["print_adapters"], ["acrobat"])
        self.assertEqual(
            payload["transport_execution"]["adapter_summary"]["mail_move_adapters"],
            ["win32com_outlook"],
        )

    def test_report_recovery_precheck_command_prints_readiness_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-123",
            )
            artifact_paths.run_metadata_path.write_text('{"run_id":"run-123"}\n', encoding="utf-8")
            artifact_paths.staged_write_plan_path.write_text('[]\n', encoding="utf-8")
            artifact_paths.backup_workbook_path.write_bytes(b"fake workbook")
            artifact_paths.backup_hash_path.write_text("abcd\n", encoding="utf-8")
            run_report = RunReport(
                run_id="run-123",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                tool_version="0.1.0",
                rule_pack_id="export_lc_sc.default",
                rule_pack_version="1.0.0",
                started_at_utc="2026-03-29T00:00:00Z",
                completed_at_utc=None,
                state_timezone="Asia/Dhaka",
                mail_iteration_order=["mail-1"],
                print_group_order=[],
                write_phase_status=WritePhaseStatus.UNCERTAIN_NOT_COMMITTED,
                print_phase_status=PrintPhaseStatus.NOT_STARTED,
                mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
                hash_algorithm="sha256",
                run_start_backup_hash="a" * 64,
                current_workbook_hash="b" * 64,
                staged_write_plan_hash="c" * 64,
                summary={"pass": 1, "warning": 0, "hard_block": 0},
            )

            buffer = io.StringIO()
            with patch(
                "project.cli.load_print_planning_bundle",
                return_value=(run_report, [], []),
            ):
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "report-recovery-precheck",
                            "export_lc_sc",
                            "--config",
                            str(config_path),
                            "--run-id",
                            "run-123",
                        ]
                    )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["run_id"], "run-123")
        self.assertTrue(payload["precheck"]["needs_recovery_gate"])
        self.assertTrue(payload["precheck"]["can_attempt_recovery_assessment"])
        self.assertEqual(payload["precheck"]["phase_statuses"]["write_phase_status"], "uncertain_not_committed")

    def test_list_recovery_candidates_command_prints_filtered_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            clean_run_dir = root / "runs" / "export_lc_sc" / "run-clean"
            uncertain_run_dir = root / "runs" / "export_lc_sc" / "run-uncertain"
            clean_run_dir.mkdir(parents=True, exist_ok=True)
            uncertain_run_dir.mkdir(parents=True, exist_ok=True)
            (clean_run_dir / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-clean",
                        "started_at_utc": "2026-03-29T00:00:00Z",
                        "write_phase_status": "committed",
                        "print_phase_status": "completed",
                        "mail_move_phase_status": "completed",
                        "summary": {},
                        "print_group_order": [],
                    }
                ),
                encoding="utf-8",
            )
            (uncertain_run_dir / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-uncertain",
                        "started_at_utc": "2026-03-30T00:00:00Z",
                        "write_phase_status": "uncertain_not_committed",
                        "print_phase_status": "not_started",
                        "mail_move_phase_status": "not_started",
                        "summary": {},
                        "print_group_order": [],
                    }
                ),
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "list-recovery-candidates",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                        "--limit",
                        "5",
                    ]
                )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["run_count"], 1)
        self.assertEqual(payload["runs"][0]["run_id"], "run-uncertain")

    def test_list_run_handoffs_command_prints_recent_packets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            (root / "reports" / "run_handoffs").mkdir(parents=True, exist_ok=True)
            (root / "reports" / "run_handoffs" / "export_lc_sc.run-123.handoff.json").write_text(
                "{}",
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "list-run-handoffs",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                        "--limit",
                        "5",
                    ]
                )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["handoff_count"], 1)
        self.assertEqual(payload["total_handoff_count"], 1)
        self.assertEqual(payload["run_handoffs"][0]["run_id"], "run-123")

    def test_list_workflow_handoffs_command_prints_packets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            (root / "reports" / "workflow_handoffs").mkdir(parents=True, exist_ok=True)
            (root / "reports" / "workflow_handoffs" / "export_lc_sc.handoff.json").write_text(
                "{}",
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "list-workflow-handoffs",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                    ]
                )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["handoff_count"], 1)
        self.assertEqual(payload["workflow_handoffs"][0]["artifact_type"], "workflow_handoff")

    def test_report_operator_queue_command_prints_actionable_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            recovery_run_dir = root / "runs" / "export_lc_sc" / "run-recovery"
            manual_run_dir = root / "runs" / "export_lc_sc" / "run-manual"
            recovery_run_dir.mkdir(parents=True, exist_ok=True)
            manual_run_dir.mkdir(parents=True, exist_ok=True)
            (recovery_run_dir / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-recovery",
                        "started_at_utc": "2026-03-30T00:00:00Z",
                        "write_phase_status": "uncertain_not_committed",
                        "print_phase_status": "not_started",
                        "mail_move_phase_status": "not_started",
                        "summary": {},
                        "print_group_order": [],
                    }
                ),
                encoding="utf-8",
            )
            (manual_run_dir / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-manual",
                        "started_at_utc": "2026-03-31T00:00:00Z",
                        "write_phase_status": "committed",
                        "print_phase_status": "completed",
                        "mail_move_phase_status": "completed",
                        "summary": {},
                        "print_group_order": [],
                    }
                ),
                encoding="utf-8",
            )
            (manual_run_dir / "document_manual_verification.json").write_text(
                json.dumps(
                    {
                        "manual_verification_complete": False,
                        "pending_document_count": 1,
                    }
                ),
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "report-operator-queue",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                        "--limit",
                        "5",
                    ]
                )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["queue_count"], 2)
        self.assertEqual(payload["runs"][0]["run_id"], "run-recovery")
        self.assertEqual(payload["runs"][0]["queue_priority"], "recovery")
        self.assertEqual(payload["runs"][1]["run_id"], "run-manual")
        self.assertEqual(payload["runs"][1]["queue_priority"], "manual_verification")

    def test_export_workflow_summary_command_writes_default_json_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            run_dir = root / "runs" / "export_lc_sc" / "run-123"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-123",
                        "started_at_utc": "2026-03-30T00:00:00Z",
                        "write_phase_status": "uncertain_not_committed",
                        "print_phase_status": "not_started",
                        "mail_move_phase_status": "not_started",
                        "summary": {},
                        "print_group_order": [],
                    }
                ),
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "export-workflow-summary",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                    ]
                )

            payload = json.loads(buffer.getvalue())
            output_path = Path(payload["output_json"])
            written = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertTrue(output_path.name.endswith("export_lc_sc.summary.json"))
        self.assertEqual(written["workflow_id"], "export_lc_sc")
        self.assertEqual(written["operator_queue"]["queue_count"], 1)

    def test_export_workflow_handoff_command_writes_default_json_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            run_dir = root / "runs" / "export_lc_sc" / "run-123"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-123",
                        "workflow_id": "export_lc_sc",
                        "tool_version": "0.1.0",
                        "rule_pack_id": "export_lc_sc.default",
                        "rule_pack_version": "1.0.0",
                        "started_at_utc": "2026-03-30T00:00:00Z",
                        "completed_at_utc": None,
                        "state_timezone": "Asia/Dhaka",
                        "mail_iteration_order": [],
                        "print_group_order": [],
                        "write_phase_status": "uncertain_not_committed",
                        "print_phase_status": "not_started",
                        "mail_move_phase_status": "not_started",
                        "hash_algorithm": "sha256",
                        "run_start_backup_hash": "a" * 64,
                        "current_workbook_hash": "b" * 64,
                        "staged_write_plan_hash": "c" * 64,
                        "summary": {},
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "mail_outcomes.jsonl").write_text("", encoding="utf-8")
            (run_dir / "staged_write_plan.json").write_text("[]\n", encoding="utf-8")
            backup_dir = root / "backups" / "export_lc_sc" / "run-123"
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / "master_workbook_backup.xlsx").write_bytes(b"fake")
            (backup_dir / "backup_hash.txt").write_text("abcd\n", encoding="utf-8")
            (root / "reports" / "run_handoffs").mkdir(parents=True, exist_ok=True)
            (root / "reports" / "run_handoffs" / "export_lc_sc.run-123.handoff.json").write_text(
                "{}",
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "export-workflow-handoff",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                    ]
                )

            payload = json.loads(buffer.getvalue())
            output_path = Path(payload["output_json"])
            written = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertTrue(output_path.name.endswith("export_lc_sc.handoff.json"))
        self.assertEqual(written["workflow_id"], "export_lc_sc")
        self.assertEqual(written["summary_counts"]["recent_handoff_count"], 1)
        self.assertIn("workflow_summary", written["workflow_handoff"])

    def test_export_run_summary_command_writes_default_json_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-123",
            )
            artifact_paths.run_metadata_path.write_text('{"run_id":"run-123"}\n', encoding="utf-8")
            artifact_paths.staged_write_plan_path.write_text("[]\n", encoding="utf-8")
            artifact_paths.backup_workbook_path.write_bytes(b"fake workbook")
            artifact_paths.backup_hash_path.write_text("abcd\n", encoding="utf-8")
            run_report = RunReport(
                run_id="run-123",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                tool_version="0.1.0",
                rule_pack_id="export_lc_sc.default",
                rule_pack_version="1.0.0",
                started_at_utc="2026-03-30T00:00:00Z",
                completed_at_utc=None,
                state_timezone="Asia/Dhaka",
                mail_iteration_order=["mail-1"],
                print_group_order=[],
                write_phase_status=WritePhaseStatus.UNCERTAIN_NOT_COMMITTED,
                print_phase_status=PrintPhaseStatus.NOT_STARTED,
                mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
                hash_algorithm="sha256",
                run_start_backup_hash="a" * 64,
                current_workbook_hash="b" * 64,
                staged_write_plan_hash="c" * 64,
                summary={"pass": 1, "warning": 0, "hard_block": 0},
            )

            buffer = io.StringIO()
            with patch(
                "project.cli.load_print_planning_bundle",
                return_value=(run_report, [], []),
            ):
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "export-run-summary",
                            "export_lc_sc",
                            "--config",
                            str(config_path),
                            "--run-id",
                            "run-123",
                        ]
                    )

            payload = json.loads(buffer.getvalue())
            output_path = Path(payload["output_json"])
            written = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["run_id"], "run-123")
        self.assertTrue(output_path.name.endswith("export_lc_sc.run-123.summary.json"))
        self.assertEqual(written["run_id"], "run-123")
        self.assertTrue(written["recovery_precheck"]["needs_recovery_gate"])

    def test_export_run_handoff_command_writes_default_json_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-123",
            )
            artifact_paths.run_metadata_path.write_text('{"run_id":"run-123"}\n', encoding="utf-8")
            artifact_paths.staged_write_plan_path.write_text("[]\n", encoding="utf-8")
            artifact_paths.backup_workbook_path.write_bytes(b"fake workbook")
            artifact_paths.backup_hash_path.write_text("abcd\n", encoding="utf-8")
            (artifact_paths.print_markers_dir / "group-1.json").write_text(
                '{"print_group_id":"group-1","print_execution_receipt":{"adapter_name":"acrobat"}}',
                encoding="utf-8",
            )
            (artifact_paths.mail_move_markers_dir / "move-1.json").write_text(
                '{"mail_move_operation_id":"move-1","move_execution_receipt":{"adapter_name":"win32com_outlook"}}',
                encoding="utf-8",
            )
            run_report = RunReport(
                run_id="run-123",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                tool_version="0.1.0",
                rule_pack_id="export_lc_sc.default",
                rule_pack_version="1.0.0",
                started_at_utc="2026-03-30T00:00:00Z",
                completed_at_utc=None,
                state_timezone="Asia/Dhaka",
                mail_iteration_order=["mail-1"],
                print_group_order=["group-1"],
                write_phase_status=WritePhaseStatus.COMMITTED,
                print_phase_status=PrintPhaseStatus.COMPLETED,
                mail_move_phase_status=MailMovePhaseStatus.COMPLETED,
                hash_algorithm="sha256",
                run_start_backup_hash="a" * 64,
                current_workbook_hash="b" * 64,
                staged_write_plan_hash="c" * 64,
                summary={"pass": 1, "warning": 0, "hard_block": 0},
            )

            buffer = io.StringIO()
            with patch(
                "project.cli.load_print_planning_bundle",
                return_value=(run_report, [], []),
            ):
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "export-run-handoff",
                            "export_lc_sc",
                            "--config",
                            str(config_path),
                            "--run-id",
                            "run-123",
                        ]
                    )

            payload = json.loads(buffer.getvalue())
            output_path = Path(payload["output_json"])
            written = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["run_id"], "run-123")
        self.assertTrue(output_path.name.endswith("export_lc_sc.run-123.handoff.json"))
        self.assertEqual(written["run_id"], "run-123")
        self.assertEqual(written["transport_execution"]["summary_counts"]["print_marker_count"], 1)
        self.assertEqual(written["transport_execution"]["summary_counts"]["mail_move_marker_count"], 1)

    def test_export_recovery_packet_command_writes_default_json_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            run_dir = root / "runs" / "export_lc_sc" / "run-123"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-123",
                        "workflow_id": "export_lc_sc",
                        "tool_version": "0.1.0",
                        "rule_pack_id": "export_lc_sc.default",
                        "rule_pack_version": "1.0.0",
                        "started_at_utc": "2026-03-30T00:00:00Z",
                        "completed_at_utc": None,
                        "state_timezone": "Asia/Dhaka",
                        "mail_iteration_order": [],
                        "print_group_order": [],
                        "write_phase_status": "uncertain_not_committed",
                        "print_phase_status": "not_started",
                        "mail_move_phase_status": "not_started",
                        "hash_algorithm": "sha256",
                        "run_start_backup_hash": "a" * 64,
                        "current_workbook_hash": "b" * 64,
                        "staged_write_plan_hash": "c" * 64,
                        "summary": {},
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "mail_outcomes.jsonl").write_text("", encoding="utf-8")
            (run_dir / "staged_write_plan.json").write_text("[]\n", encoding="utf-8")
            backup_dir = root / "backups" / "export_lc_sc" / "run-123"
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / "master_workbook_backup.xlsx").write_bytes(b"fake")
            (backup_dir / "backup_hash.txt").write_text("abcd\n", encoding="utf-8")

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "export-recovery-packet",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                    ]
                )

            payload = json.loads(buffer.getvalue())
            output_path = Path(payload["output_json"])
            written = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertTrue(output_path.name.endswith("export_lc_sc.recovery.json"))
        self.assertEqual(written["workflow_id"], "export_lc_sc")
        self.assertEqual(written["candidate_count"], 1)

    def test_report_retention_candidates_command_prints_stale_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            run_dir = root / "runs" / "export_lc_sc" / "run-old"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-old",
                        "started_at_utc": "2026-01-01T00:00:00Z",
                        "write_phase_status": "committed",
                        "print_phase_status": "completed",
                        "mail_move_phase_status": "completed",
                        "summary": {},
                        "print_group_order": [],
                    }
                ),
                encoding="utf-8",
            )
            workflow_summary = root / "reports" / "workflow_summaries" / "export_lc_sc.summary.json"
            workflow_summary.parent.mkdir(parents=True, exist_ok=True)
            workflow_summary.write_text("{}", encoding="utf-8")
            old_timestamp = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
            os.utime(workflow_summary, (old_timestamp, old_timestamp))

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "report-retention-candidates",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                        "--older-than-days",
                        "30",
                    ]
                )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["summary_counts"]["stale_run_count"], 1)
        self.assertEqual(payload["stale_runs"][0]["run_id"], "run-old")

    def test_export_retention_summary_command_writes_default_json_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            run_dir = root / "runs" / "export_lc_sc" / "run-old"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-old",
                        "started_at_utc": "2026-01-01T00:00:00Z",
                        "write_phase_status": "committed",
                        "print_phase_status": "completed",
                        "mail_move_phase_status": "completed",
                        "summary": {},
                        "print_group_order": [],
                    }
                ),
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "export-retention-summary",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                        "--older-than-days",
                        "30",
                    ]
                )

            payload = json.loads(buffer.getvalue())
            output_path = Path(payload["output_json"])
            written = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertTrue(output_path.name.endswith("export_lc_sc.retention.json"))
        self.assertEqual(written["workflow_id"], "export_lc_sc")
        self.assertIn("retention_report", written)

    def test_export_summary_catalog_command_writes_default_json_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            (root / "reports" / "workflow_summaries").mkdir(parents=True, exist_ok=True)
            (root / "reports" / "run_summaries").mkdir(parents=True, exist_ok=True)
            (root / "reports" / "run_handoffs").mkdir(parents=True, exist_ok=True)
            (root / "reports" / "recovery_packets").mkdir(parents=True, exist_ok=True)
            (root / "reports" / "retention_reports").mkdir(parents=True, exist_ok=True)
            (root / "reports" / "workflow_summaries" / "export_lc_sc.summary.json").write_text("{}", encoding="utf-8")
            (root / "reports" / "run_summaries" / "export_lc_sc.run-123.summary.json").write_text("{}", encoding="utf-8")
            (root / "reports" / "run_handoffs" / "export_lc_sc.run-123.handoff.json").write_text("{}", encoding="utf-8")

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "export-summary-catalog",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                    ]
                )

            payload = json.loads(buffer.getvalue())
            output_path = Path(payload["output_json"])
            written = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertTrue(output_path.name.endswith("export_lc_sc.catalog.json"))
        self.assertEqual(written["workflow_id"], "export_lc_sc")
        self.assertEqual(written["summary_counts"]["run_summary_count"], 1)
        self.assertEqual(written["summary_counts"]["run_handoff_count"], 1)

    def test_export_dashboard_markdown_command_writes_default_markdown_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            run_dir = root / "runs" / "export_lc_sc" / "run-123"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-123",
                        "workflow_id": "export_lc_sc",
                        "tool_version": "0.1.0",
                        "rule_pack_id": "export_lc_sc.default",
                        "rule_pack_version": "1.0.0",
                        "started_at_utc": "2026-03-30T00:00:00Z",
                        "completed_at_utc": None,
                        "state_timezone": "Asia/Dhaka",
                        "mail_iteration_order": [],
                        "print_group_order": [],
                        "write_phase_status": "uncertain_not_committed",
                        "print_phase_status": "not_started",
                        "mail_move_phase_status": "not_started",
                        "hash_algorithm": "sha256",
                        "run_start_backup_hash": "a" * 64,
                        "current_workbook_hash": "b" * 64,
                        "staged_write_plan_hash": "c" * 64,
                        "summary": {},
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "mail_outcomes.jsonl").write_text("", encoding="utf-8")
            (run_dir / "staged_write_plan.json").write_text("[]\n", encoding="utf-8")
            backup_dir = root / "backups" / "export_lc_sc" / "run-123"
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / "master_workbook_backup.xlsx").write_bytes(b"fake")
            (backup_dir / "backup_hash.txt").write_text("abcd\n", encoding="utf-8")

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "export-dashboard-markdown",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                    ]
                )

            payload = json.loads(buffer.getvalue())
            output_path = Path(payload["output_markdown"])
            written = output_path.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertTrue(output_path.name.endswith("export_lc_sc.dashboard.md"))
        self.assertIn("# Workflow Dashboard: export_lc_sc", written)
        self.assertIn("## Operator Queue", written)

    def test_export_dashboard_html_command_writes_default_html_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            run_dir = root / "runs" / "export_lc_sc" / "run-123"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-123",
                        "workflow_id": "export_lc_sc",
                        "tool_version": "0.1.0",
                        "rule_pack_id": "export_lc_sc.default",
                        "rule_pack_version": "1.0.0",
                        "started_at_utc": "2026-03-30T00:00:00Z",
                        "completed_at_utc": None,
                        "state_timezone": "Asia/Dhaka",
                        "mail_iteration_order": [],
                        "print_group_order": [],
                        "write_phase_status": "uncertain_not_committed",
                        "print_phase_status": "not_started",
                        "mail_move_phase_status": "not_started",
                        "hash_algorithm": "sha256",
                        "run_start_backup_hash": "a" * 64,
                        "current_workbook_hash": "b" * 64,
                        "staged_write_plan_hash": "c" * 64,
                        "summary": {},
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "mail_outcomes.jsonl").write_text("", encoding="utf-8")
            (run_dir / "staged_write_plan.json").write_text("[]\n", encoding="utf-8")
            backup_dir = root / "backups" / "export_lc_sc" / "run-123"
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / "master_workbook_backup.xlsx").write_bytes(b"fake")
            (backup_dir / "backup_hash.txt").write_text("abcd\n", encoding="utf-8")

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "export-dashboard-html",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                    ]
                )

            payload = json.loads(buffer.getvalue())
            output_path = Path(payload["output_html"])
            written = output_path.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertTrue(output_path.name.endswith("export_lc_sc.dashboard.html"))
        self.assertIn("<h1>Workflow Dashboard: export_lc_sc</h1>", written)
        self.assertIn("<h2>Operator Queue</h2>", written)

    def test_inspect_workbook_command_uses_live_snapshot_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )

            fake_snapshot = type(
                "FakeSnapshot",
                (),
                {
                    "sheet_name": "Sheet1",
                    "headers": [WorkbookHeader(column_index=1, text="File No.")],
                    "rows": [],
                },
            )()

            buffer = io.StringIO()
            with patch("project.cli._load_workbook_snapshot", return_value=fake_snapshot):
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "inspect-workbook",
                            "export_lc_sc",
                            "--config",
                            str(config_path),
                            "--live-workbook",
                        ]
                    )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["sheet_name"], "Sheet1")
        self.assertEqual(payload["header_count"], 1)

    def test_inspect_workbook_command_reports_expected_yearly_workbook_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )

            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                exit_code = main(
                    [
                        "inspect-workbook",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                        "--live-workbook",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout_buffer.getvalue(), "")
        self.assertIn(str(root / "workbooks" / f"{workflow_year}-master.xlsx"), stderr_buffer.getvalue())
        self.assertIn("Place the real yearly workbook", stderr_buffer.getvalue())

    def test_inspect_workbook_readiness_command_reports_mapping_and_prevalidation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )

            fake_snapshot = type(
                "FakeSnapshot",
                (),
                {
                    "sheet_name": "Sheet1",
                    "headers": [
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
                    "rows": [],
                },
            )()
            staged_write_plan = [
                WriteOperation(
                    write_operation_id="op-1",
                    run_id="run-123",
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

            buffer = io.StringIO()
            with patch("project.cli._load_workbook_snapshot", return_value=fake_snapshot):
                with patch(
                    "project.cli.load_print_planning_bundle",
                    return_value=(
                        RunReport(
                            run_id="run-123",
                            workflow_id=WorkflowId.EXPORT_LC_SC,
                            tool_version="0.1.0",
                            rule_pack_id="export_lc_sc.default",
                            rule_pack_version="1.0.0",
                            started_at_utc="2026-03-30T00:00:00Z",
                            completed_at_utc=None,
                            state_timezone="Asia/Dhaka",
                            mail_iteration_order=["mail-1"],
                            print_group_order=[],
                            write_phase_status=WritePhaseStatus.NOT_STARTED,
                            print_phase_status=PrintPhaseStatus.NOT_STARTED,
                            mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
                            hash_algorithm="sha256",
                            run_start_backup_hash="a" * 64,
                            current_workbook_hash="b" * 64,
                            staged_write_plan_hash="c" * 64,
                            summary={},
                        ),
                        [],
                        staged_write_plan,
                    ),
                ):
                    with redirect_stdout(buffer):
                        exit_code = main(
                            [
                                "inspect-workbook-readiness",
                                "export_lc_sc",
                                "--config",
                                str(config_path),
                                "--workbook-json",
                                str(root / "workbook.json"),
                                "--run-id",
                                "run-123",
                            ]
                        )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workbook_source"], "json_manifest")
        self.assertEqual(payload["header_mapping_status"], "resolved")
        self.assertEqual(payload["staged_write_operation_count"], 1)
        self.assertEqual(payload["target_prevalidation"]["status"], "passed")

    def test_inspect_workbook_readiness_command_uses_live_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )

            fake_snapshot = type(
                "FakeSnapshot",
                (),
                {
                    "sheet_name": "Sheet1",
                    "headers": [WorkbookHeader(column_index=1, text="File No.")],
                    "rows": [],
                },
            )()
            fake_result = type(
                "FakeSessionResult",
                (),
                {
                    "snapshot": fake_snapshot,
                    "preflight": {"status": "ready", "adapter_name": "xlwings"},
                },
            )()

            buffer = io.StringIO()
            with patch(
                "project.cli.XLWingsWorkbookWriteSessionProvider",
                return_value=type(
                    "FakeProvider",
                    (),
                    {"open_preflight_session": lambda self, operator_context=None: fake_result},
                )(),
            ):
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "inspect-workbook-readiness",
                            "export_lc_sc",
                            "--config",
                            str(config_path),
                            "--live-workbook",
                        ]
                    )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workbook_source"], "live_preflight")
        self.assertEqual(payload["session_preflight"]["status"], "ready")
        self.assertEqual(payload["header_count"], 1)

    def test_recover_run_command_prints_recovery_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )

            fake_snapshot = type(
                "FakeSnapshot",
                (),
                {
                    "sheet_name": "Sheet1",
                    "headers": [WorkbookHeader(column_index=1, text="File No.")],
                    "rows": [],
                },
            )()
            fake_recovery = type(
                "FakeRecovery",
                (),
                {
                    "run_id": "run-123",
                    "workflow_id": WorkflowId.EXPORT_LC_SC,
                    "outcome": "safe_reapply_staged_writes",
                    "current_workbook_hash": "a" * 64,
                    "backup_hash": "b" * 64,
                    "staged_write_plan_hash": "c" * 64,
                    "target_probes": [],
                    "discrepancies": [],
                    "details": {"probe_summary": {"matches_pre_write": 0}},
                },
            )()

            buffer = io.StringIO()
            with patch("project.cli._load_workbook_snapshot", return_value=fake_snapshot):
                with patch("project.cli.assess_recovery", return_value=fake_recovery):
                    with redirect_stdout(buffer):
                        exit_code = main(
                            [
                                "recover-run",
                                "export_lc_sc",
                                "--config",
                                str(config_path),
                                "--run-id",
                                "run-123",
                                "--workbook-json",
                                str(root / "snapshot.json"),
                            ]
                        )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["outcome"], "safe_reapply_staged_writes")
        self.assertEqual(payload["run_id"], "run-123")

    def test_plan_print_command_prints_group_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            run_report = RunReport(
                run_id="run-123",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                tool_version="0.1.0",
                rule_pack_id="export_lc_sc.default",
                rule_pack_version="1.0.0",
                started_at_utc="2026-03-28T00:00:00Z",
                completed_at_utc=None,
                state_timezone="Asia/Dhaka",
                mail_iteration_order=["mail-1"],
                print_group_order=[],
                write_phase_status=WritePhaseStatus.COMMITTED,
                print_phase_status=PrintPhaseStatus.NOT_STARTED,
                mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
                hash_algorithm="sha256",
                run_start_backup_hash="a" * 64,
                current_workbook_hash="b" * 64,
                staged_write_plan_hash="c" * 64,
                summary={"pass": 1, "warning": 0, "hard_block": 0},
            )
            mail_outcomes = [
                MailOutcomeRecord(
                    run_id="run-123",
                    mail_id="mail-1",
                    workflow_id=WorkflowId.EXPORT_LC_SC,
                    snapshot_index=0,
                    processing_status=MailProcessingStatus.WRITTEN,
                    final_decision=FinalDecision.PASS,
                    decision_reasons=[],
                    eligible_for_write=False,
                    eligible_for_print=True,
                    eligible_for_mail_move=True,
                    source_entry_id="entry-1",
                    subject_raw="subject",
                    sender_address="a@example.com",
                    saved_documents=[{"destination_path": "C:/docs/doc.pdf", "save_decision": "saved_new"}],
                )
            ]
            staged_write_plan = [
                WriteOperation(
                    write_operation_id="op-1",
                    run_id="run-123",
                    mail_id="mail-1",
                    operation_index_within_mail=0,
                    sheet_name="Sheet1",
                    row_index=3,
                    column_key="file_no",
                    expected_pre_write_value=None,
                    expected_post_write_value="P/26/0042",
                    row_eligibility_checks=[],
                )
            ]

            buffer = io.StringIO()
            with patch(
                "project.cli.load_print_planning_bundle",
                return_value=(run_report, mail_outcomes, staged_write_plan),
            ):
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "plan-print",
                            "export_lc_sc",
                            "--config",
                            str(config_path),
                            "--run-id",
                            "run-123",
                        ]
                    )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["print_group_count"], 1)
        self.assertEqual(payload["print_phase_status"], "planned")

    def test_execute_print_command_prints_completion_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            run_report = RunReport(
                run_id="run-123",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                tool_version="0.1.0",
                rule_pack_id="export_lc_sc.default",
                rule_pack_version="1.0.0",
                started_at_utc="2026-03-28T00:00:00Z",
                completed_at_utc=None,
                state_timezone="Asia/Dhaka",
                mail_iteration_order=["mail-1"],
                print_group_order=["group-1"],
                write_phase_status=WritePhaseStatus.COMMITTED,
                print_phase_status=PrintPhaseStatus.PLANNED,
                mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
                hash_algorithm="sha256",
                run_start_backup_hash="a" * 64,
                current_workbook_hash="b" * 64,
                staged_write_plan_hash="c" * 64,
                summary={"pass": 1, "warning": 0, "hard_block": 0},
            )
            mail_outcomes = [
                MailOutcomeRecord(
                    run_id="run-123",
                    mail_id="mail-1",
                    workflow_id=WorkflowId.EXPORT_LC_SC,
                    snapshot_index=0,
                    processing_status=MailProcessingStatus.WRITTEN,
                    final_decision=FinalDecision.PASS,
                    decision_reasons=[],
                    eligible_for_write=False,
                    eligible_for_print=True,
                    eligible_for_mail_move=True,
                    source_entry_id="entry-1",
                    subject_raw="subject",
                    sender_address="a@example.com",
                    saved_documents=[],
                    print_group_id="group-1",
                )
            ]
            print_batches = [
                PrintBatch(
                    print_group_id="group-1",
                    run_id="run-123",
                    mail_id="mail-1",
                    print_group_index=0,
                    document_paths=[],
                    document_path_hashes=[],
                    completion_marker_id="completion-1",
                    manual_verification_summary={
                        "document_count": 1,
                        "verified_count": 1,
                        "pending_count": 0,
                        "untracked_count": 0,
                    },
                )
            ]

            buffer = io.StringIO()
            with patch(
                "project.cli.load_print_planning_bundle",
                return_value=(run_report, mail_outcomes, []),
            ):
                with patch("project.cli.load_print_batches", return_value=print_batches):
                    with patch(
                        "project.cli.execute_print_batches",
                        return_value=(
                            replace(run_report, print_phase_status=PrintPhaseStatus.COMPLETED),
                            mail_outcomes,
                            [],
                        ),
                    ):
                        with redirect_stdout(buffer):
                            exit_code = main(
                                [
                                    "execute-print",
                                    "export_lc_sc",
                                    "--config",
                                    str(config_path),
                                    "--run-id",
                                    "run-123",
                                    "--simulate",
                                ]
                            )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["print_phase_status"], "completed")
        self.assertEqual(payload["executed_group_count"], 1)
        self.assertEqual(payload["manual_verification_summary"]["verified_count"], 1)

    def test_execute_mail_moves_command_prints_completion_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            run_report = RunReport(
                run_id="run-123",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                tool_version="0.1.0",
                rule_pack_id="export_lc_sc.default",
                rule_pack_version="1.0.0",
                started_at_utc="2026-03-28T00:00:00Z",
                completed_at_utc=None,
                state_timezone="Asia/Dhaka",
                mail_iteration_order=["mail-1"],
                print_group_order=["group-1"],
                write_phase_status=WritePhaseStatus.COMMITTED,
                print_phase_status=PrintPhaseStatus.COMPLETED,
                mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
                hash_algorithm="sha256",
                run_start_backup_hash="a" * 64,
                current_workbook_hash="b" * 64,
                staged_write_plan_hash="c" * 64,
                summary={"pass": 1, "warning": 0, "hard_block": 0},
                resolved_source_folder_entry_id="src-folder",
                resolved_destination_folder_entry_id="dst-folder",
                folder_resolution_mode="entry_id",
            )
            mail_outcomes = [
                MailOutcomeRecord(
                    run_id="run-123",
                    mail_id="mail-1",
                    workflow_id=WorkflowId.EXPORT_LC_SC,
                    snapshot_index=0,
                    processing_status=MailProcessingStatus.PRINTED,
                    final_decision=FinalDecision.PASS,
                    decision_reasons=[],
                    eligible_for_write=False,
                    eligible_for_print=False,
                    eligible_for_mail_move=True,
                    source_entry_id="entry-1",
                    subject_raw="subject",
                    sender_address="a@example.com",
                    print_group_id="group-1",
                    manual_document_verification_summary={
                        "document_count": 1,
                        "verified_count": 1,
                        "pending_count": 0,
                        "untracked_count": 0,
                    },
                )
            ]

            buffer = io.StringIO()
            with patch(
                "project.cli.load_print_planning_bundle",
                return_value=(run_report, mail_outcomes, []),
            ):
                with patch(
                    "project.cli.execute_mail_moves",
                    return_value=(
                        replace(run_report, mail_move_phase_status=MailMovePhaseStatus.COMPLETED),
                        [replace(mail_outcomes[0], processing_status=MailProcessingStatus.MOVED)],
                        [],
                        [],
                    ),
                ):
                    with redirect_stdout(buffer):
                        exit_code = main(
                            [
                                "execute-mail-moves",
                                "export_lc_sc",
                                "--config",
                                str(config_path),
                                "--run-id",
                                "run-123",
                                "--simulate",
                            ]
                        )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["mail_move_phase_status"], "completed")
        self.assertEqual(payload["mail_move_operation_count"], 0)
        self.assertEqual(payload["manual_verification_summary"]["verified_count"], 1)

    def test_execute_print_command_supports_live_print_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                        f'acrobat_executable_path = "{(root / "Acrobat.exe").as_posix()}"',
                    ]
                ),
                encoding="utf-8",
            )
            run_report = RunReport(
                run_id="run-123",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                tool_version="0.1.0",
                rule_pack_id="export_lc_sc.default",
                rule_pack_version="1.0.0",
                started_at_utc="2026-03-28T00:00:00Z",
                completed_at_utc=None,
                state_timezone="Asia/Dhaka",
                mail_iteration_order=["mail-1"],
                print_group_order=["group-1"],
                write_phase_status=WritePhaseStatus.COMMITTED,
                print_phase_status=PrintPhaseStatus.PLANNED,
                mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
                hash_algorithm="sha256",
                run_start_backup_hash="a" * 64,
                current_workbook_hash="b" * 64,
                staged_write_plan_hash="c" * 64,
                summary={"pass": 1, "warning": 0, "hard_block": 0},
            )
            mail_outcomes = [
                MailOutcomeRecord(
                    run_id="run-123",
                    mail_id="mail-1",
                    workflow_id=WorkflowId.EXPORT_LC_SC,
                    snapshot_index=0,
                    processing_status=MailProcessingStatus.WRITTEN,
                    final_decision=FinalDecision.PASS,
                    decision_reasons=[],
                    eligible_for_write=False,
                    eligible_for_print=True,
                    eligible_for_mail_move=True,
                    source_entry_id="entry-1",
                    subject_raw="subject",
                    sender_address="a@example.com",
                    saved_documents=[],
                    print_group_id="group-1",
                )
            ]
            print_batches = [
                PrintBatch(
                    print_group_id="group-1",
                    run_id="run-123",
                    mail_id="mail-1",
                    print_group_index=0,
                    document_paths=[],
                    document_path_hashes=[],
                    completion_marker_id="completion-1",
                    manual_verification_summary={},
                )
            ]

            buffer = io.StringIO()
            with patch(
                "project.cli.load_print_planning_bundle",
                return_value=(run_report, mail_outcomes, []),
            ):
                with patch("project.cli.load_print_batches", return_value=print_batches):
                    with patch("project.cli.AcrobatPrintProvider", return_value=object()) as provider_mock:
                        with patch(
                            "project.cli.execute_print_batches",
                            return_value=(
                                replace(run_report, print_phase_status=PrintPhaseStatus.COMPLETED),
                                mail_outcomes,
                                [],
                            ),
                        ):
                            with redirect_stdout(buffer):
                                exit_code = main(
                                    [
                                        "execute-print",
                                        "export_lc_sc",
                                        "--config",
                                        str(config_path),
                                        "--run-id",
                                        "run-123",
                                        "--live-print",
                                    ]
                                )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["print_phase_status"], "completed")
        provider_mock.assert_called_once()

    def test_acknowledge_partial_print_command_reports_updated_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            print_batches = [
                PrintBatch(
                    print_group_id="group-1",
                    run_id="run-123",
                    mail_id="mail-1",
                    print_group_index=0,
                    document_paths=["C:/docs/a.pdf", "C:/docs/b.pdf"],
                    document_path_hashes=["hash-a", "hash-b"],
                    completion_marker_id="completion-1",
                    manual_verification_summary={},
                )
            ]

            buffer = io.StringIO()
            with patch("project.cli.load_print_batches", return_value=print_batches):
                with patch(
                    "project.cli.acknowledge_partial_print_progress",
                    return_value={
                        "print_group_id": "group-1",
                        "mail_id": "mail-1",
                        "marker_path": "C:/tmp/group-1.json",
                        "print_status": "partial_incomplete",
                        "acknowledged_printed_document_count": 1,
                        "previous_recorded_printed_document_count": 0,
                        "remaining_document_count": 1,
                        "acknowledged_document_paths": ["C:/docs/a.pdf"],
                        "remaining_document_paths": ["C:/docs/b.pdf"],
                    },
                ) as acknowledge_mock:
                    with redirect_stdout(buffer):
                        exit_code = main(
                            [
                                "acknowledge-partial-print",
                                "export_lc_sc",
                                "--config",
                                str(config_path),
                                "--run-id",
                                "run-123",
                                "--printed-count",
                                "1",
                            ]
                        )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["run_id"], "run-123")
        self.assertEqual(payload["acknowledged_printed_document_count"], 1)
        acknowledge_mock.assert_called_once()

    def test_acknowledge_partial_print_command_can_report_completed_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            print_batches = [
                PrintBatch(
                    print_group_id="group-1",
                    run_id="run-123",
                    mail_id="mail-1",
                    print_group_index=0,
                    document_paths=["C:/docs/a.pdf", "C:/docs/b.pdf"],
                    document_path_hashes=["hash-a", "hash-b"],
                    completion_marker_id="completion-1",
                    manual_verification_summary={},
                )
            ]

            buffer = io.StringIO()
            with patch("project.cli.load_print_batches", return_value=print_batches):
                with patch(
                    "project.cli.acknowledge_partial_print_progress",
                    return_value={
                        "print_group_id": "group-1",
                        "mail_id": "mail-1",
                        "marker_path": "C:/tmp/group-1.json",
                        "print_status": "completed",
                        "acknowledged_printed_document_count": 2,
                        "previous_recorded_printed_document_count": 1,
                        "remaining_document_count": 0,
                        "acknowledged_document_paths": ["C:/docs/a.pdf", "C:/docs/b.pdf"],
                        "remaining_document_paths": [],
                    },
                ):
                    with redirect_stdout(buffer):
                        exit_code = main(
                            [
                                "acknowledge-partial-print",
                                "export_lc_sc",
                                "--config",
                                str(config_path),
                                "--run-id",
                                "run-123",
                                "--printed-count",
                                "2",
                            ]
                        )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["print_status"], "completed")
        self.assertEqual(payload["remaining_document_count"], 0)

    def test_inspect_print_adapter_command_reports_acrobat_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            acrobat_path = root / "Acrobat.exe"
            acrobat_path.write_text("fake", encoding="utf-8")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                        f'acrobat_executable_path = "{acrobat_path.as_posix()}"',
                        'print_printer_name = "Office Printer"',
                    ]
                ),
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "inspect-print-adapter",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                    ]
                )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["available"], True)
        self.assertEqual(payload["print_enabled"], True)
        self.assertEqual(payload["resolved_executable_path"], str(acrobat_path))
        self.assertEqual(payload["printer_name"], "Office Printer")

    def test_report_live_readiness_command_reports_combined_ready_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )

            fake_session_result = type(
                "FakeSessionResult",
                (),
                {"snapshot": object(), "preflight": {"status": "ready"}},
            )()

            buffer = io.StringIO()
            with patch("project.cli._load_snapshot_if_supplied", return_value=object()):
                with patch(
                    "project.cli.summarize_mail_snapshot",
                    return_value={
                        "snapshot_count": 2,
                        "attachment_count": 3,
                        "entry_id_order": ["entry-1", "entry-2"],
                        "mail_iteration_order": ["mail-1", "mail-2"],
                    },
                ):
                    with patch("project.cli._load_erp_provider", return_value=object()):
                        with patch(
                            "project.cli.inspect_erp_rows",
                            return_value={
                                "requested_file_numbers": ["P/26/0042"],
                                "canonical_file_numbers": ["P/26/0042"],
                                "match_count": 1,
                            },
                        ):
                            with patch(
                                "project.cli.XLWingsWorkbookWriteSessionProvider"
                            ) as workbook_provider_mock:
                                workbook_provider_mock.return_value.open_preflight_session.return_value = (
                                    fake_session_result
                                )
                                with patch(
                                    "project.cli.summarize_workbook_readiness",
                                    return_value={
                                        "workbook_available": True,
                                        "sheet_name": "2026",
                                        "header_mapping_status": "resolved",
                                        "row_count": 10,
                                        "session_preflight": {"status": "ready"},
                                    },
                                ):
                                    with patch(
                                        "project.cli.inspect_acrobat_print_adapter",
                                        return_value={
                                            "available": True,
                                            "resolved_executable_path": "C:\\Acrobat.exe",
                                            "printer_name": "Office Printer",
                                            "blank_separator_exists": True,
                                            "error": None,
                                        },
                                    ):
                                        with redirect_stdout(buffer):
                                            exit_code = main(
                                                [
                                                    "report-live-readiness",
                                                    "export_lc_sc",
                                                    "--config",
                                                    str(config_path),
                                                    "--erp-file-number",
                                                    "P/26/0042",
                                                ]
                                            )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["overall_status"], "ready")
        self.assertEqual(payload["ready_section_count"], 4)
        self.assertEqual(payload["sections"]["snapshot"]["status"], "ready")
        self.assertEqual(payload["sections"]["erp"]["status"], "ready")
        self.assertEqual(payload["sections"]["workbook"]["status"], "ready")
        self.assertEqual(payload["sections"]["print"]["status"], "ready")
        self.assertEqual(payload["sections"]["erp"]["match_count"], 1)

    def test_report_live_readiness_command_uses_download_probe_without_file_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )

            fake_session_result = type(
                "FakeSessionResult",
                (),
                {"snapshot": object(), "preflight": {"status": "ready"}},
            )()

            buffer = io.StringIO()
            with patch("project.cli._load_snapshot_if_supplied", return_value=object()):
                with patch(
                    "project.cli.summarize_mail_snapshot",
                    return_value={
                        "snapshot_count": 0,
                        "attachment_count": 0,
                        "entry_id_order": [],
                        "mail_iteration_order": [],
                    },
                ):
                    with patch(
                        "project.cli.inspect_playwright_report_download",
                        return_value={
                            "status": "ready",
                            "download_receipt": {
                                "exists": True,
                                "is_empty": False,
                                "looks_like_html": False,
                                "has_required_erp_headers": True,
                            },
                        },
                    ) as inspect_mock:
                        with patch(
                            "project.cli.XLWingsWorkbookWriteSessionProvider"
                        ) as workbook_provider_mock:
                            workbook_provider_mock.return_value.open_preflight_session.return_value = (
                                fake_session_result
                            )
                            with patch(
                                "project.cli.summarize_workbook_readiness",
                                return_value={
                                    "workbook_available": True,
                                    "sheet_name": "2026",
                                    "header_mapping_status": "resolved",
                                    "row_count": 10,
                                    "session_preflight": {"status": "ready"},
                                },
                            ):
                                with patch(
                                    "project.cli.inspect_acrobat_print_adapter",
                                    return_value={
                                        "available": True,
                                        "resolved_executable_path": "C:\\Acrobat.exe",
                                        "printer_name": "Office Printer",
                                        "blank_separator_exists": True,
                                        "error": None,
                                    },
                                ):
                                    with redirect_stdout(buffer):
                                        exit_code = main(
                                            [
                                                "report-live-readiness",
                                                "export_lc_sc",
                                                "--config",
                                                str(config_path),
                                            ]
                                        )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["sections"]["erp"]["status"], "ready")
        self.assertEqual(payload["sections"]["erp"]["lookup_scope"], "connectivity_only")
        inspect_mock.assert_called_once()

    def test_report_live_readiness_command_keeps_section_errors_in_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with patch(
                "project.cli._load_snapshot_if_supplied",
                side_effect=RuntimeError("Outlook unavailable"),
            ):
                with patch(
                    "project.cli.inspect_playwright_report_download",
                    side_effect=RuntimeError("ERP unavailable"),
                ):
                    with patch("project.cli.XLWingsWorkbookWriteSessionProvider") as workbook_provider_mock:
                        workbook_provider_mock.return_value.open_preflight_session.side_effect = RuntimeError(
                            "Workbook unavailable"
                        )
                        with patch(
                            "project.cli.inspect_acrobat_print_adapter",
                            side_effect=RuntimeError("Print unavailable"),
                        ):
                            with redirect_stdout(buffer):
                                exit_code = main(
                                    [
                                        "report-live-readiness",
                                        "export_lc_sc",
                                        "--config",
                                        str(config_path),
                                    ]
                                )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["overall_status"], "attention_required")
        self.assertEqual(payload["issue_section_count"], 4)
        self.assertEqual(payload["sections"]["snapshot"]["status"], "issue")
        self.assertEqual(payload["sections"]["snapshot"]["error"], "Outlook unavailable")
        self.assertEqual(payload["sections"]["erp"]["status"], "issue")
        self.assertEqual(payload["sections"]["erp"]["error"], "ERP unavailable")
        self.assertEqual(payload["sections"]["workbook"]["status"], "issue")
        self.assertEqual(payload["sections"]["workbook"]["error"], "Workbook unavailable")
        self.assertEqual(payload["sections"]["print"]["status"], "issue")
        self.assertEqual(payload["sections"]["print"]["error"], "Print unavailable")

    def test_run_live_smoke_test_command_writes_bundle_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )

            fake_session_result = type(
                "FakeSessionResult",
                (),
                {"snapshot": object(), "preflight": {"status": "ready"}},
            )()
            fake_erp_provider = type(
                "FakeERPProvider",
                (),
                {"lookup_rows": lambda self, *, file_numbers: []},
            )()

            buffer = io.StringIO()
            with patch("project.cli._load_snapshot_if_supplied", return_value=[]):
                with patch(
                    "project.cli.summarize_mail_snapshot",
                    return_value={
                        "snapshot_count": 0,
                        "attachment_count": 0,
                        "entry_id_order": [],
                        "mail_iteration_order": [],
                    },
                ):
                    with patch(
                        "project.cli.inspect_playwright_report_download",
                        return_value={
                            "status": "ready",
                            "download_receipt": {
                                "exists": True,
                                "is_empty": False,
                                "looks_like_html": False,
                                "has_required_erp_headers": True,
                            },
                        },
                    ):
                        with patch(
                            "project.cli.XLWingsWorkbookWriteSessionProvider"
                        ) as workbook_provider_mock:
                            workbook_provider_mock.return_value.open_preflight_session.return_value = (
                                fake_session_result
                            )
                            with patch(
                                "project.cli.summarize_workbook_readiness",
                                return_value={
                                    "workbook_available": True,
                                    "sheet_name": "2026",
                                    "header_mapping_status": "resolved",
                                    "row_count": 0,
                                    "session_preflight": {"status": "ready"},
                                },
                            ):
                                with patch(
                                    "project.cli.inspect_acrobat_print_adapter",
                                    return_value={
                                        "available": True,
                                        "resolved_executable_path": "C:\\Acrobat.exe",
                                        "printer_name": "Office Printer",
                                        "blank_separator_exists": True,
                                        "error": None,
                                    },
                                ):
                                    with redirect_stdout(buffer):
                                        exit_code = main(
                                            [
                                                "run-live-smoke-test",
                                                "export_lc_sc",
                                                "--config",
                                                str(config_path),
                                            ]
                                        )
            payload = json.loads(buffer.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["workflow_id"], "export_lc_sc")
            self.assertEqual(payload["overall_status"], "ready")
            summary_path = Path(payload["output_json"])
            self.assertTrue(summary_path.exists())
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "ready")
            self.assertEqual(summary["attachment_audit"]["status"], "not_requested")

    def test_run_live_smoke_test_command_saves_pdf_attachment_audits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )

            mail = EmailMessage(
                mail_id="mail-1",
                entry_id="entry-1",
                received_time_utc="2026-03-30T00:00:00Z",
                received_time_workflow_tz="2026-03-30T06:00:00+06:00",
                subject_raw="subject",
                sender_address="a@example.com",
                snapshot_index=0,
                attachments=[
                    EmailAttachment(
                        attachment_id="att-1",
                        attachment_index=0,
                        attachment_name="invoice.pdf",
                        normalized_filename="invoice.pdf",
                    )
                ],
            )
            fake_session_result = type(
                "FakeSessionResult",
                (),
                {"snapshot": object(), "preflight": {"status": "ready"}},
            )()
            fake_erp_provider = type(
                "FakeERPProvider",
                (),
                {"lookup_rows": lambda self, *, file_numbers: []},
            )()

            class FakeAttachmentProvider:
                def save_attachment(self, *, mail, attachment_index, destination_path):
                    destination_path.write_bytes(b"%PDF-1.4\nfake\n")

            buffer = io.StringIO()
            with patch("project.cli._load_snapshot_if_supplied", return_value=[mail]):
                with patch(
                    "project.cli.summarize_mail_snapshot",
                    return_value={
                        "snapshot_count": 1,
                        "attachment_count": 1,
                        "entry_id_order": ["entry-1"],
                        "mail_iteration_order": ["mail-1"],
                    },
                ):
                    with patch("project.cli._load_erp_provider", return_value=fake_erp_provider):
                        with patch(
                            "project.cli.XLWingsWorkbookWriteSessionProvider"
                        ) as workbook_provider_mock:
                            workbook_provider_mock.return_value.open_preflight_session.return_value = (
                                fake_session_result
                            )
                            with patch(
                                "project.cli.summarize_workbook_readiness",
                                return_value={
                                    "workbook_available": True,
                                    "sheet_name": "2026",
                                    "header_mapping_status": "resolved",
                                    "row_count": 0,
                                    "session_preflight": {"status": "ready"},
                                },
                            ):
                                with patch(
                                    "project.cli.inspect_acrobat_print_adapter",
                                    return_value={
                                        "available": True,
                                        "resolved_executable_path": "C:\\Acrobat.exe",
                                        "printer_name": "Office Printer",
                                        "blank_separator_exists": True,
                                        "error": None,
                                    },
                                ):
                                    with patch(
                                        "project.cli.Win32ComAttachmentContentProvider",
                                        return_value=FakeAttachmentProvider(),
                                    ):
                                        with patch(
                                            "project.workflows.live_smoke_test.extract_saved_document_raw_report",
                                            return_value={
                                                "mode": "layered",
                                                "page_count": 1,
                                                "combined_text": "hello",
                                                "pages": [{"page_number": 1, "text": "hello"}],
                                            },
                                        ):
                                            with redirect_stdout(buffer):
                                                exit_code = main(
                                                    [
                                                        "run-live-smoke-test",
                                                        "export_lc_sc",
                                                        "--config",
                                                        str(config_path),
                                                        "--save-pdf-attachments",
                                                        "--max-pdf-attachments",
                                                        "1",
                                                    ]
                                                )
            payload = json.loads(buffer.getvalue())
            self.assertEqual(exit_code, 0)
            summary = json.loads(Path(payload["output_json"]).read_text(encoding="utf-8"))
            self.assertEqual(summary["attachment_audit"]["status"], "ready")
            self.assertEqual(summary["summary_counts"]["saved_pdf_count"], 1)
            audit_dir = Path(summary["attachment_audit"]["document_audit_directory"])
            self.assertEqual(len(list(audit_dir.glob("*.layered.json"))), 1)

    def test_validate_config_uses_live_outlook_snapshot_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )

            fake_provider = type(
                "FakeProvider",
                (),
                {
                    "load_snapshot": lambda self, *, state_timezone: [
                        type(
                            "FakeMail",
                            (),
                            {
                                "mail_id": "mail-1",
                                "entry_id": "entry-1",
                                "received_time_utc": "2026-03-28T03:00:00Z",
                                "received_time_workflow_tz": "2026-03-28T09:00:00+06:00",
                                "subject_raw": "subject",
                                "sender_address": "a@example.com",
                                "snapshot_index": 0,
                                "body_text": "",
                            },
                        )()
                    ]
                },
            )()

            buffer = io.StringIO()
            with patch("project.cli.Win32ComMailSnapshotProvider", return_value=fake_provider):
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "validate-config",
                            "export_lc_sc",
                            "--config",
                            str(config_path),
                            "--live-outlook-snapshot",
                        ]
                    )

        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["snapshot_count"], 1)

    def test_validate_run_rejects_document_root_without_live_outlook_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks", "documents"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )

            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                exit_code = main(
                    [
                        "validate-run",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                        "--document-root",
                        str(root / "documents"),
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn(
            "--document-root currently requires --live-outlook-snapshot",
            stderr_buffer.getvalue(),
        )

    def test_validate_run_rejects_multiple_erp_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            erp_json_path = root / "erp.json"
            erp_json_path.write_text("[]", encoding="utf-8")
            erp_export_path = root / "erp.csv"
            erp_export_path.write_text("rptDateWiseLCRegister\nFile No.,L/C No.,Buyer Name,LC DT.\n", encoding="utf-8")

            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                exit_code = main(
                    [
                        "validate-run",
                        "export_lc_sc",
                        "--config",
                        str(config_path),
                        "--erp-json",
                        str(erp_json_path),
                        "--erp-export",
                        str(erp_export_path),
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn(
            "Choose one ERP source: --erp-json, --erp-export, or --live-erp",
            stderr_buffer.getvalue(),
        )

    def test_validate_run_ud_ip_exp_accepts_ud_payload_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = _write_cli_config(root, workflow_year=datetime.datetime.now().year)
            snapshot_path = root / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-ud-001",
                            "received_time": "2026-04-01T03:00:00Z",
                            "subject_raw": "UD-LC-0043-ANANTA",
                            "sender_address": "sender@example.com",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            workbook_path = root / "workbook.json"
            workbook_path.write_text(
                json.dumps(
                    {
                        "sheet_name": "Sheet1",
                        "headers": [
                            {"column_index": 1, "text": "L/C & S/C No."},
                            {"column_index": 2, "text": "Quantity of Fabrics (Yds/Mtr)"},
                            {"column_index": 3, "text": "UD No. & IP No."},
                            {"column_index": 4, "text": "L/C Amnd No."},
                            {"column_index": 5, "text": "L/C Amnd Date"},
                        ],
                        "rows": [
                            {"row_index": 11, "values": {"1": "LC-0043", "2": "1000 YDS", "3": ""}},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            ud_payload_path = root / "ud-payloads.json"
            ud_payload_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-ud-001",
                            "document_number": "UD-LC-0043-ANANTA",
                            "document_date": "2026-04-01",
                            "lc_sc_number": "LC-0043",
                            "quantity": "1000",
                            "quantity_unit": "YDS",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "validate-run",
                        "ud_ip_exp",
                        "--config",
                        str(config_path),
                        "--snapshot-json",
                        str(snapshot_path),
                        "--workbook-json",
                        str(workbook_path),
                        "--ud-payload-json",
                        str(ud_payload_path),
                    ]
                )
            payload = json.loads(buffer.getvalue())
            mail_outcomes = _read_jsonl(Path(payload["artifact_root"]) / "mail_outcomes.jsonl")

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow_id"], "ud_ip_exp")
        self.assertEqual(payload["summary"], {"pass": 1, "warning": 0, "hard_block": 0})
        self.assertEqual(payload["staged_write_operation_count"], 1)
        self.assertEqual(payload["transport_policy"]["status"], "disabled_pending_policy")
        self.assertIn("mail-move policy remains unresolved", payload["transport_policy"]["reason"])
        self.assertEqual(mail_outcomes[0]["ud_selection"]["selected_candidate_id"], "11")
        self.assertTrue(mail_outcomes[0]["eligible_for_write"])
        self.assertFalse(mail_outcomes[0]["eligible_for_print"])
        self.assertFalse(mail_outcomes[0]["eligible_for_mail_move"])
        self.assertIn(
            "mail-move policy remains unresolved",
            "\n".join(mail_outcomes[0]["decision_reasons"]),
        )

    def test_validate_run_ud_ip_exp_without_ud_payload_manifest_hard_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = _write_cli_config(root, workflow_year=datetime.datetime.now().year)
            snapshot_path = root / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-ud-001",
                            "received_time": "2026-04-01T03:00:00Z",
                            "subject_raw": "UD-LC-0043-ANANTA",
                            "sender_address": "sender@example.com",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            workbook_path = root / "workbook.json"
            workbook_path.write_text(
                json.dumps(
                    {
                        "sheet_name": "Sheet1",
                        "headers": [
                            {"column_index": 1, "text": "L/C & S/C No."},
                            {"column_index": 2, "text": "Quantity of Fabrics (Yds/Mtr)"},
                            {"column_index": 3, "text": "UD No. & IP No."},
                            {"column_index": 4, "text": "L/C Amnd No."},
                            {"column_index": 5, "text": "L/C Amnd Date"},
                        ],
                        "rows": [],
                    }
                ),
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "validate-run",
                        "ud_ip_exp",
                        "--config",
                        str(config_path),
                        "--snapshot-json",
                        str(snapshot_path),
                        "--workbook-json",
                        str(workbook_path),
                    ]
                )
            payload = json.loads(buffer.getvalue())
            discrepancies = _read_jsonl(Path(payload["artifact_root"]) / "discrepancies.jsonl")

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["summary"], {"pass": 0, "warning": 0, "hard_block": 1})
        self.assertEqual(payload["staged_write_operation_count"], 0)
        self.assertEqual(payload["transport_policy"]["status"], "disabled_pending_policy")
        self.assertEqual(
            [item["code"] for item in discrepancies],
            ["ud_allocation_unresolved", "ud_required_document_missing"],
        )


def _write_cli_config(root: Path, *, workflow_year: int) -> Path:
    for name in ("reports", "runs", "backups", "workbooks"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
    config_path = root / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                'state_timezone = "Asia/Dhaka"',
                f'report_root = "{(root / "reports").as_posix()}"',
                f'run_artifact_root = "{(root / "runs").as_posix()}"',
                f'backup_root = "{(root / "backups").as_posix()}"',
                'outlook_profile = "outlook"',
                f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                'erp_base_url = "https://erp.local"',
                'playwright_browser_channel = "msedge"',
                f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                "excel_lock_timeout_seconds = 60",
                "print_enabled = true",
                'source_working_folder_entry_id = "src-folder"',
                'destination_success_entry_id = "dst-folder"',
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


if __name__ == "__main__":
    unittest.main()

