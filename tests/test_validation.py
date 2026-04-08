from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project.config import load_workflow_config
from project.documents import JsonManifestSavedDocumentAnalysisProvider
from project.erp import JsonManifestERPRowProvider
from project.models import FinalDecision, WorkflowId
from project.models.enums import MailProcessingStatus
from project.outlook import ConfiguredFolderGateway
from project.reporting.persistence import write_discrepancies, write_mail_outcomes, write_run_metadata
from project.rules import load_rule_pack
from project.storage import SimulatedAttachmentContentProvider
from project.utils.json import to_jsonable
from project.utils.time import validate_timezone
from project.workbook import JsonManifestWorkbookSnapshotProvider
from project.workflows.bootstrap import initialize_workflow_run
from project.workflows.registry import get_workflow_descriptor
from project.workflows.snapshot import build_email_snapshot, load_snapshot_manifest
from project.workflows.validation import validate_run_snapshot


class ValidationTests(unittest.TestCase):
    def test_configured_folder_gateway_returns_entry_id_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
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
                        'source_working_folder_display_name = "working"',
                        'destination_success_display_name = "UD and LC"',
                    ]
                ),
                encoding="utf-8",
            )

            config = load_workflow_config(
                descriptor=get_workflow_descriptor(WorkflowId.EXPORT_LC_SC),
                config_path=config_path,
            )

        resolved = ConfiguredFolderGateway().resolve_configured_folders(config=config)

        self.assertEqual(resolved.resolution_mode, "entry_id")
        self.assertEqual(resolved.source_working_folder.entry_id, "src-folder")
        self.assertEqual(resolved.source_working_folder.display_name, "working")
        self.assertEqual(resolved.destination_success_folder.entry_id, "dst-folder")

    def test_validate_run_snapshot_marks_empty_rule_pack_mails_as_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(
                tz=validate_timezone("Asia/Dhaka")
            ).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            for directory in (report_root, run_root, backup_root, workbook_root):
                directory.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{workbook_root.as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_manifest_path = root / "snapshot.json"
            snapshot_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "LC-0038-ANANTA GARMENTS LTD_AMD_05",
                            "sender_address": "one@example.com",
                            "body_text": "Please process file P/26/42 today.",
                        },
                        {
                            "entry_id": "entry-002",
                            "received_time": "2026-03-28T04:00:00Z",
                            "subject_raw": "SC-010-PDL-8-ZYTA APPARELS LTD",
                            "sender_address": "two@example.com",
                            "body_text": "Related file is P-26-0007.",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            erp_manifest_path = root / "erp.json"
            erp_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "file_number": "P/26/0042",
                            "lc_sc_number": "LC-0038",
                            "buyer_name": "ANANTA GARMENTS LTD",
                            "lc_sc_date": "2026-01-10",
                            "source_row_index": 5,
                        },
                        {
                            "file_number": "P/26/0007",
                            "lc_sc_number": "SC-010-PDL-8",
                            "buyer_name": "ZYTA APPARELS LTD",
                            "lc_sc_date": "2026-01-12",
                            "source_row_index": 7,
                        },
                    ]
                ),
                encoding="utf-8",
            )

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
            snapshot = build_email_snapshot(
                load_snapshot_manifest(snapshot_manifest_path),
                state_timezone="Asia/Dhaka",
            )
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=snapshot,
            )

            validation_result = validate_run_snapshot(
                descriptor=descriptor,
                run_report=initialized.run_report,
                rule_pack=rule_pack,
                erp_row_provider=JsonManifestERPRowProvider(erp_manifest_path),
            )
            write_run_metadata(initialized.artifact_paths, to_jsonable(validation_result.run_report))
            write_mail_outcomes(initialized.artifact_paths, to_jsonable(validation_result.mail_outcomes))
            write_discrepancies(initialized.artifact_paths, to_jsonable(validation_result.discrepancy_reports))

            self.assertEqual(validation_result.run_report.summary, {"pass": 2, "warning": 0, "hard_block": 0})
            self.assertTrue(
                all(outcome.final_decision == FinalDecision.PASS for outcome in validation_result.mail_outcomes)
            )
            self.assertTrue(
                all(outcome.processing_status.value == "validated" for outcome in validation_result.mail_outcomes)
            )

            mail_outcome_records = [
                json.loads(line)
                for line in initialized.artifact_paths.mail_outcomes_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(mail_outcome_records), 2)
            self.assertEqual([record["final_decision"] for record in mail_outcome_records], ["pass", "pass"])
            self.assertEqual(
                [record["file_numbers_extracted"] for record in mail_outcome_records],
                [["P/26/0042"], ["P/26/0007"]],
            )
            self.assertEqual(initialized.artifact_paths.discrepancies_path.read_text(encoding="utf-8"), "")

    def test_validate_run_snapshot_uses_erp_family_as_final_source_even_when_subject_differs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(
                tz=validate_timezone("Asia/Dhaka")
            ).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            for directory in (report_root, run_root, backup_root, workbook_root):
                directory.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{workbook_root.as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_manifest_path = root / "snapshot.json"
            snapshot_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "LC-5392-CUTTING EDGE INDUSTRIES LTD_ACK",
                            "sender_address": "one@example.com",
                            "body_text": "Please process file P/26/0624 today.",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            erp_manifest_path = root / "erp.json"
            erp_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "file_number": "P/26/0624",
                            "lc_sc_number": "DPCBD1175392",
                            "buyer_name": "CUTTING EDGE INDUSTRIES LTD",
                            "lc_sc_date": "2026-03-30",
                            "source_row_index": 5,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
            snapshot = build_email_snapshot(
                load_snapshot_manifest(snapshot_manifest_path),
                state_timezone="Asia/Dhaka",
            )
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=snapshot,
            )

            validation_result = validate_run_snapshot(
                descriptor=descriptor,
                run_report=initialized.run_report,
                rule_pack=rule_pack,
                erp_row_provider=JsonManifestERPRowProvider(erp_manifest_path),
            )

            self.assertEqual(validation_result.run_report.summary, {"pass": 1, "warning": 0, "hard_block": 0})
            self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.PASS)
            self.assertEqual(validation_result.mail_outcomes[0].processing_status, MailProcessingStatus.VALIDATED)
            self.assertEqual(validation_result.mail_outcomes[0].file_numbers_extracted, ["P/26/0624"])

    def test_validate_run_snapshot_hard_blocks_blank_subject(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(
                tz=validate_timezone("Asia/Dhaka")
            ).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            for directory in (report_root, run_root, backup_root, workbook_root):
                directory.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{workbook_root.as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_manifest_path = root / "snapshot.json"
            snapshot_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "   ",
                            "sender_address": "one@example.com",
                            "body_text": "Please process file P/26/0042.",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
            snapshot = build_email_snapshot(
                load_snapshot_manifest(snapshot_manifest_path),
                state_timezone="Asia/Dhaka",
            )
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=snapshot,
            )

            validation_result = validate_run_snapshot(
                descriptor=descriptor,
                run_report=initialized.run_report,
                rule_pack=rule_pack,
            )

            self.assertEqual(validation_result.run_report.summary, {"pass": 0, "warning": 0, "hard_block": 1})
            self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.HARD_BLOCK)
            self.assertEqual(
                validation_result.mail_outcomes[0].processing_status,
                MailProcessingStatus.BLOCKED,
            )
            self.assertEqual(validation_result.discrepancy_reports[0].code, "mail_subject_missing")

    def test_validate_run_snapshot_hard_blocks_missing_erp_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(
                tz=validate_timezone("Asia/Dhaka")
            ).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            for directory in (report_root, run_root, backup_root, workbook_root):
                directory.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{workbook_root.as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_manifest_path = root / "snapshot.json"
            snapshot_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "LC-0038-ANANTA GARMENTS LTD_AMD_05",
                            "sender_address": "one@example.com",
                            "body_text": "Please process file P/26/42 today.",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            erp_manifest_path = root / "erp.json"
            erp_manifest_path.write_text(json.dumps([]), encoding="utf-8")

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
            snapshot = build_email_snapshot(
                load_snapshot_manifest(snapshot_manifest_path),
                state_timezone="Asia/Dhaka",
            )
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=snapshot,
            )

            validation_result = validate_run_snapshot(
                descriptor=descriptor,
                run_report=initialized.run_report,
                rule_pack=rule_pack,
                erp_row_provider=JsonManifestERPRowProvider(erp_manifest_path),
            )

            self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.HARD_BLOCK)
            self.assertIn(
                "export_erp_row_missing",
                [report.code for report in validation_result.discrepancy_reports],
            )

    def test_validate_run_snapshot_hard_blocks_inconsistent_family(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(
                tz=validate_timezone("Asia/Dhaka")
            ).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            for directory in (report_root, run_root, backup_root, workbook_root):
                directory.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{workbook_root.as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_manifest_path = root / "snapshot.json"
            snapshot_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "LC-0038-ANANTA GARMENTS LTD_AMD_05",
                            "sender_address": "one@example.com",
                            "body_text": "Please process file P/26/42 and P/26/0007 today.",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            erp_manifest_path = root / "erp.json"
            erp_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "file_number": "P/26/0042",
                            "lc_sc_number": "LC-0038",
                            "buyer_name": "ANANTA GARMENTS LTD",
                            "lc_sc_date": "2026-01-10",
                            "source_row_index": 5,
                        },
                        {
                            "file_number": "P/26/0007",
                            "lc_sc_number": "SC-010-PDL-8",
                            "buyer_name": "ZYTA APPARELS LTD",
                            "lc_sc_date": "2026-01-12",
                            "source_row_index": 7,
                        },
                    ]
                ),
                encoding="utf-8",
            )

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
            snapshot = build_email_snapshot(
                load_snapshot_manifest(snapshot_manifest_path),
                state_timezone="Asia/Dhaka",
            )
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=snapshot,
            )

            validation_result = validate_run_snapshot(
                descriptor=descriptor,
                run_report=initialized.run_report,
                rule_pack=rule_pack,
                erp_row_provider=JsonManifestERPRowProvider(erp_manifest_path),
            )

            self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.HARD_BLOCK)
            self.assertIn(
                "export_family_inconsistent",
                [report.code for report in validation_result.discrepancy_reports],
            )

    def test_validate_run_snapshot_stages_export_append_when_workbook_snapshot_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(
                tz=validate_timezone("Asia/Dhaka")
            ).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            for directory in (report_root, run_root, backup_root, workbook_root):
                directory.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{workbook_root.as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_manifest_path = root / "snapshot.json"
            snapshot_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "LC-0038-ANANTA GARMENTS LTD_AMD_05",
                            "sender_address": "one@example.com",
                            "body_text": "Please process file P/26/42 today.",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            erp_manifest_path = root / "erp.json"
            erp_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "file_number": "P/26/0042",
                            "lc_sc_number": "LC-0038",
                            "buyer_name": "ANANTA GARMENTS LTD",
                            "lc_sc_date": "2026-01-10",
                            "source_row_index": 5,
                            "notify_bank": "ABC BANK",
                            "current_lc_value": "10000",
                            "ship_date": "2026-02-01",
                            "expiry_date": "2026-03-01",
                            "lc_qty": "5000",
                            "lc_unit": "YDS",
                            "amd_no": "05",
                            "amd_date": "2026-01-15",
                            "nego_bank": "XYZ BANK",
                            "master_lc_no": "MLC-001",
                            "master_lc_date": "2025-12-20",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            workbook_manifest_path = root / "workbook.json"
            workbook_manifest_path.write_text(
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
                        "rows": [],
                    }
                ),
                encoding="utf-8",
            )

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
            snapshot = build_email_snapshot(
                load_snapshot_manifest(snapshot_manifest_path),
                state_timezone="Asia/Dhaka",
            )
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=snapshot,
            )

            validation_result = validate_run_snapshot(
                descriptor=descriptor,
                run_report=initialized.run_report,
                rule_pack=rule_pack,
                erp_row_provider=JsonManifestERPRowProvider(erp_manifest_path),
                workbook_snapshot=JsonManifestWorkbookSnapshotProvider(workbook_manifest_path).load_snapshot(),
            )

            self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.PASS)
            self.assertTrue(validation_result.mail_outcomes[0].eligible_for_write)
            self.assertEqual(len(validation_result.staged_write_plan), 14)
            self.assertEqual(validation_result.staged_write_plan[0].row_index, 3)
            self.assertEqual(
                [operation.column_key for operation in validation_result.staged_write_plan[:3]],
                ["file_no", "lc_sc_no", "buyer_name"],
            )

    def test_validate_run_snapshot_reuses_first_blank_buyer_name_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(
                tz=validate_timezone("Asia/Dhaka")
            ).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            for directory in (report_root, run_root, backup_root, workbook_root):
                directory.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{workbook_root.as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_manifest_path = root / "snapshot.json"
            snapshot_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "LC-0038-ANANTA GARMENTS LTD_AMD_05",
                            "sender_address": "one@example.com",
                            "body_text": "Please process file P/26/42 today.",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            erp_manifest_path = root / "erp.json"
            erp_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "file_number": "P/26/0042",
                            "lc_sc_number": "LC-0038",
                            "buyer_name": "ANANTA GARMENTS LTD",
                            "lc_sc_date": "2026-01-10",
                            "source_row_index": 5,
                            "notify_bank": "ABC BANK",
                            "current_lc_value": "10000",
                            "ship_date": "2026-02-01",
                            "expiry_date": "2026-03-01",
                            "lc_qty": "5000",
                            "lc_unit": "YDS",
                            "amd_no": "05",
                            "amd_date": "2026-01-15",
                            "nego_bank": "XYZ BANK",
                            "master_lc_no": "MLC-001",
                            "master_lc_date": "2025-12-20",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            workbook_manifest_path = root / "workbook.json"
            workbook_manifest_path.write_text(
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
                            {"row_index": 3, "values": {"1": "P/26/0001", "3": "FILLED BUYER"}},
                            {"row_index": 4, "values": {"1": "", "2": "", "3": ""}},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
            snapshot = build_email_snapshot(
                load_snapshot_manifest(snapshot_manifest_path),
                state_timezone="Asia/Dhaka",
            )
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=snapshot,
            )

            validation_result = validate_run_snapshot(
                descriptor=descriptor,
                run_report=initialized.run_report,
                rule_pack=rule_pack,
                erp_row_provider=JsonManifestERPRowProvider(erp_manifest_path),
                workbook_snapshot=JsonManifestWorkbookSnapshotProvider(workbook_manifest_path).load_snapshot(),
            )

            self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.PASS)
            self.assertEqual(len(validation_result.staged_write_plan), 14)
            self.assertTrue(all(operation.row_index == 4 for operation in validation_result.staged_write_plan))

    def test_validate_run_snapshot_sets_mtr_quantity_number_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(
                tz=validate_timezone("Asia/Dhaka")
            ).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            for directory in (report_root, run_root, backup_root, workbook_root):
                directory.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{workbook_root.as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_manifest_path = root / "snapshot.json"
            snapshot_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "LC-0038-ANANTA GARMENTS LTD_AMD_05",
                            "sender_address": "one@example.com",
                            "body_text": "Please process file P/26/42 today.",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            erp_manifest_path = root / "erp.json"
            erp_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "file_number": "P/26/0042",
                            "lc_sc_number": "LC-0038",
                            "buyer_name": "ANANTA GARMENTS LTD",
                            "lc_sc_date": "2026-01-10",
                            "source_row_index": 5,
                            "notify_bank": "ABC BANK",
                            "current_lc_value": "10000",
                            "ship_date": "2026-02-01",
                            "expiry_date": "2026-03-01",
                            "lc_qty": "5000",
                            "lc_unit": "MTR",
                            "amd_no": "05",
                            "amd_date": "2026-01-15",
                            "nego_bank": "XYZ BANK",
                            "master_lc_no": "MLC-001",
                            "master_lc_date": "2025-12-20",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            workbook_manifest_path = root / "workbook.json"
            workbook_manifest_path.write_text(
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
                        "rows": [],
                    }
                ),
                encoding="utf-8",
            )

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
            snapshot = build_email_snapshot(
                load_snapshot_manifest(snapshot_manifest_path),
                state_timezone="Asia/Dhaka",
            )
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=snapshot,
            )

            validation_result = validate_run_snapshot(
                descriptor=descriptor,
                run_report=initialized.run_report,
                rule_pack=rule_pack,
                erp_row_provider=JsonManifestERPRowProvider(erp_manifest_path),
                workbook_snapshot=JsonManifestWorkbookSnapshotProvider(workbook_manifest_path).load_snapshot(),
            )

            quantity_operation = next(
                operation
                for operation in validation_result.staged_write_plan
                if operation.column_key == "quantity_fabrics"
            )
            self.assertEqual(quantity_operation.number_format, '#,###.00 "Mtr"')

    def test_validate_run_snapshot_formats_buyer_and_bank_fields_for_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(
                tz=validate_timezone("Asia/Dhaka")
            ).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            for directory in (report_root, run_root, backup_root, workbook_root):
                directory.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{workbook_root.as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_manifest_path = root / "snapshot.json"
            snapshot_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "LC-0038-ANANTA GARMENTS LTD_AMD_05",
                            "sender_address": "one@example.com",
                            "body_text": "Please process file P/26/42 today.",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            erp_manifest_path = root / "erp.json"
            erp_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "file_number": "P/26/0042",
                            "lc_sc_number": "LC-0038",
                            "buyer_name": "ANANTA GARMENTS LTD.\\DHAKA.",
                            "lc_sc_date": "2026-01-10",
                            "source_row_index": 5,
                            "notify_bank": "ABC BANK\\DHAKA BRANCH",
                            "current_lc_value": "10000",
                            "ship_date": "2026-02-01",
                            "expiry_date": "2026-03-01",
                            "lc_qty": "5000",
                            "lc_unit": "YDS",
                            "amd_no": "05",
                            "amd_date": "2026-01-15",
                            "nego_bank": "XYZ BANK\\DHAKA BRANCH",
                            "master_lc_no": "MLC-001",
                            "master_lc_date": "2025-12-20",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            workbook_manifest_path = root / "workbook.json"
            workbook_manifest_path.write_text(
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
                        "rows": [],
                    }
                ),
                encoding="utf-8",
            )

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
            snapshot = build_email_snapshot(
                load_snapshot_manifest(snapshot_manifest_path),
                state_timezone="Asia/Dhaka",
            )
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=snapshot,
            )

            validation_result = validate_run_snapshot(
                descriptor=descriptor,
                run_report=initialized.run_report,
                rule_pack=rule_pack,
                erp_row_provider=JsonManifestERPRowProvider(erp_manifest_path),
                workbook_snapshot=JsonManifestWorkbookSnapshotProvider(workbook_manifest_path).load_snapshot(),
            )

            operations_by_column = {
                operation.column_key: operation.expected_post_write_value
                for operation in validation_result.staged_write_plan
            }
            self.assertEqual(operations_by_column["buyer_name"], "Ananta Garments Ltd., Dhaka.")
            self.assertEqual(operations_by_column["lc_issuing_bank"], "Abc Bank, Dhaka Branch")
            self.assertEqual(operations_by_column["lien_bank"], "Xyz Bank")

    def test_validate_run_snapshot_stages_multiple_new_mails_on_distinct_rows_when_one_mail_is_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(
                tz=validate_timezone("Asia/Dhaka")
            ).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            for directory in (report_root, run_root, backup_root, workbook_root):
                directory.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{workbook_root.as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_manifest_path = root / "snapshot.json"
            snapshot_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "LC-5392-CUTTING EDGE INDUSTRIES LTD_ACK",
                            "sender_address": "one@example.com",
                            "body_text": "Please process file P/26/0624 today.",
                        },
                        {
                            "entry_id": "entry-002",
                            "received_time": "2026-03-28T04:00:00Z",
                            "subject_raw": "LC-0107-ANANTA GARMENTS LTD_AMD_06 & 07",
                            "sender_address": "two@example.com",
                            "body_text": "Please process file P/26/0634 today.",
                        },
                        {
                            "entry_id": "entry-003",
                            "received_time": "2026-03-28T05:00:00Z",
                            "subject_raw": "LC-0476-SEAVIEW DRESSES LTD_D",
                            "sender_address": "three@example.com",
                            "body_text": "Please process file P/26/0635 today.",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            erp_manifest_path = root / "erp.json"
            erp_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "file_number": "P/26/0624",
                            "lc_sc_number": "DPCBD1175392",
                            "buyer_name": "CUTTING EDGE INDUSTRIES LTD\\1612",
                            "lc_sc_date": "2026-03-30",
                            "source_row_index": 5,
                        },
                        {
                            "file_number": "P/26/0634",
                            "lc_sc_number": "1558260400107",
                            "buyer_name": "ANANTA GARMENTS LTD\\NISCHINTAPUR ASHULIA DEPZ ROAD SAVAR DHAKA-1341 BANGLADESH",
                            "lc_sc_date": "2026-02-22",
                            "source_row_index": 6,
                            "notify_bank": "PRIME BANK PLC.\\TRADE SERVICES DIVISION",
                            "current_lc_value": "232365.00",
                            "ship_date": "30-Apr-26",
                            "expiry_date": "15-May-26",
                            "lc_qty": "83500.00",
                            "lc_unit": "YDS",
                            "amd_no": "06,07",
                            "amd_date": "31-Mar-2026",
                            "nego_bank": "MUTUAL TRUST BANK PLC\\BANANI",
                            "master_lc_no": "AGL/H&M/98/2025",
                            "master_lc_date": "18-Dec-25",
                        },
                        {
                            "file_number": "P/26/0635",
                            "lc_sc_number": "3053260400476",
                            "buyer_name": "SEAVIEW DRESSES LTD\\HAZRAT SHAHJALAL (RH ) ROAD RAJNOGOR SATAISH TONGI GAZIPUR",
                            "lc_sc_date": "2026-03-16",
                            "source_row_index": 7,
                            "notify_bank": "JAMUNA BANK PLC.\\BANANI BRANCH",
                            "current_lc_value": "2712.50",
                            "ship_date": "25-Mar-26",
                            "expiry_date": "09-Apr-26",
                            "lc_qty": "900.00",
                            "lc_unit": "YDS",
                            "amd_no": "",
                            "amd_date": "",
                            "nego_bank": "BRAC BANK PLC\\GULSHAN",
                            "master_lc_no": "CWF/SEAVIEW/AW-26-001",
                            "master_lc_date": "08-Feb-26",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            workbook_manifest_path = root / "workbook.json"
            workbook_manifest_path.write_text(
                json.dumps(
                    {
                        "sheet_name": "UP Issuing Status # 2026-2027",
                        "headers": [
                            {"column_index": 2, "text": "Name of Buyers"},
                            {"column_index": 3, "text": "L/C Issuing Bank"},
                            {"column_index": 4, "text": "L/C & S/C No."},
                            {"column_index": 5, "text": "LC Issue Date"},
                            {"column_index": 6, "text": "Amount"},
                            {"column_index": 7, "text": "Shipment Date"},
                            {"column_index": 8, "text": "Expiry Date"},
                            {"column_index": 9, "text": "Quantity of Fabrics (Yds/Mtr)"},
                            {"column_index": 10, "text": "L/C Amnd No."},
                            {"column_index": 11, "text": "L/C Amnd Date"},
                            {"column_index": 13, "text": "Lien Bank"},
                            {"column_index": 14, "text": "Master L/C No."},
                            {"column_index": 15, "text": "Master L/C Issue Dt."},
                            {"column_index": 29, "text": "Commercial File No."},
                        ],
                        "rows": [
                            {"row_index": 3, "values": {"2": "Cutting Edge Industries Ltd, 1612", "29": "P/26/0624"}},
                            {"row_index": 4, "values": {"2": "", "29": ""}},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
            snapshot = build_email_snapshot(
                load_snapshot_manifest(snapshot_manifest_path),
                state_timezone="Asia/Dhaka",
            )
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=snapshot,
            )

            validation_result = validate_run_snapshot(
                descriptor=descriptor,
                run_report=initialized.run_report,
                rule_pack=rule_pack,
                erp_row_provider=JsonManifestERPRowProvider(erp_manifest_path),
                workbook_snapshot=JsonManifestWorkbookSnapshotProvider(workbook_manifest_path).load_snapshot(),
            )

            self.assertEqual(validation_result.run_report.summary, {"pass": 3, "warning": 0, "hard_block": 0})
            self.assertEqual(len(validation_result.staged_write_plan), 28)
            self.assertEqual(validation_result.mail_outcomes[0].write_disposition, "duplicate_only_noop")
            self.assertFalse(validation_result.mail_outcomes[0].eligible_for_print)
            staged_dispositions = {
                outcome.write_disposition
                for outcome in validation_result.mail_outcomes[1:]
            }
            self.assertEqual(staged_dispositions, {"new_writes_staged"})
            self.assertTrue(all(outcome.eligible_for_print for outcome in validation_result.mail_outcomes[1:]))
            rows_by_mail = {}
            for operation in validation_result.staged_write_plan:
                rows_by_mail.setdefault(operation.mail_id, set()).add(operation.row_index)
            self.assertEqual(len(rows_by_mail), 2)
            self.assertEqual(sorted(next(iter(rows)) for rows in rows_by_mail.values()), [4, 5])
            self.assertIn(
                "Skipped workbook append for P/26/0624 because the file number already exists in the workbook.",
                validation_result.mail_outcomes[0].decision_reasons,
            )

    def test_validate_run_snapshot_skips_duplicate_file_number_in_workbook(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(
                tz=validate_timezone("Asia/Dhaka")
            ).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            for directory in (report_root, run_root, backup_root, workbook_root):
                directory.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{workbook_root.as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_manifest_path = root / "snapshot.json"
            snapshot_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "LC-0038-ANANTA GARMENTS LTD_AMD_05",
                            "sender_address": "one@example.com",
                            "body_text": "Please process file P/26/42 today.",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            erp_manifest_path = root / "erp.json"
            erp_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "file_number": "P/26/0042",
                            "lc_sc_number": "LC-0038",
                            "buyer_name": "ANANTA GARMENTS LTD",
                            "lc_sc_date": "2026-01-10",
                            "source_row_index": 5,
                            "notify_bank": "ABC BANK",
                            "current_lc_value": "10000",
                            "ship_date": "2026-02-01",
                            "expiry_date": "2026-03-01",
                            "lc_qty": "5000",
                            "lc_unit": "YDS",
                            "amd_no": "05",
                            "amd_date": "2026-01-15",
                            "nego_bank": "XYZ BANK",
                            "master_lc_no": "MLC-001",
                            "master_lc_date": "2025-12-20",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            workbook_manifest_path = root / "workbook.json"
            workbook_manifest_path.write_text(
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
                        "rows": [{"row_index": 3, "values": {"1": "P/26/0042"}}],
                    }
                ),
                encoding="utf-8",
            )

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
            snapshot = build_email_snapshot(
                load_snapshot_manifest(snapshot_manifest_path),
                state_timezone="Asia/Dhaka",
            )
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=snapshot,
            )

            validation_result = validate_run_snapshot(
                descriptor=descriptor,
                run_report=initialized.run_report,
                rule_pack=rule_pack,
                erp_row_provider=JsonManifestERPRowProvider(erp_manifest_path),
                workbook_snapshot=JsonManifestWorkbookSnapshotProvider(workbook_manifest_path).load_snapshot(),
            )

            self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.PASS)
            self.assertFalse(validation_result.mail_outcomes[0].eligible_for_write)
            self.assertFalse(validation_result.mail_outcomes[0].eligible_for_print)
            self.assertEqual(validation_result.staged_write_plan, [])
            self.assertEqual(validation_result.mail_outcomes[0].write_disposition, "duplicate_only_noop")
            self.assertIn(
                "Skipped workbook append for P/26/0042 because the file number already exists in the workbook.",
                validation_result.mail_outcomes[0].decision_reasons,
            )

    def test_validate_run_snapshot_marks_later_mail_as_duplicate_when_file_was_staged_earlier_in_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(
                tz=validate_timezone("Asia/Dhaka")
            ).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            for directory in (report_root, run_root, backup_root, workbook_root):
                directory.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{workbook_root.as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_manifest_path = root / "snapshot.json"
            snapshot_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "LC-0038-ANANTA GARMENTS LTD",
                            "sender_address": "one@example.com",
                            "body_text": "Please process file P/26/0042 today.",
                        },
                        {
                            "entry_id": "entry-002",
                            "received_time": "2026-03-28T04:00:00Z",
                            "subject_raw": "LC-0038-ANANTA GARMENTS LTD_ACK",
                            "sender_address": "two@example.com",
                            "body_text": "Please process file P/26/0042 today.",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            erp_manifest_path = root / "erp.json"
            erp_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "file_number": "P/26/0042",
                            "lc_sc_number": "LC-0038",
                            "buyer_name": "ANANTA GARMENTS LTD",
                            "lc_sc_date": "2026-01-10",
                            "source_row_index": 5,
                            "notify_bank": "ABC BANK",
                            "current_lc_value": "10000",
                            "ship_date": "2026-02-01",
                            "expiry_date": "2026-03-01",
                            "lc_qty": "5000",
                            "lc_unit": "YDS",
                            "amd_no": "05",
                            "amd_date": "2026-01-15",
                            "nego_bank": "XYZ BANK",
                            "master_lc_no": "MLC-001",
                            "master_lc_date": "2025-12-20",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            workbook_manifest_path = root / "workbook.json"
            workbook_manifest_path.write_text(
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
                        "rows": [{"row_index": 3, "values": {}}],
                    }
                ),
                encoding="utf-8",
            )

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
            snapshot = build_email_snapshot(
                load_snapshot_manifest(snapshot_manifest_path),
                state_timezone="Asia/Dhaka",
            )
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=snapshot,
            )

            validation_result = validate_run_snapshot(
                descriptor=descriptor,
                run_report=initialized.run_report,
                rule_pack=rule_pack,
                erp_row_provider=JsonManifestERPRowProvider(erp_manifest_path),
                workbook_snapshot=JsonManifestWorkbookSnapshotProvider(workbook_manifest_path).load_snapshot(),
            )

            self.assertEqual(len(validation_result.staged_write_plan), 14)
            self.assertTrue(validation_result.mail_outcomes[0].eligible_for_write)
            self.assertTrue(validation_result.mail_outcomes[0].eligible_for_print)
            self.assertFalse(validation_result.mail_outcomes[1].eligible_for_write)
            self.assertFalse(validation_result.mail_outcomes[1].eligible_for_print)
            self.assertEqual(validation_result.mail_outcomes[0].write_disposition, "new_writes_staged")
            self.assertEqual(validation_result.mail_outcomes[1].write_disposition, "duplicate_only_noop")
            self.assertIn(
                "Skipped workbook append for P/26/0042 because the file number was already staged earlier in this run.",
                validation_result.mail_outcomes[1].decision_reasons,
            )

    def test_validate_run_snapshot_marks_mail_as_mixed_when_one_file_is_duplicate_and_one_is_new(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(
                tz=validate_timezone("Asia/Dhaka")
            ).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            for directory in (report_root, run_root, backup_root, workbook_root):
                directory.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{workbook_root.as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_manifest_path = root / "snapshot.json"
            snapshot_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "LC-MIXED-ANANTA GARMENTS LTD",
                            "sender_address": "one@example.com",
                            "body_text": "Please process files P/26/0042 and P/26/0043 today.",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            erp_manifest_path = root / "erp.json"
            erp_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "file_number": "P/26/0042",
                            "lc_sc_number": "LC-0038",
                            "buyer_name": "ANANTA GARMENTS LTD",
                            "lc_sc_date": "2026-01-10",
                            "source_row_index": 5,
                            "notify_bank": "ABC BANK",
                            "current_lc_value": "10000",
                            "ship_date": "2026-02-01",
                            "expiry_date": "2026-03-01",
                            "lc_qty": "5000",
                            "lc_unit": "YDS",
                            "amd_no": "05",
                            "amd_date": "2026-01-15",
                            "nego_bank": "XYZ BANK",
                            "master_lc_no": "MLC-001",
                            "master_lc_date": "2025-12-20",
                        },
                        {
                            "file_number": "P/26/0043",
                            "lc_sc_number": "LC-0038",
                            "buyer_name": "ANANTA GARMENTS LTD",
                            "lc_sc_date": "2026-01-10",
                            "source_row_index": 6,
                            "notify_bank": "ABC BANK",
                            "current_lc_value": "10000",
                            "ship_date": "2026-02-01",
                            "expiry_date": "2026-03-01",
                            "lc_qty": "5000",
                            "lc_unit": "YDS",
                            "amd_no": "05",
                            "amd_date": "2026-01-15",
                            "nego_bank": "XYZ BANK",
                            "master_lc_no": "MLC-001",
                            "master_lc_date": "2025-12-20",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            workbook_manifest_path = root / "workbook.json"
            workbook_manifest_path.write_text(
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
                        "rows": [{"row_index": 3, "values": {"1": "P/26/0042"}}],
                    }
                ),
                encoding="utf-8",
            )

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
            snapshot = build_email_snapshot(
                load_snapshot_manifest(snapshot_manifest_path),
                state_timezone="Asia/Dhaka",
            )
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=snapshot,
            )

            validation_result = validate_run_snapshot(
                descriptor=descriptor,
                run_report=initialized.run_report,
                rule_pack=rule_pack,
                erp_row_provider=JsonManifestERPRowProvider(erp_manifest_path),
                workbook_snapshot=JsonManifestWorkbookSnapshotProvider(workbook_manifest_path).load_snapshot(),
            )

            self.assertEqual(validation_result.mail_outcomes[0].write_disposition, "mixed_duplicate_and_new_writes")
            self.assertTrue(validation_result.mail_outcomes[0].eligible_for_write)
            self.assertTrue(validation_result.mail_outcomes[0].eligible_for_print)
            self.assertEqual(len(validation_result.staged_write_plan), 14)

    def test_validate_run_snapshot_hard_blocks_invalid_workbook_header_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(
                tz=validate_timezone("Asia/Dhaka")
            ).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            for directory in (report_root, run_root, backup_root, workbook_root):
                directory.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{workbook_root.as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_manifest_path = root / "snapshot.json"
            snapshot_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "LC-0038-ANANTA GARMENTS LTD_AMD_05",
                            "sender_address": "one@example.com",
                            "body_text": "Please process file P/26/42 today.",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            erp_manifest_path = root / "erp.json"
            erp_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "file_number": "P/26/0042",
                            "lc_sc_number": "LC-0038",
                            "buyer_name": "ANANTA GARMENTS LTD",
                            "lc_sc_date": "2026-01-10",
                            "source_row_index": 5,
                            "notify_bank": "ABC BANK",
                            "current_lc_value": "10000",
                            "ship_date": "2026-02-01",
                            "expiry_date": "2026-03-01",
                            "lc_qty": "5000",
                            "lc_unit": "YDS",
                            "amd_no": "05",
                            "amd_date": "2026-01-15",
                            "nego_bank": "XYZ BANK",
                            "master_lc_no": "MLC-001",
                            "master_lc_date": "2025-12-20",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            workbook_manifest_path = root / "workbook.json"
            workbook_manifest_path.write_text(
                json.dumps(
                    {
                        "sheet_name": "Sheet1",
                        "headers": [
                            {"column_index": 1, "text": "File No."},
                            {"column_index": 2, "text": "L/C No."},
                            {"column_index": 3, "text": "Buyer Name"},
                            {"column_index": 6, "text": "Amount"},
                            {"column_index": 22, "text": "Amount"},
                        ],
                        "rows": [],
                    }
                ),
                encoding="utf-8",
            )

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
            snapshot = build_email_snapshot(
                load_snapshot_manifest(snapshot_manifest_path),
                state_timezone="Asia/Dhaka",
            )
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=snapshot,
            )

            validation_result = validate_run_snapshot(
                descriptor=descriptor,
                run_report=initialized.run_report,
                rule_pack=rule_pack,
                erp_row_provider=JsonManifestERPRowProvider(erp_manifest_path),
                workbook_snapshot=JsonManifestWorkbookSnapshotProvider(workbook_manifest_path).load_snapshot(),
            )

            self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.HARD_BLOCK)
            self.assertIn(
                "workbook_header_mapping_invalid",
                [report.code for report in validation_result.discrepancy_reports],
            )

    def test_validate_run_snapshot_saves_export_pdf_attachments_when_document_root_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(
                tz=validate_timezone("Asia/Dhaka")
            ).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            document_root = root / "documents"
            for directory in (report_root, run_root, backup_root, workbook_root, document_root):
                directory.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{workbook_root.as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_manifest_path = root / "snapshot.json"
            snapshot_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "LC-0038-ANANTA GARMENTS LTD_AMD_05",
                            "sender_address": "one@example.com",
                            "body_text": "Please process file P/26/42 today.",
                            "attachments": [
                                {"attachment_name": "LC.pdf"},
                                {"attachment_name": "notes.txt"},
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            erp_manifest_path = root / "erp.json"
            erp_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "file_number": "P/26/0042",
                            "lc_sc_number": "LC-0038",
                            "buyer_name": "ANANTA GARMENTS LTD",
                            "lc_sc_date": "2026-01-10",
                            "source_row_index": 5,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
            snapshot = build_email_snapshot(
                load_snapshot_manifest(snapshot_manifest_path),
                state_timezone="Asia/Dhaka",
            )
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=snapshot,
            )

            validation_result = validate_run_snapshot(
                descriptor=descriptor,
                run_report=initialized.run_report,
                rule_pack=rule_pack,
                erp_row_provider=JsonManifestERPRowProvider(erp_manifest_path),
                attachment_content_provider=SimulatedAttachmentContentProvider(
                    content_by_key={(snapshot[0].entry_id, 0): b"%PDF-1.4\nsaved lc\n"}
                ),
                document_root=document_root,
            )

            self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.PASS)
            self.assertEqual(len(validation_result.mail_outcomes[0].saved_documents), 1)
            self.assertEqual(
                validation_result.mail_outcomes[0].saved_documents[0]["save_decision"],
                "saved_new",
            )
            self.assertEqual(
                validation_result.mail_outcomes[0].saved_documents[0]["document_type"],
                "export_lc_sc_document",
            )
            self.assertTrue(validation_result.mail_outcomes[0].saved_documents[0]["print_eligible"])
            self.assertTrue(
                validation_result.mail_outcomes[0].saved_documents[0]["destination_path"].replace("\\", "/").endswith(
                    "2026/ANANTA GARMENTS LTD/LC-0038/All Attachments/LC.pdf"
                )
            )
            self.assertEqual(
                [report.code for report in validation_result.discrepancy_reports],
                [],
            )

    def test_validate_run_snapshot_uses_document_analysis_manifest_for_export_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(
                tz=validate_timezone("Asia/Dhaka")
            ).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            document_root = root / "documents"
            for directory in (report_root, run_root, backup_root, workbook_root, document_root):
                directory.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{workbook_root.as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_manifest_path = root / "snapshot.json"
            snapshot_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "LC-0038-ANANTA GARMENTS LTD_AMD_05",
                            "sender_address": "one@example.com",
                            "body_text": "Please process file P/26/42 today.",
                            "attachments": [
                                {"attachment_name": "LC-0038.pdf"},
                                {"attachment_name": "supporting.pdf"},
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            erp_manifest_path = root / "erp.json"
            erp_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "file_number": "P/26/0042",
                            "lc_sc_number": "LC-0038",
                            "buyer_name": "ANANTA GARMENTS LTD",
                            "lc_sc_date": "2026-01-10",
                            "source_row_index": 5,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            analysis_manifest_path = root / "document-analysis.json"
            analysis_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "normalized_filename": "supporting.pdf",
                            "extracted_pi_number": "PDL-26-0042",
                            "extracted_amendment_number": "05",
                            "extracted_pi_page_number": 2,
                            "clause_related_lc_sc_number": "LC-0038",
                            "clause_excerpt": "PI PDL-26-0042 belongs to LC-0038",
                            "clause_confidence": 0.99,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
            snapshot = build_email_snapshot(
                load_snapshot_manifest(snapshot_manifest_path),
                state_timezone="Asia/Dhaka",
            )
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=snapshot,
            )

            validation_result = validate_run_snapshot(
                descriptor=descriptor,
                run_report=initialized.run_report,
                rule_pack=rule_pack,
                erp_row_provider=JsonManifestERPRowProvider(erp_manifest_path),
                attachment_content_provider=SimulatedAttachmentContentProvider(
                    content_by_key={
                        (snapshot[0].entry_id, 0): b"%PDF-1.4\nsaved lc\n",
                        (snapshot[0].entry_id, 1): b"%PDF-1.4\nsaved generic\n",
                    }
                ),
                document_root=document_root,
                document_analysis_provider=JsonManifestSavedDocumentAnalysisProvider(analysis_manifest_path),
            )

            self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.PASS)
            self.assertEqual(
                [document["document_type"] for document in validation_result.mail_outcomes[0].saved_documents],
                ["export_lc_sc_document", "export_pi_document"],
            )
            self.assertEqual(
                [document["print_eligible"] for document in validation_result.mail_outcomes[0].saved_documents],
                [True, True],
            )
            self.assertEqual(
                validation_result.mail_outcomes[0].saved_documents[1]["analysis_basis"],
                "json_manifest",
            )
            self.assertEqual(
                validation_result.mail_outcomes[0].saved_documents[1]["extracted_pi_number"],
                "PDL-26-0042",
            )
            self.assertEqual(
                validation_result.mail_outcomes[0].saved_documents[1]["extracted_amendment_number"],
                "5",
            )
            self.assertEqual(
                validation_result.mail_outcomes[0].saved_documents[1]["extracted_pi_provenance"]["page_number"],
                2,
            )

    def test_validate_run_snapshot_keeps_low_confidence_ocr_documents_non_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(
                tz=validate_timezone("Asia/Dhaka")
            ).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            document_root = root / "documents"
            for directory in (report_root, run_root, backup_root, workbook_root, document_root):
                directory.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{workbook_root.as_posix()}"',
                        'erp_base_url = "https://erp.local"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_manifest_path = root / "snapshot.json"
            snapshot_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "LC-0038-ANANTA GARMENTS LTD_AMD_05",
                            "sender_address": "one@example.com",
                            "body_text": "Please process file P/26/42 today.",
                            "attachments": [
                                {"attachment_name": "LC-0038.pdf"},
                                {"attachment_name": "scan.pdf"},
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            erp_manifest_path = root / "erp.json"
            erp_manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "file_number": "P/26/0042",
                            "lc_sc_number": "LC-0038",
                            "buyer_name": "ANANTA GARMENTS LTD",
                            "lc_sc_date": "2026-01-10",
                            "source_row_index": 5,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
            snapshot = build_email_snapshot(
                load_snapshot_manifest(snapshot_manifest_path),
                state_timezone="Asia/Dhaka",
            )
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=snapshot,
            )

            class OCRLikeProvider:
                def analyze(self, *, saved_document):
                    from project.documents import SavedDocumentAnalysis

                    if saved_document.normalized_filename == "scan.pdf":
                        return SavedDocumentAnalysis(
                            analysis_basis="ocr_text",
                            extracted_pi_number="PDL-26-0042",
                            extracted_pi_confidence=0.94,
                            clause_confidence=0.94,
                            extracted_pi_provenance={
                                "page_number": 3,
                                "extraction_method": "ocr",
                                "confidence": 0.94,
                            },
                        )
                    return SavedDocumentAnalysis(
                        analysis_basis="pymupdf_text",
                        extracted_lc_sc_number="LC-0038",
                        extracted_lc_sc_confidence=1.0,
                        clause_confidence=1.0,
                    )

            validation_result = validate_run_snapshot(
                descriptor=descriptor,
                run_report=initialized.run_report,
                rule_pack=rule_pack,
                erp_row_provider=JsonManifestERPRowProvider(erp_manifest_path),
                attachment_content_provider=SimulatedAttachmentContentProvider(
                    content_by_key={
                        (snapshot[0].entry_id, 0): b"%PDF-1.4\nsaved lc\n",
                        (snapshot[0].entry_id, 1): b"%PDF-1.4\nsaved scan\n",
                    }
                ),
                document_root=document_root,
                document_analysis_provider=OCRLikeProvider(),
            )

            self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.PASS)
            self.assertEqual(validation_result.discrepancy_reports, [])
            self.assertTrue(
                all(document["print_eligible"] for document in validation_result.mail_outcomes[0].saved_documents)
            )


if __name__ == "__main__":
    unittest.main()

