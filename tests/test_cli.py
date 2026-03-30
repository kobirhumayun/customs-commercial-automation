from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from contextlib import redirect_stderr
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from project.cli import main
from project.models import (
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
from project.workbook import WorkbookHeader


class CLITests(unittest.TestCase):
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
                        'outlook_profile = "Operations"',
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
                        'outlook_profile = "Operations"',
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
                        'outlook_profile = "Operations"',
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
                        'outlook_profile = "Operations"',
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
                        'outlook_profile = "Operations"',
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
                        'outlook_profile = "Operations"',
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
                        'outlook_profile = "Operations"',
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
                        'outlook_profile = "Operations"',
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
                        'outlook_profile = "Operations"',
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
                        'outlook_profile = "Operations"',
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


if __name__ == "__main__":
    unittest.main()
