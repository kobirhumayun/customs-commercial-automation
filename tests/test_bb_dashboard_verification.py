from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout

from project.cli import main
from project.config import load_workflow_config
from project.erp import JsonManifestERPRowProvider
from project.models import WorkbookSessionPreflight, WorkflowId, WritePhaseStatus
from project.rules import load_rule_pack
from project.workbook import JsonManifestWorkbookSnapshotProvider, WorkbookWriteSessionResult
from project.workflows.bb_dashboard_verification import validate_bb_dashboard_verification_run
from project.workflows.bb_dashboard_verification.providers import JsonManifestDashboardLookupProvider
from project.workflows.bootstrap import initialize_workflow_run
from project.workflows.registry import get_workflow_descriptor
from project.workflows.write_preparation import prepare_live_write_batch


class BBDashboardVerificationTests(unittest.TestCase):
    def test_validate_run_cli_persists_bb_dashboard_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path, workbook_manifest_path, erp_manifest_path, dashboard_manifest_path = _write_dashboard_fixture_bundle(root)

            with patch("project.cli.open_bb_dashboard_verification_report_in_browser") as browser_open_mock:
                browser_open_mock.return_value = None
                stdout_buffer = io.StringIO()
                with redirect_stdout(stdout_buffer):
                    exit_code = main(
                        [
                            "validate-run",
                            "bb_dashboard_verification",
                            "--config",
                            str(config_path),
                            "--workbook-json",
                            str(workbook_manifest_path),
                            "--erp-json",
                            str(erp_manifest_path),
                            "--dashboard-json",
                            str(dashboard_manifest_path),
                        ]
                    )

            self.assertEqual(exit_code, 0)
            browser_open_mock.assert_called_once()

            artifact_root = Path(browser_open_mock.call_args.kwargs["html_path"]).parent
            run_metadata = json.loads((artifact_root / "run_metadata.json").read_text(encoding="utf-8"))
            report_payload = json.loads((artifact_root / "bb_dashboard_verification_report.json").read_text(encoding="utf-8"))
            mail_outcomes = _read_jsonl(artifact_root / "mail_outcomes.jsonl")
            discrepancies = _read_jsonl(artifact_root / "discrepancies.jsonl")

        self.assertEqual(run_metadata["summary"], {"pass": 2, "warning": 0, "hard_block": 1})
        self.assertEqual(report_payload["family_count"], 3)
        self.assertEqual(report_payload["families"][0]["final_workbook_value"], "OK")
        self.assertEqual(report_payload["families"][1]["final_workbook_value"], "OK (KGS)")
        self.assertIsNone(report_payload["families"][2]["final_workbook_value"])
        self.assertEqual(len(mail_outcomes), 3)
        self.assertEqual(sum(len(item["staged_write_operations"]) for item in mail_outcomes), 9)
        self.assertEqual([item["code"] for item in discrepancies], ["bb_dashboard_family_input_invalid"])

    def test_prepare_live_write_batch_supports_bb_dashboard_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path, workbook_manifest_path, erp_manifest_path, dashboard_manifest_path = _write_dashboard_fixture_bundle(root)

            descriptor = get_workflow_descriptor(WorkflowId.BB_DASHBOARD_VERIFICATION)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.BB_DASHBOARD_VERIFICATION)
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=[],
            )
            workbook_snapshot = JsonManifestWorkbookSnapshotProvider(workbook_manifest_path).load_snapshot()
            workflow_result = validate_bb_dashboard_verification_run(
                run_report=initialized.run_report,
                workbook_snapshot=workbook_snapshot,
                erp_rows=JsonManifestERPRowProvider(erp_manifest_path).load_rows(),
                dashboard_provider=JsonManifestDashboardLookupProvider(dashboard_manifest_path),
            )

            class FakeSessionProvider:
                def open_preflight_session(self, *, operator_context, max_attempts=3):
                    return WorkbookWriteSessionResult(
                        preflight=WorkbookSessionPreflight(
                            workbook_path=str(root / "workbooks" / "2026-master.xlsx"),
                            adapter_name="fake-xlwings",
                            status="ready",
                            attempt_count=1,
                            host_name=operator_context.host_name if operator_context else "host",
                            process_id=operator_context.process_id if operator_context else 1,
                            session_id="excel-session-001",
                            opened_at_utc="2026-05-14T00:00:00Z",
                            read_only=False,
                            save_capable=True,
                        ),
                        snapshot=workbook_snapshot,
                    )

            prepared = prepare_live_write_batch(
                validation_result=workflow_result.validation_result,
                workbook_path=root / "workbooks" / "2026-master.xlsx",
                operator_context=initialized.run_report.operator_context,
                session_provider=FakeSessionProvider(),
            )

        self.assertEqual(prepared.run_report.write_phase_status, WritePhaseStatus.PREVALIDATED)
        self.assertEqual(prepared.run_report.target_prevalidation_summary.status, "passed")
        self.assertEqual(len(prepared.target_probes), 9)
        self.assertTrue(all(probe.classification == "matches_pre_write" for probe in prepared.target_probes))


