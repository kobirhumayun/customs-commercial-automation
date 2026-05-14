from __future__ import annotations

import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout

from project.cli import main
from project.config import load_workflow_config
from project.erp import JsonManifestERPRowProvider
from project.models import WorkbookSessionPreflight, WorkflowId, WritePhaseStatus
from project.rules import load_rule_pack
from project.workbook import JsonManifestWorkbookSnapshotProvider, WorkbookWriteSessionResult
from project.workflows.bb_dashboard_verification import (
    DashboardCandidateFamily,
    DashboardCandidateRow,
    ERPFamilyAggregate,
    _build_report_html,
    _compare_dashboard_snapshot,
    validate_bb_dashboard_verification_run,
)
from project.workflows.bb_dashboard_verification.providers import (
    DashboardFamilySnapshot,
    JsonManifestDashboardLookupProvider,
    _read_text,
)
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

    def test_warning_result_still_stages_dashboard_status_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path, workbook_manifest_path, erp_manifest_path, dashboard_manifest_path = _write_dashboard_warning_fixture_bundle(root)

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

        self.assertEqual(workflow_result.validation_result.run_report.summary, {"pass": 0, "warning": 1, "hard_block": 0})
        self.assertEqual(len(workflow_result.validation_result.mail_outcomes), 1)
        outcome = workflow_result.validation_result.mail_outcomes[0]
        self.assertTrue(outcome.eligible_for_write)
        self.assertEqual(outcome.write_disposition, "new_writes_staged")
        self.assertEqual(len(outcome.staged_write_operations), 1)
        self.assertEqual(outcome.staged_write_operations[0]["column_key"], "dashboard_status")
        self.assertIn("Quantity mismatch", outcome.staged_write_operations[0]["expected_post_write_value"])

    def test_report_html_renders_dashboard_columns_with_quantity_sum_and_foreign_lc_breaks(self) -> None:
        report_html = _build_report_html(
            report_payload={
                "run_id": "run-1",
                "generated_at_utc": "2026-05-14T00:00:00Z",
                "families": [
                    {
                        "lc_sc_no": "2159260400534",
                        "sl_no_values": ["633.0"],
                        "workbook_master_lc_values": ["COG/VDAL/08/2025"],
                        "erp": {
                            "buyer_name": "VINTAGE DENIM APPARELS LTD",
                            "current_lc_value": "98315.5",
                            "lc_qty": "33170",
                        },
                        "final_workbook_value": "IRC Details did not contain the ERP buyer name.",
                        "written_shipment_date": "",
                        "written_expiry_date": "",
                        "dashboard": {
                            "beneficiary_name": "PIONEER DENIM LIMITED",
                            "irc_details": "TUSUKA FASHIONS LTD., BLOCK-B,TONGI I/A,GAZIPUR",
                            "erc_details": "Tusuka Fashions Ltd., Plot-10,Block-B, Shahid Sundar Ali Road, P.S. Tongi ,Gazipur-1710",
                            "lc_date": "22-Apr-2026",
                            "last_date_of_shipment": "15-Jun-2026",
                            "lc_expiry_date": "30-Jun-2026",
                            "lc_value": "98315.5",
                            "foreign_lc_numbers": ["COG/VDAL/08/2025", "EXTRA-FLC-002"],
                            "commodity_quantities": ["4850", "3670", "24650"],
                        },
                    }
                ],
            }
        )

        self.assertIn("Dashboard Beneficiary", report_html)
        self.assertIn("Dashboard Foreign LC No", report_html)
        self.assertIn("Dashboard Quantity Total", report_html)
        self.assertIn("COG/VDAL/08/2025<br>EXTRA-FLC-002", report_html)
        self.assertIn(">33170<", report_html)

    def test_read_text_uses_input_value_for_form_controls(self) -> None:
        class FakeLocator:
            def __init__(self, *, tag_name: str, input_value: str = "", text_content: str = "") -> None:
                self.first = self
                self._tag_name = tag_name
                self._input_value = input_value
                self._text_content = text_content

            def wait_for(self, **kwargs) -> None:
                return None

            def evaluate(self, expression: str):
                if "tagName" not in expression:
                    raise AssertionError("Expected tagName lookup")
                return self._tag_name

            def input_value(self) -> str:
                return self._input_value

            def text_content(self) -> str:
                return self._text_content

        class FakePage:
            def __init__(self, locator: FakeLocator) -> None:
                self._locator = locator

            def locator(self, selector: str) -> FakeLocator:
                return self._locator

        locator = FakeLocator(tag_name="TEXTAREA", input_value="PIONEER DENIM LIMITED")
        page = FakePage(locator)

        self.assertEqual(_read_text(page, "#P75_BENEFICIARY_NAME"), "PIONEER DENIM LIMITED")

    def test_read_text_falls_back_to_text_content_for_non_form_elements(self) -> None:
        class FakeLocator:
            def __init__(self, *, tag_name: str, input_value: str = "", text_content: str = "") -> None:
                self.first = self
                self._tag_name = tag_name
                self._input_value = input_value
                self._text_content = text_content

            def wait_for(self, **kwargs) -> None:
                return None

            def evaluate(self, expression: str):
                if "tagName" not in expression:
                    raise AssertionError("Expected tagName lookup")
                return self._tag_name

            def input_value(self) -> str:
                return self._input_value

            def text_content(self) -> str:
                return self._text_content

        class FakePage:
            def __init__(self, locator: FakeLocator) -> None:
                self._locator = locator

            def locator(self, selector: str) -> FakeLocator:
                return self._locator

        locator = FakeLocator(tag_name="TD", text_content="TDL-199")
        page = FakePage(locator)

        self.assertEqual(
            _read_text(page, "xpath=//table[@id='report_R483371920050373531']//tr[td]/td[2]"),
            "TDL-199",
        )

    def test_compare_dashboard_snapshot_normalizes_dates_before_comparison(self) -> None:
        family = DashboardCandidateFamily(
            family_id="mail-1",
            lc_sc_no="LC-001",
            lc_sc_key="LC 001",
            row_indexes=[11],
            sl_no_values=["101"],
            master_lc_values=["MLC-001"],
            rows=[
                DashboardCandidateRow(
                    row_index=11,
                    sl_no="101",
                    lc_sc_no="LC-001",
                    lc_sc_key="LC 001",
                    master_lc_values=["MLC-001"],
                    dashboard_status="",
                    shipment_date="",
                    expiry_date="",
                    number_formats={},
                )
            ],
        )
        aggregate = ERPFamilyAggregate(
            lc_sc_no="LC-001",
            lc_sc_key="LC 001",
            buyer_name="ANANTA GARMENTS LTD",
            lc_date="10-Jan-26",
            ship_date="10-Feb-26",
            expiry_date="10-Mar-26",
            current_lc_value=50,
            lc_qty=40,
            net_weight=18,
            ship_remarks=None,
            source_row_count=1,
        )
        snapshot = DashboardFamilySnapshot(
            beneficiary_name="PIONEER DENIM LIMITED",
            irc_details="ANANTA GARMENTS LTD",
            erc_details="ANANTA GARMENTS LTD",
            lc_date="2026-01-10",
            last_date_of_shipment="2026-02-10",
            lc_expiry_date="2026-03-10",
            lc_value="50",
            foreign_lc_numbers=["MLC-001"],
            commodity_quantities=["40"],
        )

        comparison = _compare_dashboard_snapshot(
            family=family,
            aggregate=aggregate,
            snapshot=snapshot,
        )

        self.assertEqual(comparison["status"], "OK")
        self.assertEqual(comparison["decision_reasons"], ["Dashboard quantity matched ERP LC quantity."])

    def test_compare_dashboard_snapshot_does_not_add_quantity_mismatch_when_quantity_matches(self) -> None:
        family = DashboardCandidateFamily(
            family_id="mail-2",
            lc_sc_no="LC-002",
            lc_sc_key="LC 002",
            row_indexes=[12],
            sl_no_values=["102"],
            master_lc_values=["MLC-002"],
            rows=[
                DashboardCandidateRow(
                    row_index=12,
                    sl_no="102",
                    lc_sc_no="LC-002",
                    lc_sc_key="LC 002",
                    master_lc_values=["MLC-002"],
                    dashboard_status="",
                    shipment_date="",
                    expiry_date="",
                    number_formats={},
                )
            ],
        )
        aggregate = ERPFamilyAggregate(
            lc_sc_no="LC-002",
            lc_sc_key="LC 002",
            buyer_name="VINTAGE DENIM APPARELS LTD",
            lc_date="22-Apr-26",
            ship_date="15-Jun-26",
            expiry_date="30-Jun-26",
            current_lc_value=Decimal("98315.5"),
            lc_qty=Decimal("33170"),
            net_weight=Decimal("21054.11"),
            ship_remarks=None,
            source_row_count=1,
        )
        snapshot = DashboardFamilySnapshot(
            beneficiary_name="PIONEER DENIM LIMITED",
            irc_details="TUSUKA FASHIONS LTD.",
            erc_details="TUSUKA FASHIONS LTD.",
            lc_date="22-Apr-2026",
            last_date_of_shipment="15-Jun-2026",
            lc_expiry_date="30-Jun-2026",
            lc_value="98315.5",
            foreign_lc_numbers=["MLC-002"],
            commodity_quantities=["4850", "3670", "24650"],
        )

        comparison = _compare_dashboard_snapshot(
            family=family,
            aggregate=aggregate,
            snapshot=snapshot,
        )

        self.assertNotIn("Quantity mismatch", comparison["status"])
        self.assertEqual(
            comparison["decision_reasons"],
            [
                "IRC Details did not contain the ERP buyer name.",
                "ERC Details did not contain the ERP buyer name.",
            ],
        )


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


