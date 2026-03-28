from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from project.cli import main
from project.workbook import WorkbookHeader
from project.models import WorkflowId


class CLITests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
