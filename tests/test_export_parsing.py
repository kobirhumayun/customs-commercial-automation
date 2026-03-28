from __future__ import annotations

import unittest

from project.workflows.export_lc_sc.parsing import extract_file_numbers, normalize_file_number, parse_export_subject


class ExportParsingTests(unittest.TestCase):
    def test_normalize_file_number_accepts_supported_variants(self) -> None:
        self.assertEqual(normalize_file_number("p/26/42"), "P/26/0042")
        self.assertEqual(normalize_file_number(" P-26-0042 "), "P/26/0042")
        self.assertEqual(normalize_file_number(r"P\26\7"), "P/26/0007")

    def test_extract_file_numbers_returns_canonical_unique_values_in_order(self) -> None:
        body_text = "Files P/26/42, p-26-0007 and duplicate P/26/0042 are attached."

        self.assertEqual(
            extract_file_numbers(body_text),
            ["P/26/0042", "P/26/0007"],
        )

    def test_parse_export_subject_handles_supported_examples(self) -> None:
        parsed = parse_export_subject("SC-010-PDL-8-ZYTA APPARELS LTD")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.prefix, "SC")
        self.assertEqual(parsed.lc_sc_number, "SC-010-PDL-8")
        self.assertEqual(parsed.lc_sc_number_end_sequence, "8")
        self.assertEqual(parsed.buyer_name, "ZYTA APPARELS LTD")
        self.assertEqual(parsed.suffix_tokens, [])


if __name__ == "__main__":
    unittest.main()
