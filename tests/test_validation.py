from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project.config import load_workflow_config
from project.models import FinalDecision, WorkflowId
from project.reporting.persistence import write_discrepancies, write_mail_outcomes, write_run_metadata
from project.rules import load_rule_pack
from project.utils.json import to_jsonable
from project.utils.time import validate_timezone
from project.workflows.bootstrap import initialize_workflow_run
from project.workflows.registry import get_workflow_descriptor
from project.workflows.snapshot import build_email_snapshot, load_snapshot_manifest
from project.workflows.validation import validate_run_snapshot


class ValidationTests(unittest.TestCase):
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
                        'outlook_profile = "Operations"',
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
                            "subject_raw": "Mail one",
                            "sender_address": "one@example.com",
                        },
                        {
                            "entry_id": "entry-002",
                            "received_time": "2026-03-28T04:00:00Z",
                            "subject_raw": "Mail two",
                            "sender_address": "two@example.com",
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
            self.assertEqual(initialized.artifact_paths.discrepancies_path.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
