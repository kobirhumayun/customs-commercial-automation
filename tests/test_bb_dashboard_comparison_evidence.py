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