def _write_dashboard_warning_fixture_bundle(root: Path) -> tuple[Path, Path, Path, Path]:
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

    workbook_manifest_path = root / "bb-warning-workbook.json"
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
                            "2": "LC-WARN-001",
                            "3": "",
                            "4": "",
                            "5": "MLC-WARN-001",
                            "6": "BGMEA/DHK/UD/2026/100/001",
                            "7": "",
                            "8": "",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    erp_manifest_path = root / "bb-warning-erp.json"
    erp_manifest_path.write_text(
        json.dumps(
            [
                {
                    "file_number": "P/26/9001",
                    "lc_sc_number": "LC-WARN-001",
                    "buyer_name": "ANANTA GARMENTS LTD\\DHAKA.",
                    "lc_sc_date": "2026-01-10",
                    "source_row_index": 1,
                    "current_lc_value": "50",
                    "ship_date": "2026-02-10",
                    "expiry_date": "2026-03-10",
                    "lc_qty": "20",
                    "net_weight": "18",
                    "ship_remarks": "",
                }
            ]
        ),
        encoding="utf-8",
    )

    dashboard_manifest_path = root / "bb-warning-dashboard.json"
    dashboard_manifest_path.write_text(
        json.dumps(
            [
                {
                    "search_key": "LC-WARN-001",
                    "outcome": "resolved",
                    "beneficiary_name": "PIONEER DENIM LIMITED",
                    "irc_details": "ANANTA GARMENTS LTD",
                    "erc_details": "ANANTA GARMENTS LTD",
                    "lc_date": "2026-01-10",
                    "last_date_of_shipment": "2026-02-10",
                    "lc_expiry_date": "2026-03-10",
                    "lc_value": "50",
                    "foreign_lc_numbers": ["MLC-WARN-001"],
                    "commodity_quantities": ["10"],
                }
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
