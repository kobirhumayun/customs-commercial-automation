from __future__ import annotations

import unittest

from project.workflows.ud_ip_exp import (
    UDIPEXPDocumentKind,
    document_kind_from_number,
    normalize_ud_ip_exp_document_number,
)


class UDIPEXPParsingTests(unittest.TestCase):
    def test_normalize_document_number_handles_documented_examples(self) -> None:
        self.assertEqual(
            normalize_ud_ip_exp_document_number("ip lc 0043 vintage denim studio ltd."),
            "IP-LC-0043-VINTAGE DENIM STUDIO LTD",
        )
        self.assertEqual(
            normalize_ud_ip_exp_document_number("exp-  9981 ;"),
            "EXP-9981",
        )

    def test_normalize_document_number_handles_mixed_separators(self) -> None:
        self.assertEqual(
            normalize_ud_ip_exp_document_number(" ud/sc/010 ananta garments ltd: "),
            "UD-SC-010-ANANTA GARMENTS LTD",
        )
        self.assertEqual(
            normalize_ud_ip_exp_document_number("IP\u2011LC\u20110043\u200b-Vintage"),
            "IP-LC-0043-VINTAGE",
        )

    def test_normalize_document_number_rejects_unknown_prefix_or_empty_body(self) -> None:
        self.assertIsNone(normalize_ud_ip_exp_document_number("invoice exp 9981"))
        self.assertIsNone(normalize_ud_ip_exp_document_number("UD ;"))

    def test_document_kind_from_number_returns_prefix_kind(self) -> None:
        self.assertEqual(document_kind_from_number("EXP-9981"), UDIPEXPDocumentKind.EXP)
        self.assertIsNone(document_kind_from_number("INV-9981"))


if __name__ == "__main__":
    unittest.main()
