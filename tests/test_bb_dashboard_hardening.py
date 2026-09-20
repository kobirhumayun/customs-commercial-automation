from dataclasses import replace
from contextlib import ExitStack, redirect_stdout
import io
import json
from decimal import InvalidOperation
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tests.test_bb_dashboard_verification import _write_dashboard_fixture_bundle
from project.config import load_workflow_config
from project.erp import JsonManifestERPRowProvider
from project.models import WorkflowId, WritePhaseStatus, WriteCommitMarker
from project.cli import main
from project.rules import load_rule_pack
from project.workbook import JsonManifestWorkbookSnapshotProvider
from project.workflows.bootstrap import initialize_workflow_run
from project.workflows.registry import get_workflow_descriptor
from project.workflows.bb_dashboard_verification import (
    validate_bb_dashboard_verification_run, _evaluate_lookup_result,
    _format_report_sl_no_value, _read_live_display_values,
    refresh_bb_dashboard_verification_report, _dedupe_erp_rows,
)
from project.workflows.bb_dashboard_verification.providers import (
    JsonManifestDashboardLookupProvider, DashboardLookupResult,
)


class DashboardHardeningTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        config_path, workbook_path, erp_path, dashboard_path = _write_dashboard_fixture_bundle(Path(temp.name))
        self.config_path, self.erp_path, self.dashboard_path = config_path, erp_path, dashboard_path
        descriptor = get_workflow_descriptor(WorkflowId.BB_DASHBOARD_VERIFICATION)
        config = load_workflow_config(descriptor=descriptor, config_path=config_path)
        initialized = initialize_workflow_run(descriptor=descriptor, config=config,
                                              rule_pack=load_rule_pack(descriptor.workflow_id), mail_snapshot=[])
        self.args = dict(run_report=initialized.run_report,
                         workbook_snapshot=JsonManifestWorkbookSnapshotProvider(workbook_path).load_snapshot(),
                         erp_rows=JsonManifestERPRowProvider(erp_path).load_rows())
        self.provider = JsonManifestDashboardLookupProvider(dashboard_path)

    def validate_first_snapshot(self, **changes):
        delegate = self.provider

        class Provider:
            calls = 0

            def lookup_family(self, **kwargs):
                result = delegate.lookup_family(**kwargs)
                self.calls += 1
                return replace(result, snapshot=replace(result.snapshot, **changes)) if self.calls == 1 else result

        return validate_bb_dashboard_verification_run(**self.args, dashboard_provider=Provider())

    def test_malformed_dashboard_family_blocks_but_next_family_completes(self):
        for changes in ({"lc_value": "NaN"}, {"lc_value": "sNaN"}, {"lc_value": "Infinity"},
                        {"lc_value": "not a number"}, {"commodity_quantities": ["40", "NaN"]}):
            with self.subTest(changes=changes):
                result = self.validate_first_snapshot(**changes)
                first, second = result.report_payload["families"][:2]
                self.assertEqual(first["final_decision"], "hard_block")
                self.assertEqual(second["final_workbook_value"], "OK (KGS)")
                self.assertEqual({op.column_key for op in result.validation_result.staged_write_plan
                                  if op.mail_id == first["family_id"]}, {"dashboard_status"})
                self.assertIsNone(first["written_shipment_date"])
                discrepancy = result.validation_result.discrepancy_reports[0]
                self.assertEqual(discrepancy.code, "bb_dashboard_numeric_input_invalid")
                self.assertTrue(discrepancy.details["issues"])
                self.assertEqual(discrepancy.details["dashboard"][next(iter(changes))], next(iter(changes.values())))
                self.assertIn("Invalid dashboard numeric input", result.report_html)

    def test_decimal_evaluation_error_is_contained(self):
        calls = 0

        def evaluate(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise InvalidOperation("fixture arithmetic error")
            return _evaluate_lookup_result(**kwargs)

        with patch("project.workflows.bb_dashboard_verification._evaluate_lookup_result", side_effect=evaluate):
            result = validate_bb_dashboard_verification_run(**self.args, dashboard_provider=self.provider)
        self.assertEqual(result.report_payload["families"][0]["final_decision"], "hard_block")
        self.assertEqual(result.report_payload["families"][1]["final_decision"], "pass")

    def test_dashboard_blanks_are_skipped_and_populated_rows_are_summed(self):
        result = self.validate_first_snapshot(commodity_quantities=["15", "", "  ", "25"])
        family = result.report_payload["families"][0]
        self.assertEqual(family["final_workbook_value"], "OK")
        self.assertEqual(family["erp"]["lc_qty"], "40")
        evidence = family["comparison_evidence"]
        quantity = next(item for item in evidence["fields"] if item["field"] == "LC Qty")
        self.assertEqual(quantity["normalized_dashboard"], "40")
        self.assertEqual(quantity["dashboard_minus_reference"], "0")
        self.assertNotIn("quantity_completeness", evidence["decision_summary"]["unavailable_rule_ids"])
        self.assertEqual({op.column_key for op in result.validation_result.staged_write_plan
                          if op.mail_id == family["family_id"]}, {"dashboard_status", "shipment_date", "expiry_date"})

    def test_aggregated_dashboard_difference_is_compared_not_individual_rows(self):
        result = self.validate_first_snapshot(commodity_quantities=["15", "", "20"])
        family = result.report_payload["families"][0]
        self.assertEqual(family["final_workbook_value"], "Quantity mismatch")
        self.assertTrue(any("35" in reason and "40" in reason for reason in family["decision_reasons"]))

    def test_nonfinite_required_erp_value_blocks_upstream(self):
        self.args["erp_rows"][0] = replace(self.args["erp_rows"][0], current_lc_value="NaN")
        result = validate_bb_dashboard_verification_run(**self.args, dashboard_provider=self.provider)
        first = result.report_payload["families"][0]
        self.assertEqual(first["final_decision"], "hard_block")
        self.assertEqual(first["search_attempts"], [])
        self.assertIsNone(first["written_expiry_date"])
        self.assertEqual(result.report_payload["families"][1]["final_decision"], "pass")

    def test_exception_report_matches_staged_columns(self):
        class Raises:
            def lookup_family(self, **kwargs):
                raise RuntimeError("fixture failure")

        result = validate_bb_dashboard_verification_run(**self.args, dashboard_provider=Raises())
        for family in result.report_payload["families"][:2]:
            self.assertEqual(family["final_decision"], "hard_block")
            self.assertIsNotNone(family["written_shipment_date"])
            self.assertIsNotNone(family["written_expiry_date"])
            self.assertEqual({op.column_key for op in result.validation_result.staged_write_plan
                              if op.mail_id == family["family_id"]}, {"dashboard_status", "shipment_date", "expiry_date"})
            for column, report_key in (("shipment_date", "written_shipment_date"), ("expiry_date", "written_expiry_date")):
                values = {op.expected_post_write_value for op in result.validation_result.staged_write_plan
                          if op.mail_id == family["family_id"] and op.column_key == column}
                self.assertEqual(values, {family[report_key]})

    def test_returned_fetch_error_preserves_existing_date_writes(self):
        class Error:
            def lookup_family(self, **kwargs):
                return DashboardLookupResult(outcome="fetch_error", attempts=[], message="fixture failure")

        result = validate_bb_dashboard_verification_run(**self.args, dashboard_provider=Error())
        family = result.report_payload["families"][0]
        self.assertEqual({op.column_key for op in result.validation_result.staged_write_plan
                          if op.mail_id == family["family_id"]}, {"dashboard_status", "shipment_date", "expiry_date"})
        self.assertEqual(family["written_shipment_date"], "10/02/2026")

    def test_erp_and_dashboard_blank_quantities_are_skipped_before_comparison(self):
        self.args["erp_rows"][0] = replace(self.args["erp_rows"][0], lc_qty="  ")
        result = self.validate_first_snapshot(commodity_quantities=["5", "", "15"])
        family = result.report_payload["families"][0]
        self.assertEqual(family["erp"]["lc_qty"], "20")
        self.assertEqual(family["final_workbook_value"], "OK")
        self.assertEqual(result.report_payload["families"][1]["final_workbook_value"], "OK (KGS)")

    def test_entirely_blank_erp_quantity_is_unavailable_not_zero(self):
        for index in (0, 1):
            self.args["erp_rows"][index] = replace(self.args["erp_rows"][index], lc_qty="")
        result = self.validate_first_snapshot(commodity_quantities=["0"])
        family = result.report_payload["families"][0]
        self.assertEqual(family["final_decision"], "hard_block")
        self.assertEqual(family["search_attempts"], [])
        self.assertIsNone(family["written_shipment_date"])

    def test_entirely_blank_dashboard_quantity_is_not_a_zero_match(self):
        for index in (0, 1):
            self.args["erp_rows"][index] = replace(self.args["erp_rows"][index], lc_qty="0")
        result = self.validate_first_snapshot(commodity_quantities=["", " "])
        self.assertEqual(result.report_payload["families"][0]["final_workbook_value"], "Quantity mismatch")
        result = self.validate_first_snapshot(commodity_quantities=["", "0"])
        self.assertEqual(result.report_payload["families"][0]["final_workbook_value"], "OK")

    def test_invalid_erp_family_dates_block_before_lookup_and_date_writes(self):
        original = list(self.args["erp_rows"])
        for field in ("ship_date", "expiry_date"):
            for values in (("", ""), ("not a date", "not a date"), ("2026-02-10", "2026-02-11")):
                with self.subTest(field=field, values=values):
                    self.args["erp_rows"] = [replace(original[0], **{field: values[0]}),
                                             replace(original[1], **{field: values[1]}), original[2]]
                    with patch.object(type(self.provider), "lookup_family", autospec=True,
                                      return_value=DashboardLookupResult(outcome="fetch_error", attempts=[])) as lookup:
                        result = validate_bb_dashboard_verification_run(**self.args, dashboard_provider=self.provider)
                    self.assertEqual(lookup.call_count, 1)  # Only the other valid ERP family reaches lookup.
                    family = result.report_payload["families"][0]
                    self.assertEqual(family["final_decision"], "hard_block")
                    self.assertIsNone(family["written_shipment_date"])
                    self.assertIsNone(family["written_expiry_date"])
                    self.assertEqual({op.column_key for op in result.validation_result.staged_write_plan
                                      if op.mail_id == family["family_id"]}, {"dashboard_status"})

    def test_eligible_missing_lc_row_is_reported_without_writes(self):
        snapshot = self.args["workbook_snapshot"]
        first = snapshot.rows[0]
        changed = replace(first, values={**first.values, 1: "002", 2: ""})
        self.args["workbook_snapshot"] = replace(snapshot, rows=[changed, *snapshot.rows[1:]])
        result = validate_bb_dashboard_verification_run(**self.args, dashboard_provider=self.provider)
        self.assertEqual(result.report_payload["skipped_rows"][0]["sl_no"], "002")
        self.assertFalse(any(op.row_index == first.row_index for op in result.validation_result.staged_write_plan))
        self.assertIn("Skipped workbook rows", result.report_html)
        self.assertEqual(result.report_payload["family_count"], 3)

    def test_display_text_survives_live_read_and_html_formatting(self):
        for text in ("002", "002.00", "1,002", "2E3", "21A"):
            with self.subTest(text=text):
                class Sheet:
                    def range(self, coordinates):
                        return type("Cell", (), {"api": type("Api", (), {"Text": text})()})()

                resolved = _read_live_display_values(sheet=Sheet(), column_index=1, row_indexes=[11])
                self.assertEqual(resolved[11], text)
                self.assertEqual(_format_report_sl_no_value(resolved[11]), text)

    def test_extreme_finite_arithmetic_failure_does_not_abort_report(self):
        for index in (0, 1):
            self.args["erp_rows"][index] = replace(self.args["erp_rows"][index],
                current_lc_value="-45000000000000000000000000", lc_qty="-45000000000000000000000000")
        result = self.validate_first_snapshot(lc_value="90000000000000000000000000",
                                              commodity_quantities=["90000000000000000000000000"])
        first, second = result.report_payload["families"][:2]
        self.assertEqual(first["final_decision"], "hard_block")
        self.assertEqual(first["dashboard"]["lc_value"], "90000000000000000000000000")
        self.assertEqual(first["comparison_evidence"]["availability"], "unavailable")
        self.assertIn("InvalidOperation", first["comparison_evidence"]["evidence_error"])
        self.assertEqual(second["final_decision"], "pass")
        self.assertIn("comparison_arithmetic", result.report_html)

    def test_distinct_amendments_are_aggregated_and_repeated_exports_deduped(self):
        first = replace(self.args["erp_rows"][0], amd_no="1", amd_date="2026-01-12")
        second = replace(first, amd_no="2", amd_date="2026-01-13", source_row_index=2)
        repeated = replace(second, source_row_index=99)
        self.args["erp_rows"] = [first, second, repeated, self.args["erp_rows"][2]]
        result = validate_bb_dashboard_verification_run(**self.args, dashboard_provider=self.provider)
        family = result.report_payload["families"][0]
        self.assertEqual(family["erp"]["source_row_count"], 2)
        self.assertEqual(family["erp"]["current_lc_value"], "100")
        self.assertEqual(family["erp"]["lc_qty"], "40")
        self.assertEqual(family["erp"]["net_weight"], "36")
        self.assertEqual(family["final_workbook_value"], "OK")

    def test_amendment_number_or_date_distinguishes_rows_and_blank_identity_still_dedupes(self):
        first = self.args["erp_rows"][0]
        for change in ({"amd_no": "1"}, {"amd_date": "2026-01-12"}):
            self.assertEqual(len(_dedupe_erp_rows([first, replace(first, **change)])), 2)
        self.assertEqual(len(_dedupe_erp_rows([first, replace(first, source_row_index=99)])), 1)

    def test_write_report_requires_committed_phase_and_matching_marker(self):
        result = validate_bb_dashboard_verification_run(**self.args, dashboard_provider=self.provider)
        validation = result.validation_result
        run = validation.run_report
        marker = WriteCommitMarker(run_id=run.run_id, workflow_id=run.workflow_id,
            tool_version=run.tool_version, rule_pack_version=run.rule_pack_version,
            committed_at_utc="2026-09-20T00:00:00Z", operation_count=len(validation.staged_write_plan),
            mail_iteration_order_hash="fixture", staged_write_plan_hash=run.staged_write_plan_hash,
            run_start_backup_hash=run.run_start_backup_hash, post_write_probe_summary={})
        for phase, commit, confirmed in (
            (WritePhaseStatus.NOT_STARTED, None, False),
            (WritePhaseStatus.PREVALIDATED, None, False),
            (WritePhaseStatus.HARD_BLOCKED_NO_WRITE, None, False),
            (WritePhaseStatus.UNCERTAIN_NOT_COMMITTED, None, False),
            (WritePhaseStatus.COMMITTED, None, False),
            (WritePhaseStatus.COMMITTED, replace(marker, operation_count=999), False),
            (WritePhaseStatus.COMMITTED, marker, True),
        ):
            with self.subTest(phase=phase, confirmed=confirmed, marker=commit):
                updated = replace(validation, run_report=replace(run, write_phase_status=phase), commit_marker=commit)
                refreshed = refresh_bb_dashboard_verification_report(result=result, validation_result=updated)
                self.assertEqual(refreshed.report_payload["write_phase_status"], phase.value)
                self.assertEqual(refreshed.report_payload["workbook_write"]["commit_confirmed"], confirmed)
                self.assertEqual(refreshed.report_payload["families"], result.report_payload["families"])
                self.assertIn("Workbook Write Outcome", refreshed.report_html)
                self.assertEqual(refreshed.report_payload["workbook_write"]["confirmed_operation_count"],
                                 len(validation.staged_write_plan) if confirmed else 0)

    def test_cli_refreshes_report_after_blocked_or_uncertain_write(self):
        for phase in (WritePhaseStatus.HARD_BLOCKED_NO_WRITE, WritePhaseStatus.UNCERTAIN_NOT_COMMITTED):
            with self.subTest(phase=phase), ExitStack() as stack:
                def validate(**kwargs):
                    kwargs["live_workbook_path"] = None
                    return validate_bb_dashboard_verification_run(**kwargs)

                def write(**kwargs):
                    result = kwargs["validation_result"]
                    discrepancy = replace(result.discrepancy_reports[0], mail_id=None,
                                          code="workbook_lock_conflict", message="Write failed <fixture>")
                    return replace(result, run_report=replace(result.run_report, write_phase_status=phase),
                                   discrepancy_reports=[*result.discrepancy_reports, discrepancy])

                stack.enter_context(patch("project.cli._load_workbook_snapshot", return_value=self.args["workbook_snapshot"]))
                stack.enter_context(patch("project.cli.validate_bb_dashboard_verification_run", side_effect=validate))
                stack.enter_context(patch("project.cli.execute_live_write_batch", side_effect=write))
                opened = stack.enter_context(patch("project.cli.open_bb_dashboard_verification_report_in_browser"))
                stack.enter_context(redirect_stdout(io.StringIO()))
                main(["validate-run", "bb_dashboard_verification", "--config", str(self.config_path),
                      "--live-workbook", "--erp-json", str(self.erp_path),
                      "--dashboard-json", str(self.dashboard_path), "--apply-live-writes"])
                html_path = opened.call_args.kwargs["html_path"]
                payload = json.loads((html_path.parent / "bb_dashboard_verification_report.json").read_text(encoding="utf-8"))
                html = html_path.read_text(encoding="utf-8")
                self.assertEqual(payload["write_phase_status"], phase.value)
                self.assertFalse(payload["workbook_write"]["commit_confirmed"])
                self.assertIn("Write failed &lt;fixture&gt;", html)
                self.assertEqual(payload["families"][0]["final_workbook_value"], "OK")
                self.assertIn("planned values", html)
