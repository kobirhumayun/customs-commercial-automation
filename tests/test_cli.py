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


if __name__ == "__main__":
    unittest.main()