def _write_dashboard_fixture_bundle(root: Path) -> tuple[Path, Path, Path, Path]:
    report_root = root / "reports"
    run_root = root / "runs"
    backup_root = root / "backups"
    workbook_root = root / "workbooks"
    for directory in (report_root, run_root, backup_root, workbook_root):
        directory.mkdir(parents=True, exist_ok=True)

    workbook_path = workbook_root / "2026-master.xlsx"
    workbook_path.write_bytes(b"fake workbook")

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
                f'master_workbook_path_template = "{workbook_path.as_posix()}"',
                "excel_lock_timeout_seconds = 60",
                "print_enabled = false",
            ]
        ),
        encoding="utf-8",
    )

    workbook_manifest_path = root / "bb-workbook.json"
    workbook_manifest_path.write_text(
        json.dumps(
            {
                "sheet_name": "Sheet1",
                "headers": [
                    {"column_index": 1, "text": "SL.No."},
                    {"column_index": 2, "text": "L/C & S/C No."},
                    {"column_index": 3, "text": "Shipment Date"},
                    {"column_index": 4, "text": "Expiry Date"},
                    {"column_index": 5, "text": "Master L/C No."},
                    {"column_index": 6, "text": "UD No. & IP No."},
                    {"column_index": 7, "text": "UP No."},
                    {"column_index": 8, "text": "Bangladesh Bank Dashboard"},
                ],
                "rows": [
                    {
                        "row_index": 11,
                        "values": {
                            "1": "101",
                            "2": "LC-001",
                            "3": "",
                            "4": "",
                            "5": "MLC-001\nMLC-002",
                            "6": "BGMEA/DHK/UD/2026/100/001",
                            "7": "",
                            "8": "",
                        },
                    },
                    {
                        "row_index": 12,
                        "values": {
                            "1": "102",
                            "2": "LC-001",
                            "3": "",
                            "4": "",
                            "5": "MLC-002",
                            "6": "BGMEA/DHK/UD/2026/100/001",
                            "7": "",
                            "8": "Pending",
                        },
                    },
                    {
                        "row_index": 13,
                        "values": {
                            "1": "103",
                            "2": "LC-002",
                            "3": "",
                            "4": "",
                            "5": "MLC-003",
                            "6": "BGMEA/DHK/UD/2026/200/001",
                            "7": "",
                            "8": "",
                        },
                    },
                    {
                        "row_index": 14,
                        "values": {
                            "1": "104",
                            "2": "LC-003",
                            "3": "",
                            "4": "",
                            "5": "MLC-004",
                            "6": "BGMEA/DHK/UD/2026/300/001",
                            "7": "",
                            "8": "",
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    erp_manifest_path = root / "bb-erp.json"
    erp_manifest_path.write_text(
        json.dumps(
            [
                {
                    "file_number": "P/26/0001",
                    "lc_sc_number": "LC-001",
                    "buyer_name": "ANANTA GARMENTS LTD\\DHAKA.",
                    "lc_sc_date": "2026-01-10",
                    "source_row_index": 1,
                    "current_lc_value": "50",
                    "ship_date": "2026-02-10",
                    "expiry_date": "2026-03-10",
                    "lc_qty": "20",
                    "net_weight": "18",
                    "ship_remarks": "REF1000",
                },
                {
                    "file_number": "P/26/0002",
                    "lc_sc_number": "LC-001",
                    "buyer_name": "ANANTA GARMENTS LTD\\DHAKA.",
                    "lc_sc_date": "2026-01-10",
                    "source_row_index": 2,
                    "current_lc_value": "50",
                    "ship_date": "2026-02-10",
                    "expiry_date": "2026-03-10",
                    "lc_qty": "20",
                    "net_weight": "18",
                    "ship_remarks": "REF1000",
                },
                {
                    "file_number": "P/26/0003",
                    "lc_sc_number": "LC-002",
                    "buyer_name": "BEXIMCO APPARELS LTD\\DHAKA.",
                    "lc_sc_date": "2026-01-11",
                    "source_row_index": 3,
                    "current_lc_value": "80",
                    "ship_date": "2026-02-11",
                    "expiry_date": "2026-03-11",
                    "lc_qty": "100",
                    "net_weight": "120",
                    "ship_remarks": "",
                },
            ]
        ),
        encoding="utf-8",
    )

    dashboard_manifest_path = root / "bb-dashboard.json"
    dashboard_manifest_path.write_text(
        json.dumps(
            [
                {
                    "search_key": "REF1000",
                    "outcome": "resolved",
                    "beneficiary_name": "PIONEER DENIM LIMITED",
                    "irc_details": "ANANTA GARMENTS LTD",
                    "erc_details": "ANANTA GARMENTS LTD",
                    "lc_date": "2026-01-10",
                    "last_date_of_shipment": "2026-02-10",
                    "lc_expiry_date": "2026-03-10",
                    "lc_value": "100",
                    "foreign_lc_numbers": ["MLC-002"],
                    "commodity_quantities": ["40"],
                },
                {
                    "search_key": "LC0-002",
                    "outcome": "resolved",
                    "beneficiary_name": "PIONEER DENIM LIMITED",
                    "irc_details": "BEXIMCO APPARELS LTD",
                    "erc_details": "BEXIMCO APPARELS LTD",
                    "lc_date": "2026-01-11",
                    "last_date_of_shipment": "2026-02-11",
                    "lc_expiry_date": "2026-03-11",
                    "lc_value": "80",
                    "foreign_lc_numbers": ["MLC-003"],
                    "commodity_quantities": ["120"],
                },
            ]
        ),
        encoding="utf-8",
    )
    return config_path, workbook_manifest_path, erp_manifest_path, dashboard_manifest_path


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


if __name__ == "__main__":
    unittest.main()
