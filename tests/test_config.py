from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project.exceptions import ConfigError
from project.config import load_workflow_config
from project.models.enums import WorkflowId
from project.utils.time import validate_timezone
from project.workflows.registry import get_workflow_descriptor


class ConfigLoadingTests(unittest.TestCase):
    def test_cli_overrides_take_precedence_over_env_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(tz=validate_timezone("Asia/Dhaka")).year
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
                        'outlook_profile = "FileProfile"',
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
            config = load_workflow_config(
                descriptor=descriptor,
                config_path=config_path,
                overrides={"outlook_profile": "CliProfile"},
                environment={"CCA_OUTLOOK_PROFILE": "EnvProfile"},
            )

            self.assertEqual(config.state_timezone, "Asia/Dhaka")
            self.assertEqual(config.values["outlook_profile"], "CliProfile")
            self.assertTrue(config.print_enabled)
            self.assertEqual(
                config.resolve_master_workbook_path(workflow_year),
                workbook_root / f"{workflow_year}-master.xlsx",
            )

    def test_resolve_existing_master_workbook_path_raises_clear_error_when_yearly_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(tz=validate_timezone("Asia/Dhaka")).year
            report_root = root / "reports"
            run_root = root / "runs"
            backup_root = root / "backups"
            workbook_root = root / "workbooks"
            for directory in (report_root, run_root, backup_root, workbook_root):
                directory.mkdir(parents=True, exist_ok=True)

            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "FileProfile"',
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
            config = load_workflow_config(
                descriptor=descriptor,
                config_path=config_path,
            )

            with self.assertRaises(ConfigError) as exc_info:
                config.resolve_existing_master_workbook_path(workflow_year)

            self.assertIn(str(workbook_root / f"{workflow_year}-master.xlsx"), str(exc_info.exception))
            self.assertIn("Place the real yearly workbook", str(exc_info.exception))

    def test_load_workflow_config_creates_missing_managed_roots_for_fresh_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(tz=validate_timezone("Asia/Dhaka")).year
            report_root = root / "reports"
            run_root = root / "state" / "runs"
            backup_root = root / "state" / "backups"
            workbook_root = root / "workbooks"
            workbook_root.mkdir(parents=True, exist_ok=True)

            (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{report_root.as_posix()}"',
                        f'run_artifact_root = "{run_root.as_posix()}"',
                        f'backup_root = "{backup_root.as_posix()}"',
                        'outlook_profile = "FileProfile"',
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
            config = load_workflow_config(
                descriptor=descriptor,
                config_path=config_path,
            )

            self.assertEqual(config.report_root, report_root)
            self.assertTrue(report_root.is_dir())
            self.assertTrue(run_root.is_dir())
            self.assertTrue(backup_root.is_dir())

    def test_load_workflow_config_applies_centralized_optional_defaults_and_env_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_year = __import__("datetime").datetime.now(tz=validate_timezone("Asia/Dhaka")).year
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
                        'outlook_profile = "FileProfile"',
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
            config = load_workflow_config(
                descriptor=descriptor,
                config_path=config_path,
                environment={"CCA_PRINT_COMMAND_TIMEOUT_SECONDS": "180"},
            )

            self.assertEqual(
                config.values["erp_lc_register_relative_url"],
                "/RptCommercialExport/DateWiseLCRegisterForDocuments",
            )
            self.assertEqual(config.values["erp_report_default_lookback_days"], 365)
            self.assertEqual(config.values["bb_dashboard_back_link_text"], "Back")
            self.assertEqual(config.values["print_command_timeout_seconds"], 180)


if __name__ == "__main__":
    unittest.main()
