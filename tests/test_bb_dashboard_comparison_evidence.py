from dataclasses import replace
from decimal import Decimal, InvalidOperation
import unittest

from project.workflows.bb_dashboard_verification import (
    DashboardCandidateFamily, ERPFamilyAggregate, _build_comparison_evidence,
    _compare_dashboard_snapshot, _compare_value_and_quantity, _parse_decimal,
    _render_comparison_evidence, _sum_decimal_strings,
)
from project.workflows.bb_dashboard_verification.providers import DashboardFamilySnapshot


class ComparisonEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.family = DashboardCandidateFamily("family", "LC-1", "LC 1", [2], ["002"], ["MLC-1"], [])
        self.erp = ERPFamilyAggregate(
            "LC-1", "LC 1", "BUYER LTD", "2026-01-01", "2026-02-01", "2026-03-01",
            Decimal("1000"), Decimal("100"), Decimal("80"), None, 1,
        )
        self.snapshot = DashboardFamilySnapshot(
            beneficiary_name="PIONEER DENIM LIMITED", irc_details="BUYER LIMITED", erc_details="",
            lc_date="01-Jan-26", last_date_of_shipment="2026-02-01", lc_expiry_date="2026-03-01",
            lc_value="1000", foreign_lc_numbers=["MLC-1"], commodity_quantities=["100"],
        )

    def evidence(self, snapshot=None):
        return _build_comparison_evidence(family=self.family, aggregate=self.erp, snapshot=snapshot or self.snapshot)

    def test_accepted_differences_retain_rule_result_and_delta(self):
        for value, quantity, expected in (("1000", "80.79", "OK (KGS)"), ("1100", "120", "OK"), ("900", "90", "mismatch")):
            with self.subTest(expected=expected):
                snapshot = replace(self.snapshot, lc_value=value, commodity_quantities=[quantity])
                before = _compare_dashboard_snapshot(family=self.family, aggregate=self.erp, snapshot=snapshot)
                evidence = self.evidence(snapshot)
                self.assertEqual(evidence["numeric_rule_status"], expected)
                self.assertEqual(Decimal(evidence["fields"][0]["dashboard_minus_reference"]), Decimal(value) - 1000)
                self.assertEqual(before, _compare_dashboard_snapshot(family=self.family, aggregate=self.erp, snapshot=snapshot))

    def test_evidence_distinguishes_sources_and_normalizes_dates(self):
        fields = {item["field"]: item for item in self.evidence()["fields"]}
        self.assertEqual(fields["LC Date"]["normalized_dashboard"], "2026-01-01")
        self.assertEqual(fields["LC Date"]["dashboard_minus_reference"], 0)
        self.assertEqual(fields["Beneficiary"]["reference_source"], "Configured beneficiary")
        self.assertEqual(fields["Foreign LC"]["reference_source"], "Workbook Master L/C No.")

    def test_partial_quantities_are_observed_without_changing_summation(self):
        evidence = self.evidence(replace(self.snapshot, commodity_quantities=["100", ""]))
        self.assertEqual(evidence["numeric_rule_status"], "OK")
        self.assertTrue(evidence["input_observations"])
        self.assertEqual(_sum_decimal_strings(["100", ""]), Decimal("100"))

    def test_invalid_evidence_and_missing_snapshot_are_safe(self):
        for value in ("NaN", "invalid", "Infinity", ""):
            with self.subTest(value=value):
                self.assertIsNone(self.evidence(replace(self.snapshot, lc_value=value))["numeric_rule_status"])
        self.assertEqual(_build_comparison_evidence(family=self.family, aggregate=self.erp, snapshot=None)["availability"], "unavailable")

    def test_html_escapes_source_values_and_supports_old_reports(self):
        evidence = self.evidence(replace(self.snapshot, irc_details="<script>alert(1)</script>"))
        html = _render_comparison_evidence([{"lc_sc_no": "LC-1", "sl_no_values": ["002"], "comparison_evidence": evidence}])
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("SL.No. 2", html)  # Keep the existing report display convention.
        self.assertEqual(_render_comparison_evidence([{"lc_sc_no": "legacy"}]), "")

    def test_characterize_unparseable_lc_dates_and_nonfinite_numbers(self):
        # Existing behavior retained pending explicit approval of decision changes.
        comparison = _compare_dashboard_snapshot(
            family=self.family, aggregate=replace(self.erp, lc_date="invalid"),
            snapshot=replace(self.snapshot, lc_date="also invalid"),
        )
        self.assertEqual(comparison["status"], "OK")
        with self.assertRaises(InvalidOperation):
            _compare_value_and_quantity(dashboard_lc_value=_parse_decimal("NaN"), quantity_sum=Decimal("100"), aggregate=self.erp)

    def rules(self, snapshot=None, aggregate=None, family=None):
        evidence = _build_comparison_evidence(
            family=family or self.family, aggregate=aggregate or self.erp, snapshot=snapshot or self.snapshot,
        )
        return {rule["rule_id"]: rule for rule in evidence["rule_results"]}

    def test_mixed_numeric_failures_explain_both_relations(self):
        for value, quantity, value_status, quantity_status in (
            ("1100", "90", "pass", "fail"), ("900", "120", "fail", "fail"),
            ("1100", "100", "pass", "pass"),
        ):
            with self.subTest(value=value, quantity=quantity):
                rules = self.rules(replace(self.snapshot, lc_value=value, commodity_quantities=[quantity]))
                self.assertEqual(rules["value_floor"]["status"], value_status)
                self.assertEqual(rules["lc_quantity_match"]["status"], quantity_status)
                self.assertEqual(rules["excess_alternative"]["status"], "fail")
                self.assertIn("Both must be higher", rules["excess_alternative"]["reason"])

    def test_bad_value_does_not_hide_independent_quantity_failure(self):
        rules = self.rules(replace(self.snapshot, lc_value="invalid", commodity_quantities=["90"]))
        self.assertEqual(rules["value_floor"]["status"], "unavailable")
        self.assertEqual(rules["lc_quantity_match"]["status"], "fail")
        self.assertEqual(rules["excess_alternative"]["status"], "unavailable")

    def test_alternative_paths_and_single_buyer_section(self):
        rules = self.rules()
        self.assertEqual(rules["exact_value_quantity"]["status"], "pass")
        self.assertEqual(rules["kgs_alternative"]["status"], "not_applicable")
        self.assertEqual(rules["erc_buyer"]["status"], "not_applicable")
        self.assertEqual(rules["irc_buyer"]["status"], "pass")
        rules = self.rules(replace(self.snapshot, commodity_quantities=["80.79"]))
        self.assertEqual(rules["kgs_alternative"]["status"], "pass")
        self.assertEqual(rules["lc_quantity_match"]["status"], "not_applicable")
        self.assertEqual(rules["excess_alternative"]["status"], "not_applicable")
        rules = self.rules(replace(self.snapshot, commodity_quantities=["80.81"]))
        self.assertEqual(rules["kgs_alternative"]["status"], "fail")
        rules = self.rules(replace(self.snapshot, commodity_quantities=["90"]), aggregate=replace(self.erp, net_weight=None))
        self.assertEqual(rules["kgs_alternative"]["status"], "unavailable")
        self.assertIn("net weight is missing", rules["kgs_alternative"]["reason"])

    def test_excess_reports_minimum_and_ratio_even_when_both_fail(self):
        for value, quantity, minimum, ratio in (("1050", "190", "fail", "fail"), ("1100", "120", "pass", "pass"), ("1100", "180", "pass", "pass")):
            with self.subTest(value=value, quantity=quantity):
                rules = self.rules(replace(self.snapshot, lc_value=value, commodity_quantities=[quantity]))
                self.assertEqual(rules["excess_minimum"]["status"], minimum)
                self.assertEqual(rules["excess_quantity_range"]["status"], ratio)
                if minimum == ratio == "pass":
                    self.assertEqual(rules["lc_quantity_match"]["status"], "not_applicable")

    def test_expiry_checks_have_independent_outcomes_and_offsets(self):
        rules = self.rules(replace(self.snapshot, lc_expiry_date="2026-02-04"))
        self.assertEqual(rules["expiry_order"]["status"], "fail")
        self.assertEqual(rules["dashboard_expiry_window"]["status"], "fail")
        self.assertIn("3 days", rules["dashboard_expiry_window"]["reason"])
        self.assertEqual(rules["erp_expiry_window"]["status"], "pass")

    def test_missing_malformed_and_nonoverlapping_inputs_are_distinct(self):
        for raw, expected in (("", "missing"), ("invalid", "malformed")):
            rules = self.rules(replace(self.snapshot, lc_date=raw))
            self.assertEqual(rules["lc_date"]["status"], "unavailable")
            self.assertIn(expected, rules["lc_date"]["reason"])
        rules = self.rules(replace(self.snapshot, lc_date="invalid"), aggregate=replace(self.erp, lc_date="invalid"))
        self.assertIn("treats two unparseable dates as equal", rules["lc_date"]["reason"])
        rules = self.rules(replace(self.snapshot, foreign_lc_numbers=[]), family=replace(self.family, master_lc_values=[]))
        self.assertIn("Workbook Master", rules["foreign_lc_overlap"]["reason"])
        self.assertIn("Dashboard Foreign", rules["foreign_lc_overlap"]["reason"])
        rules = self.rules(replace(self.snapshot, foreign_lc_numbers=["different"]))
        self.assertIn("Both reference lists are populated", rules["foreign_lc_overlap"]["reason"])
        rules = self.rules(replace(self.snapshot, commodity_quantities=["100", "oops"]))
        self.assertIn("invalid", rules["dashboard_quantity_row_2"]["reason"])
        self.assertEqual(rules["lc_quantity_match"]["status"], "unavailable")

    def test_rule_explanations_render_and_legacy_evidence_still_renders(self):
        html = _render_comparison_evidence([{"comparison_evidence": self.evidence()}])
        self.assertIn("<th>Prerequisites</th>", html)
        self.assertIn("dashboard_expiry_window", html)
        self.assertIn("not_applicable", html)
        self.assertIn("Comparison Evidence", _render_comparison_evidence([{"comparison_evidence": {"fields": []}}]))

    def test_summary_paths_come_from_rule_results(self):
        for value, quantity, path in (("1000", "100", "Value and LC quantity within tolerance"),
                                      ("1000", "80.79", "KGS alternative"),
                                      ("1100", "120", "Approved excess"),
                                      ("900", "120", "No numeric acceptance path"),
                                      ("bad", "100", "Unavailable")):
            with self.subTest(path=path):
                evidence = self.evidence(replace(self.snapshot, lc_value=value, commodity_quantities=[quantity]))
                self.assertEqual(evidence["decision_summary"]["numeric_path"], path)
                self.assertEqual(evidence["decision_summary"]["failed_rule_ids"],
                                 [rule["rule_id"] for rule in evidence["rule_results"] if rule["status"] == "fail"])

    def test_unused_and_unqualified_alternatives_have_distinct_evaluations(self):
        self.assertEqual(self.rules()["kgs_alternative"]["evaluation"], "Not required")
        rules = self.rules(replace(self.snapshot, lc_value="1100", commodity_quantities=["120"]))
        self.assertEqual(rules["exact_value_quantity"]["evaluation"], "Evaluated; did not qualify")
        self.assertEqual(rules["kgs_alternative"]["evaluation"], "Not eligible: LC value does not match")
        self.assertEqual(rules["lc_quantity_match"]["evaluation"], "Difference accepted by alternative path")

    def test_table_labels_units_differences_and_intervals(self):
        fields = {field["field"]: field for field in self.evidence()["fields"]}
        self.assertEqual(fields["LC Value"]["display_source"], "ERP aggregate")
        self.assertIn("currency not captured", fields["LC Value"]["difference_basis"])
        self.assertEqual(fields["Beneficiary"]["difference_display"], "Not applicable")
        self.assertEqual(fields["Dashboard shipment to expiry"]["difference_display"], "28")
        self.assertEqual(fields["ERP shipment to expiry"]["rule_ids"], ["erp_expiry_window"])
        evidence = self.evidence(replace(self.snapshot, lc_date="invalid"))
        self.assertEqual(next(field for field in evidence["fields"] if field["field"] == "LC Date")["difference_display"], "Unavailable")

    def test_summary_keeps_numeric_acceptance_distinct_from_family_failure(self):
        evidence = self.evidence(replace(self.snapshot, beneficiary_name="WRONG"))
        family = {"final_decision": "warning", "final_workbook_value": "Beneficiary mismatch",
                  "decision_reasons": ["Beneficiary mismatch"], "comparison_evidence": evidence}
        html = _render_comparison_evidence([family, family])
        self.assertIn("Overall decision: warning", html)
        self.assertIn("Numeric acceptance path: Value and LC quantity within tolerance", html)
        self.assertNotIn("Numeric rule result: OK", html)
        self.assertIn('href="#evidence-0-beneficiary"', html)
        self.assertIn('id="evidence-1-beneficiary"', html)
        self.assertIn("Commodity rows:<br>100<br>Total: 100", html)
        self.assertNotIn("[&#x27;100&#x27;]", html)
        self.assertIn("<summary>Rule reference</summary>", html)
        self.assertIn("0.01", html)
        self.assertIn("0.8", html)

    def test_missing_evidence_is_explicit_in_summary(self):
        evidence = _build_comparison_evidence(family=self.family, aggregate=self.erp, snapshot=None)
        html = _render_comparison_evidence([{"comparison_evidence": evidence}])
        self.assertIn("Numeric acceptance path: Unavailable", html)
        self.assertIn("comparison_inputs", html)
        legacy_html = _render_comparison_evidence([{"comparison_evidence": {"fields": []}}])
        self.assertIn("No rule diagnostics recorded", legacy_html)
