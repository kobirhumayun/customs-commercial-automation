from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout

from project.cli import main
from project.config import load_workflow_config
from project.erp import JsonManifestERPRowProvider
from project.models import FinalDecision, WorkbookSessionPreflight, WorkflowId, WritePhaseStatus
from project.rules import load_rule_pack
from project.workbook import (
    JsonManifestWorkbookSnapshotProvider,
    WorkbookHeader,
    WorkbookRow,
    WorkbookSnapshot,
    WorkbookWriteSessionResult,
)
from project.workflows.bb_dashboard_verification import (
    DashboardCandidateFamily,
    DashboardCandidateRow,
    ERPFamilyAggregate,
    _build_report_html,
    _compare_buyer_details,
    _compare_dashboard_snapshot,
    _resolve_sl_no_values_by_row,
    validate_bb_dashboard_verification_run,
)
from project.workflows.bb_dashboard_verification.providers import (
    DashboardFamilySnapshot,
    DashboardLookupResult,
    JsonManifestDashboardLookupProvider,
    PlaywrightDashboardLookupProvider,
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
        self.assertEqual(
            report_payload["families"][2]["final_workbook_value"],
            "No ERP rows were available for workbook family LC-003.",
        )
        self.assertEqual(len(mail_outcomes), 3)
        self.assertEqual(sum(len(item["staged_write_operations"]) for item in mail_outcomes), 10)
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
        self.assertEqual(len(prepared.target_probes), 10)
        self.assertTrue(all(probe.classification == "matches_pre_write" for probe in prepared.target_probes))

        shipment_date_ops = [
            operation
            for outcome in workflow_result.validation_result.mail_outcomes
            for operation in outcome.staged_write_operations
            if operation["column_key"] == "shipment_date"
        ]
        expiry_date_ops = [
            operation
            for outcome in workflow_result.validation_result.mail_outcomes
            for operation in outcome.staged_write_operations
            if operation["column_key"] == "expiry_date"
        ]
        self.assertTrue(shipment_date_ops)
        self.assertTrue(expiry_date_ops)
        self.assertTrue(all(operation["number_format"] == "dd/mm/yyyy" for operation in shipment_date_ops))
        self.assertTrue(all(operation["number_format"] == "dd/mm/yyyy" for operation in expiry_date_ops))

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
        self.assertEqual(len(outcome.staged_write_operations), 3)
        self.assertEqual(outcome.staged_write_operations[0]["column_key"], "dashboard_status")
        self.assertEqual(outcome.staged_write_operations[0]["expected_post_write_value"], "Quantity mismatch")
        self.assertEqual(outcome.staged_write_operations[1]["column_key"], "shipment_date")
        self.assertEqual(outcome.staged_write_operations[1]["expected_post_write_value"], "10/02/2026")
        self.assertEqual(outcome.staged_write_operations[2]["column_key"], "expiry_date")
        self.assertEqual(outcome.staged_write_operations[2]["expected_post_write_value"], "10/03/2026")

    def test_hard_block_result_still_stages_dashboard_status_write(self) -> None:
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

        self.assertEqual(workflow_result.validation_result.run_report.summary, {"pass": 2, "warning": 0, "hard_block": 1})
        self.assertEqual(len(workflow_result.validation_result.mail_outcomes), 3)
        hard_block_outcome = workflow_result.validation_result.mail_outcomes[2]
        self.assertEqual(hard_block_outcome.final_decision, FinalDecision.HARD_BLOCK)
        self.assertTrue(hard_block_outcome.eligible_for_write)
        self.assertEqual(hard_block_outcome.write_disposition, "new_writes_staged")
        self.assertEqual(len(hard_block_outcome.staged_write_operations), 1)
        self.assertEqual(hard_block_outcome.staged_write_operations[0]["column_key"], "dashboard_status")
        self.assertEqual(
            hard_block_outcome.staged_write_operations[0]["expected_post_write_value"],
            "No ERP rows were available for workbook family LC-003.",
        )

    def test_validate_run_missing_sl_no_hard_blocks_without_staging_date_writeback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path, _workbook_manifest_path, _erp_manifest_path, dashboard_manifest_path = _write_dashboard_fixture_bundle(root)

            descriptor = get_workflow_descriptor(WorkflowId.BB_DASHBOARD_VERIFICATION)
            config = load_workflow_config(descriptor=descriptor, config_path=config_path)
            rule_pack = load_rule_pack(WorkflowId.BB_DASHBOARD_VERIFICATION)
            initialized = initialize_workflow_run(
                descriptor=descriptor,
                config=config,
                rule_pack=rule_pack,
                mail_snapshot=[],
            )
            workbook_snapshot = WorkbookSnapshot(
                sheet_name="Sheet1",
                headers=[
                    WorkbookHeader(column_index=1, text="SL.No."),
                    WorkbookHeader(column_index=2, text="L/C & S/C No."),
                    WorkbookHeader(column_index=3, text="Shipment Date"),
                    WorkbookHeader(column_index=4, text="Expiry Date"),
                    WorkbookHeader(column_index=5, text="Master L/C No."),
                    WorkbookHeader(column_index=6, text="UD No. & IP No."),
                    WorkbookHeader(column_index=7, text="UP No."),
                    WorkbookHeader(column_index=8, text="Bangladesh Bank Dashboard"),
                ],
                rows=[
                    WorkbookRow(
                        row_index=11,
                        values={
                            1: "",
                            2: "LC-MISSING-SL",
                            3: "",
                            4: "",
                            5: "MLC-001",
                            6: "BGMEA/DHK/UD/2026/100/001",
                            7: "",
                            8: "",
                        },
                    )
                ],
            )

            workflow_result = validate_bb_dashboard_verification_run(
                run_report=initialized.run_report,
                workbook_snapshot=workbook_snapshot,
                erp_rows=[],
                dashboard_provider=JsonManifestDashboardLookupProvider(dashboard_manifest_path),
            )

        self.assertEqual(workflow_result.validation_result.run_report.summary, {"pass": 0, "warning": 0, "hard_block": 1})
        self.assertEqual(len(workflow_result.validation_result.discrepancy_reports), 1)
        self.assertEqual(
            workflow_result.validation_result.discrepancy_reports[0].message,
            "One or more filtered workbook rows in family LC-MISSING-SL are missing SL.No. values.",
        )
        outcome = workflow_result.validation_result.mail_outcomes[0]
        self.assertEqual(outcome.final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual(
            [operation["column_key"] for operation in outcome.staged_write_operations],
            ["dashboard_status"],
        )
        self.assertEqual(
            outcome.staged_write_operations[0]["expected_post_write_value"],
            "One or more filtered workbook rows in family LC-MISSING-SL are missing SL.No. values.",
        )

    def test_validate_run_normalizes_snapshot_numeric_sl_no_to_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path, workbook_manifest_path, erp_manifest_path, dashboard_manifest_path = _write_dashboard_warning_fixture_bundle(root)
            workbook_payload = json.loads(workbook_manifest_path.read_text(encoding="utf-8"))
            workbook_payload["rows"][0]["values"]["1"] = 633.0
            workbook_manifest_path.write_text(json.dumps(workbook_payload), encoding="utf-8")

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

        self.assertEqual(workflow_result.report_payload["families"][0]["sl_no_values"], ["633"])
        self.assertIn(">633<", workflow_result.report_html)

    def test_resolve_sl_no_values_by_row_uses_live_workbook_display_text(self) -> None:
        workbook_snapshot = WorkbookSnapshot(
            sheet_name="Sheet1",
            headers=[WorkbookHeader(column_index=1, text="SL.No.")],
            rows=[WorkbookRow(row_index=11, values={1: "633.0"}, number_formats={})],
        )

        class FakeCell:
            def __init__(self, text: str) -> None:
                self.api = type("Api", (), {"Text": text})()

        class FakeSheet:
            def range(self, coordinates):
                self.last_coordinates = coordinates
                return FakeCell("21A")

        class FakeBook:
            def __init__(self) -> None:
                self.sheets = [FakeSheet()]

            def close(self) -> None:
                return None

        class FakeBooks:
            def open(self, *_args, **_kwargs):
                return FakeBook()

        class FakeApp:
            def __init__(self, **_kwargs) -> None:
                self.books = FakeBooks()

            def quit(self) -> None:
                return None

        fake_xlwings = type("FakeXLWings", (), {"App": FakeApp})

        with patch.dict(sys.modules, {"xlwings": fake_xlwings}):
            resolved = _resolve_sl_no_values_by_row(
                workbook_snapshot=workbook_snapshot,
                sl_no_column_index=1,
                live_workbook_path=Path("D:/customs-automation/2026-master.xlsx"),
            )

        self.assertEqual(resolved, {11: "21A"})

    def test_resolve_sl_no_values_by_row_uses_display_text_for_each_requested_live_row(self) -> None:
        workbook_snapshot = WorkbookSnapshot(
            sheet_name="Sheet1",
            headers=[WorkbookHeader(column_index=1, text="SL.No.")],
            rows=[
                WorkbookRow(row_index=11, values={1: "101.0"}, number_formats={}),
                WorkbookRow(row_index=13, values={1: "103.0"}, number_formats={}),
            ],
        )

        class FakeCell:
            def __init__(self, text: str) -> None:
                self.api = type("Api", (), {"Text": text})()

        class FakeSheet:
            def __init__(self) -> None:
                self.range_calls: list[object] = []

            def range(self, *coordinates):
                self.range_calls.append(coordinates)
                row_index, _column_index = coordinates[0]
                values = {
                    11: "21A",
                    13: "23A",
                }
                return FakeCell(values[row_index])

        class FakeBook:
            def __init__(self) -> None:
                self.sheets = [FakeSheet()]

            def close(self) -> None:
                return None

        class FakeBooks:
            def __init__(self) -> None:
                self.book = FakeBook()

            def open(self, *_args, **_kwargs):
                return self.book

        class FakeApp:
            last_instance = None

            def __init__(self, **_kwargs) -> None:
                self.books = FakeBooks()
                FakeApp.last_instance = self

            def quit(self) -> None:
                return None

        fake_xlwings = type("FakeXLWings", (), {"App": FakeApp})

        with patch.dict(sys.modules, {"xlwings": fake_xlwings}):
            resolved = _resolve_sl_no_values_by_row(
                workbook_snapshot=workbook_snapshot,
                sl_no_column_index=1,
                live_workbook_path=Path("D:/customs-automation/2026-master.xlsx"),
            )

        fake_sheet = FakeApp.last_instance.books.book.sheets[0]
        self.assertEqual(resolved, {11: "21A", 13: "23A"})
        self.assertEqual(fake_sheet.range_calls, [((11, 1),), ((13, 1),)])

    def test_validate_run_exception_report_does_not_reuse_prior_family_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path, workbook_manifest_path, erp_manifest_path, _dashboard_manifest_path = _write_dashboard_fixture_bundle(root)

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
            erp_rows = JsonManifestERPRowProvider(erp_manifest_path).load_rows()

            class FailingLookupProvider:
                def __init__(self) -> None:
                    self.calls = 0

                def lookup_family(self, *, search_keys: list[str]) -> DashboardLookupResult:
                    self.calls += 1
                    if self.calls == 1:
                        return DashboardLookupResult(
                            outcome="resolved",
                            attempts=[],
                            matched_search_key=search_keys[0] if search_keys else None,
                            snapshot=DashboardFamilySnapshot(
                                beneficiary_name="PIONEER DENIM LIMITED",
                                irc_details="ANANTA GARMENTS LTD",
                                erc_details="ANANTA GARMENTS LTD",
                                lc_date="2026-01-10",
                                last_date_of_shipment="2026-02-10",
                                lc_expiry_date="2026-03-10",
                                lc_value="100",
                                foreign_lc_numbers=["MLC-002"],
                                commodity_quantities=["40"],
                            ),
                        )
                    raise RuntimeError("dashboard session reset failed")

                def close(self) -> None:
                    return None

            workflow_result = validate_bb_dashboard_verification_run(
                run_report=initialized.run_report,
                workbook_snapshot=workbook_snapshot,
                erp_rows=erp_rows,
                dashboard_provider=FailingLookupProvider(),
            )

        second_family = workflow_result.report_payload["families"][1]
        self.assertEqual(
            second_family["final_workbook_value"],
            "Dashboard fetch failed for family LC-002.",
        )
        self.assertEqual(second_family["search_attempts"], [])

    def test_report_html_renders_dashboard_columns_with_quantity_sum_and_foreign_lc_breaks(self) -> None:
        report_html = _build_report_html(
            report_payload={
                "run_id": "run-1",
                "workflow_id": "bb_dashboard_verification",
                "rule_pack_id": "bb_dashboard_verification.default",
                "rule_pack_version": "1.0.0",
                "state_timezone": "Asia/Dhaka",
                "generated_at_utc": "2026-05-14T00:00:00Z",
                "summary": {"pass": 0, "warning": 1, "hard_block": 0},
                "family_count": 1,
                "families": [
                    {
                        "lc_sc_no": "2159260400534",
                        "sl_no_values": ["633.0"],
                        "workbook_master_lc_values": ["COG/VDAL/08/2025"],
                        "final_decision": "warning",
                        "erp": {
                            "buyer_name": "VINTAGE DENIM APPARELS LTD",
                            "current_lc_value": "98315.5",
                            "lc_qty": "33170",
                            "net_weight": "21054.11",
                        },
                        "final_workbook_value": "IRC Details mismatch",
                        "decision_reasons": ["IRC Details did not contain the ERP buyer name."],
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

        self.assertIn("<h1>Workflow Dashboard: bb_dashboard_verification</h1>", report_html)
        self.assertIn("<h2>Snapshot</h2>", report_html)
        self.assertIn("<h2>Summary</h2>", report_html)
        self.assertIn("<h2 class=\"sticky-section-title\">Family Results</h2>", report_html)
        self.assertIn("Generated at: 14/05/2026 06:00:00 AM (Asia/Dhaka)", report_html)
        self.assertIn("overflow-x: scroll", report_html)
        self.assertIn("position: sticky", report_html)
        self.assertIn("border-right: 1px solid #d9e2ec", report_html)
        self.assertIn("Decision Reasons", report_html)
        self.assertIn("Dashboard Beneficiary", report_html)
        self.assertIn("Dashboard Foreign LC No", report_html)
        self.assertIn("Dashboard Quantity Total", report_html)
        self.assertIn("ERP Net Weight", report_html)
        self.assertIn("COG/VDAL/08/2025<br>EXTRA-FLC-002", report_html)
        self.assertIn(">633<", report_html)
        self.assertNotIn(">633.0<", report_html)
        self.assertIn(">33170<", report_html)
        self.assertIn(">21054.11<", report_html)
        self.assertLess(report_html.index("ERP LC Qty"), report_html.index("ERP Net Weight"))
        self.assertLess(report_html.index("ERP Net Weight"), report_html.index("Final Workbook Value"))

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

    def test_playwright_provider_resets_to_fresh_search_page_between_lookup_calls(self) -> None:
        class FakeLocator:
            def wait_for(self, **kwargs) -> None:
                return None

            def click(self) -> None:
                return None

            def fill(self, _value: str) -> None:
                return None

        class FakePage:
            def __init__(self, url: str) -> None:
                self.url = url
                self.closed = False

            def locator(self, _selector: str) -> FakeLocator:
                return FakeLocator()

            def close(self) -> None:
                self.closed = True

        provider = PlaywrightDashboardLookupProvider(
            login_url="https://exp.bb.org.bd/ords/f?p=116:75:",
            username=None,
            password=None,
            username_selector=None,
            password_selector=None,
            submit_selector=None,
            post_login_wait_selector=None,
            search_input_selector="#P75_SEARCH_LC",
            search_button_selector="button.t-Button",
            detail_ready_selector=None,
            no_result_selector=None,
            beneficiary_selector="#P75_BENEFICIARY_NAME",
            irc_selector="#P75_IRC_DETAILS",
            erc_selector="#P75_ERC_DETAILS",
            lc_date_selector="#P75_LC_DATE",
            last_date_of_shipment_selector="#P75_LAST_DATE_OF_SHIPMENT",
            lc_expiry_date_selector="#P75_LC_EXPIRY_DATE",
            lc_value_selector="#P75_LC_VALUE",
            foreign_lc_selector="xpath=//foreign",
            quantity_selector="xpath=//quantity",
        )
        provider._page = FakePage(url="https://exp.bb.org.bd/ords/oims/r/import/75?session=1")
        provider._page_dirty = False

        with patch.object(PlaywrightDashboardLookupProvider, "_ensure_authenticated_page") as ensure_page_mock:
            ensure_page_mock.return_value = None
            with patch.object(PlaywrightDashboardLookupProvider, "_reset_to_fresh_search_page") as reset_page_mock:
                reset_page_mock.return_value = None
                with patch.object(PlaywrightDashboardLookupProvider, "_read_settled_snapshot") as read_snapshot_mock:
                    read_snapshot_mock.side_effect = [
                        DashboardFamilySnapshot(
                            beneficiary_name="PIONEER DENIM LIMITED",
                            irc_details="IRC 1",
                            erc_details="ERC 1",
                            lc_date="2026-04-22",
                            last_date_of_shipment="2026-06-15",
                            lc_expiry_date="2026-06-30",
                            lc_value="98315.5",
                            foreign_lc_numbers=["FLC-001"],
                            commodity_quantities=["33170"],
                            source_url="https://exp.bb.org.bd/ords/oims/r/import/75?session=1",
                        ),
                        DashboardFamilySnapshot(
                            beneficiary_name="PIONEER DENIM LIMITED",
                            irc_details="IRC 2",
                            erc_details="ERC 2",
                            lc_date="2026-04-23",
                            last_date_of_shipment="2026-06-16",
                            lc_expiry_date="2026-07-01",
                            lc_value="99999.0",
                            foreign_lc_numbers=["FLC-002"],
                            commodity_quantities=["12345"],
                            source_url="https://exp.bb.org.bd/ords/oims/r/import/75?session=1",
                        ),
                    ]
                    first_result = provider.lookup_family(search_keys=["1741260401172"])
                    second_result = provider.lookup_family(search_keys=["2159260400534"])

        self.assertEqual(first_result.outcome, "resolved")
        self.assertEqual(second_result.outcome, "resolved")
        reset_page_mock.assert_called_once()
        self.assertEqual(first_result.snapshot.irc_details, "IRC 1")
        self.assertEqual(second_result.snapshot.irc_details, "IRC 2")
        self.assertTrue(provider._page_dirty)

    def test_playwright_provider_reset_flow_uses_back_then_inland_btb_search_link(self) -> None:
        class FakeLink:
            def __init__(self, *, page, text: str) -> None:
                self._page = page
                self._text = text

            def click(self) -> None:
                self._page.clicks.append(self._text)

        class FakeLocator:
            def __init__(self) -> None:
                self.wait_calls: list[tuple[str, int]] = []

            def wait_for(self, *, state: str, timeout: int) -> None:
                self.wait_calls.append((state, timeout))

            @property
            def first(self):
                return self

            def evaluate(self, _script: str):
                return "INPUT"

            def input_value(self) -> str:
                return ""

            def text_content(self) -> str:
                return ""

            def count(self) -> int:
                return 1

            def all_inner_texts(self) -> list[str]:
                return []

        class FakePage:
            def __init__(self) -> None:
                self.clicks: list[str] = []
                self.waited_urls: list[str] = []
                self.locator_calls: list[str] = []
                self.url = "https://exp.bb.org.bd/ords/oims/r/import/75?clear=75&session=1"

            def get_by_text(self, text: str, exact: bool = False) -> FakeLink:
                return FakeLink(page=self, text=text)

            def wait_for_url(self, pattern: str, timeout: int) -> None:
                self.waited_urls.append(pattern)

            def locator(self, selector: str) -> FakeLocator:
                self.locator_calls.append(selector)
                return FakeLocator()

        provider = PlaywrightDashboardLookupProvider(
            login_url="https://exp.bb.org.bd/ords/f?p=116:75:",
            username=None,
            password=None,
            username_selector=None,
            password_selector=None,
            submit_selector=None,
            post_login_wait_selector=None,
            search_input_selector="#P75_SEARCH_LC",
            search_button_selector="button.t-Button",
            detail_ready_selector=None,
            no_result_selector=None,
            beneficiary_selector="#P75_BENEFICIARY_NAME",
            irc_selector="#P75_IRC_DETAILS",
            erc_selector="#P75_ERC_DETAILS",
            lc_date_selector="#P75_LC_DATE",
            last_date_of_shipment_selector="#P75_LAST_DATE_OF_SHIPMENT",
            lc_expiry_date_selector="#P75_LC_EXPIRY_DATE",
            lc_value_selector="#P75_LC_VALUE",
            foreign_lc_selector="xpath=//foreign",
            quantity_selector="xpath=//quantity",
        )
        provider._page = FakePage()
        provider._page_dirty = True

        with patch("project.workflows.bb_dashboard_verification.providers._best_effort_wait_for_network_idle") as idle_mock:
            idle_mock.return_value = None
            provider._reset_to_fresh_search_page()

        self.assertEqual(
            provider._page.clicks,
            ["Back", "Inland BTB LC/Contract Search/Edit"],
        )
        self.assertEqual(
            provider._page.waited_urls,
            ["**/350?session=*", "**/75?clear=75**"],
        )
        self.assertEqual(provider._page.locator_calls[0], "#P75_SEARCH_LC")
        self.assertIn("#P75_BENEFICIARY_NAME", provider._page.locator_calls)
        self.assertFalse(provider._page_dirty)

    def test_playwright_provider_reset_flow_hard_blocks_if_blank_search_page_was_not_restored(self) -> None:
        class FakeLink:
            def __init__(self, *, page, text: str) -> None:
                self._page = page
                self._text = text

            def click(self) -> None:
                self._page.clicks.append(self._text)

        class FakeLocator:
            def __init__(self, selector: str) -> None:
                self.selector = selector

            def wait_for(self, *, state: str, timeout: int) -> None:
                return None

            @property
            def first(self):
                return self

            def evaluate(self, _script: str):
                if self.selector == "#P75_SEARCH_LC":
                    return "INPUT"
                return "TEXTAREA"

            def input_value(self) -> str:
                if self.selector == "#P75_SEARCH_LC":
                    return "3054260400812"
                return "stale-value"

            def text_content(self) -> str:
                return ""

            def count(self) -> int:
                return 1

            def all_inner_texts(self) -> list[str]:
                return []

        class FakePage:
            def __init__(self) -> None:
                self.clicks: list[str] = []
                self.waited_urls: list[str] = []
                self.locator_calls: list[str] = []
                self.url = "https://exp.bb.org.bd/ords/oims/r/import/75?clear=75&session=1"

            def get_by_text(self, text: str, exact: bool = False) -> FakeLink:
                return FakeLink(page=self, text=text)

            def wait_for_url(self, pattern: str, timeout: int) -> None:
                self.waited_urls.append(pattern)

            def locator(self, selector: str) -> FakeLocator:
                self.locator_calls.append(selector)
                return FakeLocator(selector)

        provider = PlaywrightDashboardLookupProvider(
            login_url="https://exp.bb.org.bd/ords/f?p=116:75:",
            username=None,
            password=None,
            username_selector=None,
            password_selector=None,
            submit_selector=None,
            post_login_wait_selector=None,
            search_input_selector="#P75_SEARCH_LC",
            search_button_selector="button.t-Button",
            detail_ready_selector=None,
            no_result_selector=None,
            beneficiary_selector="#P75_BENEFICIARY_NAME",
            irc_selector="#P75_IRC_DETAILS",
            erc_selector="#P75_ERC_DETAILS",
            lc_date_selector="#P75_LC_DATE",
            last_date_of_shipment_selector="#P75_LAST_DATE_OF_SHIPMENT",
            lc_expiry_date_selector="#P75_LC_EXPIRY_DATE",
            lc_value_selector="#P75_LC_VALUE",
            foreign_lc_selector="xpath=//foreign",
            quantity_selector="xpath=//quantity",
        )
        provider._page = FakePage()
        provider._page_dirty = True

        with patch("project.workflows.bb_dashboard_verification.providers._best_effort_wait_for_network_idle") as idle_mock:
            idle_mock.return_value = None
            with self.assertRaisesRegex(ValueError, "blank search page"):
                provider._reset_to_fresh_search_page()

    def test_playwright_provider_treats_non_empty_snapshot_as_resolved_when_detail_view_selector_flakes(self) -> None:
        class FakeLocator:
            def wait_for(self, **kwargs) -> None:
                return None

            def click(self) -> None:
                return None

            def fill(self, _value: str) -> None:
                return None

        class FakePage:
            def locator(self, _selector: str) -> FakeLocator:
                return FakeLocator()

        provider = PlaywrightDashboardLookupProvider(
            login_url="https://exp.bb.org.bd/ords/f?p=116:75:",
            username=None,
            password=None,
            username_selector=None,
            password_selector=None,
            submit_selector=None,
            post_login_wait_selector=None,
            search_input_selector="#P75_SEARCH_LC",
            search_button_selector="button.t-Button",
            detail_ready_selector="#detail-ready",
            no_result_selector="#no-result",
            beneficiary_selector="#P75_BENEFICIARY_NAME",
            irc_selector="#P75_IRC_DETAILS",
            erc_selector="#P75_ERC_DETAILS",
            lc_date_selector="#P75_LC_DATE",
            last_date_of_shipment_selector="#P75_LAST_DATE_OF_SHIPMENT",
            lc_expiry_date_selector="#P75_LC_EXPIRY_DATE",
            lc_value_selector="#P75_LC_VALUE",
            foreign_lc_selector="xpath=//foreign",
            quantity_selector="xpath=//quantity",
        )
        provider._page = FakePage()
        provider._page_dirty = False

        with patch.object(PlaywrightDashboardLookupProvider, "_ensure_authenticated_page") as ensure_page_mock:
            ensure_page_mock.return_value = None
            with patch("project.workflows.bb_dashboard_verification.providers._best_effort_wait_for_network_idle") as idle_mock:
                idle_mock.return_value = None
                with patch("project.workflows.bb_dashboard_verification.providers._selector_visible") as selector_visible_mock:
                    selector_visible_mock.side_effect = [False, False, False]
                    with patch.object(PlaywrightDashboardLookupProvider, "_read_snapshot") as read_snapshot_mock:
                        read_snapshot_mock.return_value = DashboardFamilySnapshot(
                            beneficiary_name="PIONEER DENIM LIMITED",
                            irc_details="IRC 1",
                            erc_details="ERC 1",
                            lc_date="2026-04-22",
                            last_date_of_shipment="2026-06-15",
                            lc_expiry_date="2026-06-30",
                            lc_value="98315.5",
                            foreign_lc_numbers=["FLC-001"],
                            commodity_quantities=["33170"],
                            source_url="https://exp.bb.org.bd/ords/oims/r/import/75?session=1",
                        )
                        result = provider.lookup_family(search_keys=["1741260401172"])

        self.assertEqual(result.outcome, "resolved")
        self.assertEqual(result.matched_search_key, "1741260401172")
        self.assertIsNotNone(result.snapshot)
        self.assertIsNone(result.message)
        self.assertEqual(result.attempts[-1].outcome, "resolved")
        self.assertIn("readiness selector did not become visible", result.attempts[-1].message or "")

    def test_playwright_provider_returns_no_result_when_detail_view_selector_flakes_and_snapshot_is_empty(self) -> None:
        class FakeLocator:
            def wait_for(self, **kwargs) -> None:
                return None

            def click(self) -> None:
                return None

            def fill(self, _value: str) -> None:
                return None

        class FakePage:
            def locator(self, _selector: str) -> FakeLocator:
                return FakeLocator()

        provider = PlaywrightDashboardLookupProvider(
            login_url="https://exp.bb.org.bd/ords/f?p=116:75:",
            username=None,
            password=None,
            username_selector=None,
            password_selector=None,
            submit_selector=None,
            post_login_wait_selector=None,
            search_input_selector="#P75_SEARCH_LC",
            search_button_selector="button.t-Button",
            detail_ready_selector="#detail-ready",
            no_result_selector="#no-result",
            beneficiary_selector="#P75_BENEFICIARY_NAME",
            irc_selector="#P75_IRC_DETAILS",
            erc_selector="#P75_ERC_DETAILS",
            lc_date_selector="#P75_LC_DATE",
            last_date_of_shipment_selector="#P75_LAST_DATE_OF_SHIPMENT",
            lc_expiry_date_selector="#P75_LC_EXPIRY_DATE",
            lc_value_selector="#P75_LC_VALUE",
            foreign_lc_selector="xpath=//foreign",
            quantity_selector="xpath=//quantity",
        )
        provider._page = FakePage()
        provider._page_dirty = False

        with patch.object(PlaywrightDashboardLookupProvider, "_ensure_authenticated_page") as ensure_page_mock:
            ensure_page_mock.return_value = None
            with patch("project.workflows.bb_dashboard_verification.providers._best_effort_wait_for_network_idle") as idle_mock:
                idle_mock.return_value = None
                with patch("project.workflows.bb_dashboard_verification.providers._selector_visible") as selector_visible_mock:
                    selector_visible_mock.side_effect = [False, False, False]
                    with patch.object(PlaywrightDashboardLookupProvider, "_read_snapshot") as read_snapshot_mock:
                        read_snapshot_mock.return_value = DashboardFamilySnapshot(
                            beneficiary_name="",
                            irc_details="",
                            erc_details="",
                            lc_date="",
                            last_date_of_shipment="",
                            lc_expiry_date="",
                            lc_value="",
                            foreign_lc_numbers=[],
                            commodity_quantities=[],
                            source_url="https://exp.bb.org.bd/ords/oims/r/import/75?session=1",
                        )
                        result = provider.lookup_family(search_keys=["1741260401172"])

        self.assertEqual(result.outcome, "no_result")
        self.assertEqual(result.matched_search_key, "1741260401172")
        self.assertIsNone(result.snapshot)

    def test_playwright_provider_waits_for_snapshot_to_settle_before_returning(self) -> None:
        provider = PlaywrightDashboardLookupProvider(
            login_url="https://exp.bb.org.bd/ords/f?p=116:75:",
            username=None,
            password=None,
            username_selector=None,
            password_selector=None,
            submit_selector=None,
            post_login_wait_selector=None,
            search_input_selector="#P75_SEARCH_LC",
            search_button_selector="button.t-Button",
            detail_ready_selector="#P75_BENEFICIARY_NAME",
            no_result_selector=None,
            beneficiary_selector="#P75_BENEFICIARY_NAME",
            irc_selector="#P75_IRC_DETAILS",
            erc_selector="#P75_ERC_DETAILS",
            lc_date_selector="#P75_LC_DATE",
            last_date_of_shipment_selector="#P75_LAST_DATE_OF_SHIPMENT",
            lc_expiry_date_selector="#P75_LC_EXPIRY_DATE",
            lc_value_selector="#P75_LC_VALUE",
            foreign_lc_selector="xpath=//foreign",
            quantity_selector="xpath=//quantity",
        )

        wrong_snapshot = DashboardFamilySnapshot(
            beneficiary_name="PIONEER DENIMS LTD",
            irc_details="IRC 1",
            erc_details="ERC 1",
            lc_date="2026-05-06",
            last_date_of_shipment="2026-06-05",
            lc_expiry_date="2026-06-20",
            lc_value="9150",
            foreign_lc_numbers=["JULES-28/2026"],
            commodity_quantities=["3000"],
            source_url="https://exp.bb.org.bd/ords/oims/r/import/75?session=1",
        )
        correct_snapshot = DashboardFamilySnapshot(
            beneficiary_name="PIONEER DENIM LIMITED",
            irc_details="IRC 1",
            erc_details="ERC 1",
            lc_date="2026-05-06",
            last_date_of_shipment="2026-06-05",
            lc_expiry_date="2026-06-20",
            lc_value="9150",
            foreign_lc_numbers=["JULES-28/2026"],
            commodity_quantities=["3000"],
            source_url="https://exp.bb.org.bd/ords/oims/r/import/75?session=1",
        )

        with patch.object(
            PlaywrightDashboardLookupProvider,
            "_read_snapshot",
            side_effect=[wrong_snapshot, correct_snapshot, correct_snapshot, correct_snapshot],
        ) as read_snapshot_mock:
            with patch(
                "project.workflows.bb_dashboard_verification.providers._best_effort_wait_for_timeout"
            ) as wait_mock:
                wait_mock.return_value = None
                with patch(
                    "project.workflows.bb_dashboard_verification.providers.time.monotonic",
                    side_effect=[0.0, 0.0, 0.01, 0.10, 0.11, 0.25, 0.26, 0.35],
                ):
                    snapshot = provider._read_settled_snapshot(page=object())

        self.assertEqual(snapshot.beneficiary_name, "PIONEER DENIM LIMITED")
        self.assertEqual(read_snapshot_mock.call_count, 4)

    def test_playwright_provider_recovers_once_when_lookup_redirects_to_login(self) -> None:
        class FakeLocator:
            def wait_for(self, **kwargs) -> None:
                return None

            def click(self) -> None:
                return None

            def fill(self, _value: str) -> None:
                return None

        class FakePage:
            def __init__(self) -> None:
                self.url = "https://exp.bb.org.bd/ords/oims/r/import/login?session=1"

            def locator(self, _selector: str) -> FakeLocator:
                return FakeLocator()

        provider = PlaywrightDashboardLookupProvider(
            login_url="https://exp.bb.org.bd/ords/f?p=116:75:",
            username=None,
            password=None,
            username_selector=None,
            password_selector=None,
            submit_selector=None,
            post_login_wait_selector=None,
            search_input_selector="#P75_SEARCH_LC",
            search_button_selector="button.t-Button",
            detail_ready_selector=None,
            no_result_selector=None,
            beneficiary_selector="#P75_BENEFICIARY_NAME",
            irc_selector="#P75_IRC_DETAILS",
            erc_selector="#P75_ERC_DETAILS",
            lc_date_selector="#P75_LC_DATE",
            last_date_of_shipment_selector="#P75_LAST_DATE_OF_SHIPMENT",
            lc_expiry_date_selector="#P75_LC_EXPIRY_DATE",
            lc_value_selector="#P75_LC_VALUE",
            foreign_lc_selector="xpath=//foreign",
            quantity_selector="xpath=//quantity",
        )
        provider._page = FakePage()
        provider._page_dirty = False

        with patch.object(PlaywrightDashboardLookupProvider, "_ensure_authenticated_page") as ensure_page_mock:
            ensure_page_mock.return_value = None
            with patch.object(PlaywrightDashboardLookupProvider, "_recover_dashboard_session") as recover_mock:
                def recover(*, page, retry_index: int, fallback_error_message=None) -> None:
                    page.url = "https://exp.bb.org.bd/ords/oims/r/import/75?session=1"
                    provider._page_dirty = False

                recover_mock.side_effect = recover
                with patch("project.workflows.bb_dashboard_verification.providers._best_effort_wait_for_network_idle") as idle_mock:
                    idle_mock.return_value = None
                    with patch.object(PlaywrightDashboardLookupProvider, "_read_snapshot") as read_snapshot_mock:
                        read_snapshot_mock.return_value = DashboardFamilySnapshot(
                            beneficiary_name="PIONEER DENIM LIMITED",
                            irc_details="IRC 1",
                            erc_details="ERC 1",
                            lc_date="2026-04-22",
                            last_date_of_shipment="2026-06-15",
                            lc_expiry_date="2026-06-30",
                            lc_value="98315.5",
                            foreign_lc_numbers=["FLC-001"],
                            commodity_quantities=["33170"],
                            source_url="https://exp.bb.org.bd/ords/oims/r/import/75?session=1",
                        )
                        result = provider.lookup_family(search_keys=["1741260401172"])

        self.assertEqual(result.outcome, "resolved")
        recover_mock.assert_called_once()

    def test_playwright_provider_latches_terminal_session_failure(self) -> None:
        class FakePage:
            def __init__(self) -> None:
                self.url = "https://exp.bb.org.bd/ords/oims/r/import/login?session=1"

        provider = PlaywrightDashboardLookupProvider(
            login_url="https://exp.bb.org.bd/ords/f?p=116:75:",
            username=None,
            password=None,
            username_selector=None,
            password_selector=None,
            submit_selector=None,
            post_login_wait_selector=None,
            search_input_selector="#P75_SEARCH_LC",
            search_button_selector="button.t-Button",
            detail_ready_selector=None,
            no_result_selector=None,
            beneficiary_selector="#P75_BENEFICIARY_NAME",
            irc_selector="#P75_IRC_DETAILS",
            erc_selector="#P75_ERC_DETAILS",
            lc_date_selector="#P75_LC_DATE",
            last_date_of_shipment_selector="#P75_LAST_DATE_OF_SHIPMENT",
            lc_expiry_date_selector="#P75_LC_EXPIRY_DATE",
            lc_value_selector="#P75_LC_VALUE",
            foreign_lc_selector="xpath=//foreign",
            quantity_selector="xpath=//quantity",
        )
        provider._page = FakePage()
        provider._page_dirty = False

        with patch.object(PlaywrightDashboardLookupProvider, "_ensure_authenticated_page") as ensure_page_mock:
            ensure_page_mock.return_value = None
            with patch.object(PlaywrightDashboardLookupProvider, "_recover_dashboard_session") as recover_mock:
                recover_mock.side_effect = ValueError(
                    "Bangladesh Bank dashboard login attempt has been blocked. Please wait and retry later."
                )
                first_result = provider.lookup_family(search_keys=["1741260401172"])

        self.assertEqual(first_result.outcome, "fetch_error")
        self.assertIn("blocked", first_result.message or "")
        self.assertIn("blocked", provider._session_failure_message or "")

        second_result = provider.lookup_family(search_keys=["2159260400534"])
        self.assertEqual(second_result.outcome, "fetch_error")
        self.assertIn("blocked", second_result.message or "")

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
                    shipment_date_number_format="dd/mm/yyyy",
                    expiry_date_number_format="dd/mm/yyyy",
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
                    shipment_date_number_format="dd/mm/yyyy",
                    expiry_date_number_format="dd/mm/yyyy",
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

        self.assertEqual(comparison["status"], "IRC Details, ERC Details mismatch")
        self.assertNotIn("Quantity mismatch", comparison["status"])
        self.assertEqual(
            comparison["decision_reasons"],
            [
                "IRC Details did not contain the ERP buyer name.",
                "ERC Details did not contain the ERP buyer name.",
            ],
        )

    def test_compare_dashboard_snapshot_accepts_ltd_and_limited_as_equivalent(self) -> None:
        family = DashboardCandidateFamily(
            family_id="mail-2a",
            lc_sc_no="LC-002A",
            lc_sc_key="LC 002A",
            row_indexes=[121],
            sl_no_values=["102A"],
            master_lc_values=["MLC-002A"],
            rows=[
                DashboardCandidateRow(
                    row_index=121,
                    sl_no="102A",
                    lc_sc_no="LC-002A",
                    lc_sc_key="LC 002A",
                    master_lc_values=["MLC-002A"],
                    dashboard_status="",
                    shipment_date="",
                    expiry_date="",
                    shipment_date_number_format="dd/mm/yyyy",
                    expiry_date_number_format="dd/mm/yyyy",
                    number_formats={},
                )
            ],
        )
        aggregate = ERPFamilyAggregate(
            lc_sc_no="LC-002A",
            lc_sc_key="LC 002A",
            buyer_name="VINTAGE DENIM APPARELS LIMITED",
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
            irc_details="Vintage Denim Apparels Ltd., Block-B, Tongi I/A, Gazipur",
            erc_details="Vintage Denim Apparels Ltd., Plot-10, Block-B, Gazipur",
            lc_date="22-Apr-2026",
            last_date_of_shipment="15-Jun-2026",
            lc_expiry_date="30-Jun-2026",
            lc_value="98315.5",
            foreign_lc_numbers=["MLC-002A"],
            commodity_quantities=["33170"],
        )

        comparison = _compare_dashboard_snapshot(
            family=family,
            aggregate=aggregate,
            snapshot=snapshot,
        )

        self.assertEqual(comparison["status"], "OK")

    def test_compare_buyer_details_strips_trailing_s_from_each_word_before_comparison(self) -> None:
        self.assertEqual(
            _compare_buyer_details(
                buyer_name="NATURAL DENIMS LTD",
                irc_details="Natural Denim Limited, Plot#532, Tonga Bari, Ashulia",
                erc_details="Natural Denims Ltd., Plot-10, Block-B, Gazipur",
            ),
            [],
        )

    def test_compare_buyer_details_removes_whitespace_before_containment_comparison(self) -> None:
        self.assertEqual(
            _compare_buyer_details(
                buyer_name="BUYERS NAMES LTD",
                irc_details="BuyerName Limited, Gazipur",
                erc_details="Buyers   Names Ltd., Plot-10, Block-B, Gazipur",
            ),
            [],
        )

    def test_compare_dashboard_snapshot_reports_both_value_and_quantity_when_both_are_lower(self) -> None:
        family = DashboardCandidateFamily(
            family_id="mail-2ab",
            lc_sc_no="LC-002AB",
            lc_sc_key="LC 002AB",
            row_indexes=[1212],
            sl_no_values=["102AB"],
            master_lc_values=["MLC-002AB"],
            rows=[
                DashboardCandidateRow(
                    row_index=1212,
                    sl_no="102AB",
                    lc_sc_no="LC-002AB",
                    lc_sc_key="LC 002AB",
                    master_lc_values=["MLC-002AB"],
                    dashboard_status="",
                    shipment_date="",
                    expiry_date="",
                    shipment_date_number_format="dd/mm/yyyy",
                    expiry_date_number_format="dd/mm/yyyy",
                    number_formats={},
                )
            ],
        )
        aggregate = ERPFamilyAggregate(
            lc_sc_no="LC-002AB",
            lc_sc_key="LC 002AB",
            buyer_name="VINTAGE DENIM APPARELS LIMITED",
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
            irc_details="Vintage Denim Apparels Ltd., Block-B, Tongi I/A, Gazipur",
            erc_details="Vintage Denim Apparels Ltd., Plot-10, Block-B, Gazipur",
            lc_date="22-Apr-2026",
            last_date_of_shipment="15-Jun-2026",
            lc_expiry_date="30-Jun-2026",
            lc_value="98000",
            foreign_lc_numbers=["MLC-002AB"],
            commodity_quantities=["33000"],
        )

        comparison = _compare_dashboard_snapshot(
            family=family,
            aggregate=aggregate,
            snapshot=snapshot,
        )

        self.assertEqual(comparison["status"], "Value, Quantity mismatch")
        self.assertEqual(
            comparison["decision_reasons"],
            [
                "LC Value mismatch: dashboard '98000' was lower than ERP '98315.5'.",
                "Quantity mismatch: dashboard total '33000' was lower than ERP LC Qty '33170'.",
            ],
        )

    def test_compare_dashboard_snapshot_accepts_and_and_ampersand_as_equivalent_in_foreign_lc_numbers(self) -> None:
        family = DashboardCandidateFamily(
            family_id="mail-2aa",
            lc_sc_no="LC-002AA",
            lc_sc_key="LC 002AA",
            row_indexes=[1211],
            sl_no_values=["102AA"],
            master_lc_values=["SDL-H AND M-2026-05"],
            rows=[
                DashboardCandidateRow(
                    row_index=1211,
                    sl_no="102AA",
                    lc_sc_no="LC-002AA",
                    lc_sc_key="LC 002AA",
                    master_lc_values=["SDL-H AND M-2026-05"],
                    dashboard_status="",
                    shipment_date="",
                    expiry_date="",
                    shipment_date_number_format="dd/mm/yyyy",
                    expiry_date_number_format="dd/mm/yyyy",
                    number_formats={},
                )
            ],
        )
        aggregate = ERPFamilyAggregate(
            lc_sc_no="LC-002AA",
            lc_sc_key="LC 002AA",
            buyer_name="VINTAGE DENIM APPARELS LIMITED",
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
            irc_details="Vintage Denim Apparels Ltd., Block-B, Tongi I/A, Gazipur",
            erc_details="Vintage Denim Apparels Ltd., Plot-10, Block-B, Gazipur",
            lc_date="22-Apr-2026",
            last_date_of_shipment="15-Jun-2026",
            lc_expiry_date="30-Jun-2026",
            lc_value="98315.5",
            foreign_lc_numbers=["SDL-H&M-2026-05"],
            commodity_quantities=["33170"],
        )

        comparison = _compare_dashboard_snapshot(
            family=family,
            aggregate=aggregate,
            snapshot=snapshot,
        )

        self.assertEqual(comparison["status"], "OK")

    def test_compare_dashboard_snapshot_accepts_when_irc_passes_and_erc_is_empty(self) -> None:
        family = DashboardCandidateFamily(
            family_id="mail-2b",
            lc_sc_no="LC-002B",
            lc_sc_key="LC 002B",
            row_indexes=[122],
            sl_no_values=["102B"],
            master_lc_values=["MLC-002B"],
            rows=[
                DashboardCandidateRow(
                    row_index=122,
                    sl_no="102B",
                    lc_sc_no="LC-002B",
                    lc_sc_key="LC 002B",
                    master_lc_values=["MLC-002B"],
                    dashboard_status="",
                    shipment_date="",
                    expiry_date="",
                    shipment_date_number_format="dd/mm/yyyy",
                    expiry_date_number_format="dd/mm/yyyy",
                    number_formats={},
                )
            ],
        )
        aggregate = ERPFamilyAggregate(
            lc_sc_no="LC-002B",
            lc_sc_key="LC 002B",
            buyer_name="NATURAL DENIMS LTD",
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
            irc_details="Natural Denims Limited, Plot#532, Tonga Bari, Ashulia",
            erc_details="",
            lc_date="22-Apr-2026",
            last_date_of_shipment="15-Jun-2026",
            lc_expiry_date="30-Jun-2026",
            lc_value="98315.5",
            foreign_lc_numbers=["MLC-002B"],
            commodity_quantities=["33170"],
        )

        comparison = _compare_dashboard_snapshot(
            family=family,
            aggregate=aggregate,
            snapshot=snapshot,
        )

        self.assertEqual(comparison["status"], "OK")

    def test_compare_dashboard_snapshot_rejects_when_one_populated_section_passes_and_other_fails(self) -> None:
        family = DashboardCandidateFamily(
            family_id="mail-2c",
            lc_sc_no="LC-002C",
            lc_sc_key="LC 002C",
            row_indexes=[123],
            sl_no_values=["102C"],
            master_lc_values=["MLC-002C"],
            rows=[
                DashboardCandidateRow(
                    row_index=123,
                    sl_no="102C",
                    lc_sc_no="LC-002C",
                    lc_sc_key="LC 002C",
                    master_lc_values=["MLC-002C"],
                    dashboard_status="",
                    shipment_date="",
                    expiry_date="",
                    shipment_date_number_format="dd/mm/yyyy",
                    expiry_date_number_format="dd/mm/yyyy",
                    number_formats={},
                )
            ],
        )
        aggregate = ERPFamilyAggregate(
            lc_sc_no="LC-002C",
            lc_sc_key="LC 002C",
            buyer_name="NATURAL DENIMS LTD",
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
            irc_details="Natural Denims Limited, Plot#532, Tonga Bari, Ashulia",
            erc_details="Different Buyer Name, Gazipur",
            lc_date="22-Apr-2026",
            last_date_of_shipment="15-Jun-2026",
            lc_expiry_date="30-Jun-2026",
            lc_value="98315.5",
            foreign_lc_numbers=["MLC-002C"],
            commodity_quantities=["33170"],
        )

        comparison = _compare_dashboard_snapshot(
            family=family,
            aggregate=aggregate,
            snapshot=snapshot,
        )

        self.assertEqual(
            comparison["decision_reasons"],
            ["ERC Details did not contain the ERP buyer name."],
        )

    def test_compare_dashboard_snapshot_rejects_when_both_buyer_sections_are_empty(self) -> None:
        family = DashboardCandidateFamily(
            family_id="mail-2d",
            lc_sc_no="LC-002D",
            lc_sc_key="LC 002D",
            row_indexes=[124],
            sl_no_values=["102D"],
            master_lc_values=["MLC-002D"],
            rows=[
                DashboardCandidateRow(
                    row_index=124,
                    sl_no="102D",
                    lc_sc_no="LC-002D",
                    lc_sc_key="LC 002D",
                    master_lc_values=["MLC-002D"],
                    dashboard_status="",
                    shipment_date="",
                    expiry_date="",
                    shipment_date_number_format="dd/mm/yyyy",
                    expiry_date_number_format="dd/mm/yyyy",
                    number_formats={},
                )
            ],
        )
        aggregate = ERPFamilyAggregate(
            lc_sc_no="LC-002D",
            lc_sc_key="LC 002D",
            buyer_name="NATURAL DENIMS LTD",
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
            irc_details="",
            erc_details="",
            lc_date="22-Apr-2026",
            last_date_of_shipment="15-Jun-2026",
            lc_expiry_date="30-Jun-2026",
            lc_value="98315.5",
            foreign_lc_numbers=["MLC-002D"],
            commodity_quantities=["33170"],
        )

        comparison = _compare_dashboard_snapshot(
            family=family,
            aggregate=aggregate,
            snapshot=snapshot,
        )

        self.assertEqual(
            comparison["decision_reasons"],
            ["Both IRC Details and ERC Details were empty, so the ERP buyer name could not be verified."],
        )

    def test_compare_dashboard_snapshot_accepts_later_dashboard_shipment_and_expiry_dates(self) -> None:
        family = DashboardCandidateFamily(
            family_id="mail-3",
            lc_sc_no="LC-003",
            lc_sc_key="LC 003",
            row_indexes=[13],
            sl_no_values=["103"],
            master_lc_values=["MLC-003"],
            rows=[
                DashboardCandidateRow(
                    row_index=13,
                    sl_no="103",
                    lc_sc_no="LC-003",
                    lc_sc_key="LC 003",
                    master_lc_values=["MLC-003"],
                    dashboard_status="",
                    shipment_date="",
                    expiry_date="",
                    shipment_date_number_format="dd/mm/yyyy",
                    expiry_date_number_format="dd/mm/yyyy",
                    number_formats={},
                )
            ],
        )
        aggregate = ERPFamilyAggregate(
            lc_sc_no="LC-003",
            lc_sc_key="LC 003",
            buyer_name="NATURAL DENIMS LTD",
            lc_date="09-Apr-26",
            ship_date="01-Jun-26",
            expiry_date="15-Jun-26",
            current_lc_value=Decimal("45165"),
            lc_qty=Decimal("17650"),
            net_weight=Decimal("10361.6"),
            ship_remarks=None,
            source_row_count=1,
        )
        snapshot = DashboardFamilySnapshot(
            beneficiary_name="PIONEER DENIM LIMITED",
            irc_details="Natural Denims Ltd., Plot#532, Tonga Bari, Ashulia,",
            erc_details="NATURAL DENIMS LTD, PLOT NO-532, TONGA BARI,  ASHULIA, SAVAR, DHAKA.",
            lc_date="09-Apr-2026",
            last_date_of_shipment="05-Jun-2026",
            lc_expiry_date="20-Jun-2026",
            lc_value="45165",
            foreign_lc_numbers=["MLC-003"],
            commodity_quantities=["17650"],
        )

        comparison = _compare_dashboard_snapshot(
            family=family,
            aggregate=aggregate,
            snapshot=snapshot,
        )

        self.assertEqual(comparison["status"], "OK")
        self.assertEqual(comparison["decision_reasons"], ["Dashboard quantity matched ERP LC quantity."])

    def test_compare_dashboard_snapshot_accepts_inclusive_minimum_expiry_window(self) -> None:
        family = DashboardCandidateFamily(
            family_id="mail-3b",
            lc_sc_no="LC-003B",
            lc_sc_key="LC 003B",
            row_indexes=[131],
            sl_no_values=["103B"],
            master_lc_values=["MLC-003B"],
            rows=[
                DashboardCandidateRow(
                    row_index=131,
                    sl_no="103B",
                    lc_sc_no="LC-003B",
                    lc_sc_key="LC 003B",
                    master_lc_values=["MLC-003B"],
                    dashboard_status="",
                    shipment_date="",
                    expiry_date="",
                    shipment_date_number_format="dd/mm/yyyy",
                    expiry_date_number_format="dd/mm/yyyy",
                    number_formats={},
                )
            ],
        )
        aggregate = ERPFamilyAggregate(
            lc_sc_no="LC-003B",
            lc_sc_key="LC 003B",
            buyer_name="NATURAL DENIMS LTD",
            lc_date="01-Jan-26",
            ship_date="01-Jan-26",
            expiry_date="06-Jan-26",
            current_lc_value=Decimal("45165"),
            lc_qty=Decimal("17650"),
            net_weight=Decimal("10361.6"),
            ship_remarks=None,
            source_row_count=1,
        )
        snapshot = DashboardFamilySnapshot(
            beneficiary_name="PIONEER DENIM LIMITED",
            irc_details="Natural Denims Ltd.",
            erc_details="Natural Denims Ltd.",
            lc_date="01-Jan-2026",
            last_date_of_shipment="01-Jan-2026",
            lc_expiry_date="06-Jan-2026",
            lc_value="45165",
            foreign_lc_numbers=["MLC-003B"],
            commodity_quantities=["17650"],
        )

        comparison = _compare_dashboard_snapshot(
            family=family,
            aggregate=aggregate,
            snapshot=snapshot,
        )

        self.assertEqual(comparison["status"], "OK")
        self.assertEqual(comparison["decision_reasons"], ["Dashboard quantity matched ERP LC quantity."])

    def test_compare_dashboard_snapshot_accepts_250_day_shipment_offset_and_90_day_expiry_window(self) -> None:
        family = DashboardCandidateFamily(
            family_id="mail-3c",
            lc_sc_no="LC-003C",
            lc_sc_key="LC 003C",
            row_indexes=[132],
            sl_no_values=["103C"],
            master_lc_values=["MLC-003C"],
            rows=[
                DashboardCandidateRow(
                    row_index=132,
                    sl_no="103C",
                    lc_sc_no="LC-003C",
                    lc_sc_key="LC 003C",
                    master_lc_values=["MLC-003C"],
                    dashboard_status="",
                    shipment_date="",
                    expiry_date="",
                    shipment_date_number_format="dd/mm/yyyy",
                    expiry_date_number_format="dd/mm/yyyy",
                    number_formats={},
                )
            ],
        )
        aggregate = ERPFamilyAggregate(
            lc_sc_no="LC-003C",
            lc_sc_key="LC 003C",
            buyer_name="NATURAL DENIMS LTD",
            lc_date="01-Jan-26",
            ship_date="01-Jan-26",
            expiry_date="01-Apr-26",
            current_lc_value=Decimal("45165"),
            lc_qty=Decimal("17650"),
            net_weight=Decimal("10361.6"),
            ship_remarks=None,
            source_row_count=1,
        )
        snapshot = DashboardFamilySnapshot(
            beneficiary_name="PIONEER DENIM LIMITED",
            irc_details="Natural Denims Ltd.",
            erc_details="Natural Denims Ltd.",
            lc_date="01-Jan-2026",
            last_date_of_shipment="08-Sep-2026",
            lc_expiry_date="07-Dec-2026",
            lc_value="45165",
            foreign_lc_numbers=["MLC-003C"],
            commodity_quantities=["17650"],
        )

        comparison = _compare_dashboard_snapshot(
            family=family,
            aggregate=aggregate,
            snapshot=snapshot,
        )

        self.assertEqual(comparison["status"], "OK")
        self.assertEqual(comparison["decision_reasons"], ["Dashboard quantity matched ERP LC quantity."])

    def test_compare_dashboard_snapshot_rejects_shipment_more_than_250_days_later(self) -> None:
        family = DashboardCandidateFamily(
            family_id="mail-3d",
            lc_sc_no="LC-003D",
            lc_sc_key="LC 003D",
            row_indexes=[133],
            sl_no_values=["103D"],
            master_lc_values=["MLC-003D"],
            rows=[
                DashboardCandidateRow(
                    row_index=133,
                    sl_no="103D",
                    lc_sc_no="LC-003D",
                    lc_sc_key="LC 003D",
                    master_lc_values=["MLC-003D"],
                    dashboard_status="",
                    shipment_date="",
                    expiry_date="",
                    shipment_date_number_format="dd/mm/yyyy",
                    expiry_date_number_format="dd/mm/yyyy",
                    number_formats={},
                )
            ],
        )
        aggregate = ERPFamilyAggregate(
            lc_sc_no="LC-003D",
            lc_sc_key="LC 003D",
            buyer_name="NATURAL DENIMS LTD",
            lc_date="01-Jan-26",
            ship_date="01-Jan-26",
            expiry_date="01-Apr-26",
            current_lc_value=Decimal("45165"),
            lc_qty=Decimal("17650"),
            net_weight=Decimal("10361.6"),
            ship_remarks=None,
            source_row_count=1,
        )
        snapshot = DashboardFamilySnapshot(
            beneficiary_name="PIONEER DENIM LIMITED",
            irc_details="Natural Denims Ltd.",
            erc_details="Natural Denims Ltd.",
            lc_date="01-Jan-2026",
            last_date_of_shipment="09-Sep-2026",
            lc_expiry_date="08-Dec-2026",
            lc_value="45165",
            foreign_lc_numbers=["MLC-003D"],
            commodity_quantities=["17650"],
        )

        comparison = _compare_dashboard_snapshot(
            family=family,
            aggregate=aggregate,
            snapshot=snapshot,
        )

        self.assertEqual(comparison["status"], "Shipment Date mismatch")

    def test_compare_dashboard_snapshot_accepts_approved_excess_rule(self) -> None:
        family = DashboardCandidateFamily(
            family_id="mail-3e",
            lc_sc_no="LC-003E",
            lc_sc_key="LC 003E",
            row_indexes=[134],
            sl_no_values=["103E"],
            master_lc_values=["MLC-003E"],
            rows=[
                DashboardCandidateRow(
                    row_index=134,
                    sl_no="103E",
                    lc_sc_no="LC-003E",
                    lc_sc_key="LC 003E",
                    master_lc_values=["MLC-003E"],
                    dashboard_status="",
                    shipment_date="",
                    expiry_date="",
                    shipment_date_number_format="dd/mm/yyyy",
                    expiry_date_number_format="dd/mm/yyyy",
                    number_formats={},
                )
            ],
        )
        aggregate = ERPFamilyAggregate(
            lc_sc_no="LC-003E",
            lc_sc_key="LC 003E",
            buyer_name="NATURAL DENIMS LTD",
            lc_date="01-Jan-26",
            ship_date="01-Jan-26",
            expiry_date="01-Apr-26",
            current_lc_value=Decimal("150"),
            lc_qty=Decimal("100"),
            net_weight=Decimal("90"),
            ship_remarks=None,
            source_row_count=1,
        )
        snapshot = DashboardFamilySnapshot(
            beneficiary_name="PIONEER DENIM LIMITED",
            irc_details="Natural Denims Ltd.",
            erc_details="Natural Denims Ltd.",
            lc_date="01-Jan-2026",
            last_date_of_shipment="01-Jan-2026",
            lc_expiry_date="01-Apr-2026",
            lc_value="250",
            foreign_lc_numbers=["MLC-003E"],
            commodity_quantities=["180"],
        )

        comparison = _compare_dashboard_snapshot(
            family=family,
            aggregate=aggregate,
            snapshot=snapshot,
        )

        self.assertEqual(comparison["status"], "OK")
        self.assertEqual(comparison["decision_reasons"], ["Dashboard LC value and quantity satisfied the approved excess rule."])

    def test_compare_dashboard_snapshot_rejects_single_field_excess(self) -> None:
        family = DashboardCandidateFamily(
            family_id="mail-3f",
            lc_sc_no="LC-003F",
            lc_sc_key="LC 003F",
            row_indexes=[135],
            sl_no_values=["103F"],
            master_lc_values=["MLC-003F"],
            rows=[
                DashboardCandidateRow(
                    row_index=135,
                    sl_no="103F",
                    lc_sc_no="LC-003F",
                    lc_sc_key="LC 003F",
                    master_lc_values=["MLC-003F"],
                    dashboard_status="",
                    shipment_date="",
                    expiry_date="",
                    shipment_date_number_format="dd/mm/yyyy",
                    expiry_date_number_format="dd/mm/yyyy",
                    number_formats={},
                )
            ],
        )
        aggregate = ERPFamilyAggregate(
            lc_sc_no="LC-003F",
            lc_sc_key="LC 003F",
            buyer_name="NATURAL DENIMS LTD",
            lc_date="01-Jan-26",
            ship_date="01-Jan-26",
            expiry_date="01-Apr-26",
            current_lc_value=Decimal("150"),
            lc_qty=Decimal("100"),
            net_weight=Decimal("90"),
            ship_remarks=None,
            source_row_count=1,
        )
        snapshot = DashboardFamilySnapshot(
            beneficiary_name="PIONEER DENIM LIMITED",
            irc_details="Natural Denims Ltd.",
            erc_details="Natural Denims Ltd.",
            lc_date="01-Jan-2026",
            last_date_of_shipment="01-Jan-2026",
            lc_expiry_date="01-Apr-2026",
            lc_value="150",
            foreign_lc_numbers=["MLC-003F"],
            commodity_quantities=["180"],
        )

        comparison = _compare_dashboard_snapshot(
            family=family,
            aggregate=aggregate,
            snapshot=snapshot,
        )

        self.assertEqual(comparison["status"], "Value, Quantity mismatch")
        self.assertIn("single-field excess is not allowed", " ".join(comparison["decision_reasons"]))

    def test_compare_dashboard_snapshot_accepts_net_weight_with_point_eight_tolerance(self) -> None:
        family = DashboardCandidateFamily(
            family_id="mail-4",
            lc_sc_no="LC-004",
            lc_sc_key="LC 004",
            row_indexes=[14],
            sl_no_values=["104"],
            master_lc_values=["MLC-004"],
            rows=[
                DashboardCandidateRow(
                    row_index=14,
                    sl_no="104",
                    lc_sc_no="LC-004",
                    lc_sc_key="LC 004",
                    master_lc_values=["MLC-004"],
                    dashboard_status="",
                    shipment_date="",
                    expiry_date="",
                    shipment_date_number_format="dd/mm/yyyy",
                    expiry_date_number_format="dd/mm/yyyy",
                    number_formats={},
                )
            ],
        )
        aggregate = ERPFamilyAggregate(
            lc_sc_no="LC-004",
            lc_sc_key="LC 004",
            buyer_name="ANANTA GARMENTS LTD",
            lc_date="10-Jan-26",
            ship_date="10-Feb-26",
            expiry_date="10-Mar-26",
            current_lc_value=Decimal("50"),
            lc_qty=Decimal("40"),
            net_weight=Decimal("18"),
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
            foreign_lc_numbers=["MLC-004"],
            commodity_quantities=["18.79"],
        )

        comparison = _compare_dashboard_snapshot(
            family=family,
            aggregate=aggregate,
            snapshot=snapshot,
        )

        self.assertEqual(comparison["status"], "OK (KGS)")
        self.assertEqual(comparison["decision_reasons"], ["Dashboard quantity matched ERP net weight."])


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
