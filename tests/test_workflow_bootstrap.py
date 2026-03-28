from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project.config import load_workflow_config
from project.models.enums import WorkflowId
from project.rules import load_rule_pack
from project.utils.time import validate_timezone
from project.workflows.bootstrap import initialize_workflow_run
from project.workflows.registry import get_workflow_descriptor


class WorkflowBootstrapTests(unittest.TestCase):
    def test_initialize_workflow_run_creates_required_artifacts(self) -> None:
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

            descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)

            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
            )

            self.assertTrue(initialized.artifact_paths.run_metadata_path.exists())
            self.assertTrue(initialized.artifact_paths.staged_write_plan_path.exists())
            self.assertTrue(initialized.artifact_paths.print_plan_path.exists())
            self.assertTrue(initialized.artifact_paths.mail_outcomes_path.exists())
            self.assertTrue(initialized.artifact_paths.discrepancies_path.exists())
            self.assertTrue(initialized.artifact_paths.backup_workbook_path.exists())
            self.assertTrue(initialized.artifact_paths.backup_hash_path.exists())


if __name__ == "__main__":
    unittest.main()
