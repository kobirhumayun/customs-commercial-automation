from __future__ import annotations

import unittest

from project.workflows.ud_ip_exp.structured_extraction import (
    StructuredUDExtractionContext,
    extract_structured_ud_analysis,
)


class UDIPEXPStructuredExtractionTests(unittest.TestCase):
    def test_extracts_base_ud_properties_from_layered_tables(self) -> None:
        analysis = extract_structured_ud_analysis(
            report=_base_report(),
            context=StructuredUDExtractionContext(
                erp_lc_sc_number="1345260400434",
                erp_ship_remarks="",
            ),
        )

        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.extracted_document_subtype, "base_ud")
        self.assertEqual(analysis.extracted_document_number, "BGMEA/DHK/UD/2026/5483/003")
        self.assertEqual(analysis.extracted_document_date, "2026-03-31")
        self.assertEqual(analysis.extracted_lc_sc_number, "1345260400434")
        self.assertEqual(analysis.extracted_lc_sc_date, "2026-03-16")
        self.assertEqual(analysis.extracted_lc_sc_value, "17375.8")
        self.assertEqual(analysis.extracted_quantity_by_unit, {"YDS": "6633"})

    def test_ship_remarks_match_has_priority_over_lc_number_match(self) -> None:
        report = _base_report()
        report["pages"][0]["tables"][2]["rows"].append(
            ["2", "SHIP-REMARKS-0434", "2026-03-17", "999.00", "999.00", "USD"]
        )

        analysis = extract_structured_ud_analysis(
            report=report,
            context=StructuredUDExtractionContext(
                erp_lc_sc_number="1345260400434",
                erp_ship_remarks="SHIP-REMARKS-0434",
            ),
        )

        self.assertEqual(analysis.extracted_lc_sc_number, "1345260400434")
        self.assertEqual(analysis.extracted_lc_sc_provenance["matched_identifier"], "SHIP-REMARKS-0434")
        self.assertEqual(analysis.extracted_lc_sc_date, "2026-03-17")
        self.assertEqual(analysis.extracted_lc_sc_value, "999")

    def test_lc_number_match_allows_only_left_zero_stripping(self) -> None:
        analysis = extract_structured_ud_analysis(
            report=_amendment_report(),
            context=StructuredUDExtractionContext(
                erp_lc_sc_number="0000201260400935",
                erp_ship_remarks="",
            ),
        )

        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.extracted_lc_sc_number, "0000201260400935")
        self.assertEqual(analysis.extracted_lc_sc_date, "2026-03-09")
        self.assertEqual(analysis.extracted_lc_sc_value, "69734.7")
        self.assertEqual(analysis.extracted_lc_sc_provenance["matched_identifier"], "0000201260400935")
        self.assertEqual(analysis.extracted_lc_sc_provenance["table_identifier"], "201260400935")
        self.assertEqual(analysis.extracted_lc_sc_provenance["match_strategy"], "leading_zero_stripped")

    def test_lc_number_match_trims_only_outer_spaces(self) -> None:
        report = _amendment_report()
        report["pages"][0]["tables"][2]["rows"][1][1] = "  201260400935  "

        analysis = extract_structured_ud_analysis(
            report=report,
            context=StructuredUDExtractionContext(
                erp_lc_sc_number="0000201260400935",
                erp_ship_remarks="",
            ),
        )

        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.extracted_lc_sc_number, "0000201260400935")
        self.assertEqual(analysis.extracted_lc_sc_provenance["table_identifier"], "201260400935")
        self.assertEqual(analysis.extracted_lc_sc_provenance["match_strategy"], "leading_zero_stripped")

    def test_lc_number_match_does_not_change_internal_spaces(self) -> None:
        report = _amendment_report()
        report["pages"][0]["tables"][2]["rows"][1][1] = "201 260400935"

        analysis = extract_structured_ud_analysis(
            report=report,
            context=StructuredUDExtractionContext(
                erp_lc_sc_number="0000201260400935",
                erp_ship_remarks="",
            ),
        )

        self.assertIsNotNone(analysis)
        self.assertIsNone(analysis.extracted_lc_sc_number)
        self.assertIsNone(analysis.extracted_lc_sc_date)
        self.assertIsNone(analysis.extracted_lc_sc_value)

    def test_ship_remarks_match_does_not_strip_left_zeros(self) -> None:
        report = _base_report()
        report["pages"][0]["tables"][2]["rows"] = [
            ["SL No", "32. Import L/C No.", "33. Date", "34. Value", "Used Value", "35. Currency"],
            ["1", "12345", "2026-03-16", "17375.8", "17375.8", "USD"],
        ]

        analysis = extract_structured_ud_analysis(
            report=report,
            context=StructuredUDExtractionContext(
                erp_lc_sc_number="99999",
                erp_ship_remarks="00012345",
            ),
        )

        self.assertIsNotNone(analysis)
        self.assertIsNone(analysis.extracted_lc_sc_number)
        self.assertIsNone(analysis.extracted_lc_sc_date)
        self.assertIsNone(analysis.extracted_lc_sc_value)

    def test_layered_category_fallback_does_not_double_count_table_and_img2table(self) -> None:
        table_report = _base_report()
        category_page = table_report["pages"][0]
        report = {
            "combined_text": "UD Authenticating Authority",
            "pages": [{"page_number": 1, "searchable_text": "UD Authenticating Authority"}],
            "categories": {
                "table": {"pages": [category_page]},
                "img2table": {"pages": [category_page]},
            },
        }

        analysis = extract_structured_ud_analysis(
            report=report,
            context=StructuredUDExtractionContext(
                erp_lc_sc_number="1345260400434",
                erp_ship_remarks="",
            ),
        )

        self.assertEqual(analysis.extracted_quantity_by_unit, {"YDS": "6633"})

    def test_extracts_amendment_properties_from_layered_tables(self) -> None:
        analysis = extract_structured_ud_analysis(
            report=_amendment_report(),
            context=StructuredUDExtractionContext(
                erp_lc_sc_number="201260400935",
                erp_ship_remarks="",
            ),
        )

        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.extracted_document_subtype, "ud_amendment")
        self.assertEqual(analysis.extracted_document_number, "BGMEA/DHK/AM/2026/3420/004-010")
        self.assertEqual(analysis.extracted_document_date, "2026-04-12")
        self.assertEqual(analysis.extracted_lc_sc_number, "201260400935")
        self.assertEqual(analysis.extracted_lc_sc_date, "2026-03-09")
        self.assertEqual(analysis.extracted_lc_sc_value, "69734.7")
        self.assertEqual(analysis.extracted_quantity_by_unit, {"YDS": "21390"})


def _base_report() -> dict:
    return {
        "combined_text": "UD Authenticating Authority",
        "pages": [
            {
                "page_number": 1,
                "searchable_text": "UD Authenticating Authority",
                "tables": [
                    {"table_index": 1, "rows": [["01.", "Name"]]},
                    {
                        "table_index": 2,
                        "rows": [
                            ["03. Application No", "2603310081", "Date", "2026-03-31"],
                            ["04. UD No (For office use only)", "BGMEA/DHK/UD/2026/5483/003", "Date", "2026-03-31"],
                        ],
                    },
                    {
                        "table_index": 3,
                        "rows": [
                            ["SL No", "32. Import L/C No.", "33. Date", "34. Value", "Used Value", "35. Currency"],
                            ["1", "1345260400434", "2026-03-16", "17375.8", "17375.8", "USD"],
                        ],
                    },
                    {
                        "table_index": 4,
                        "rows": [
                            ["Fabric Description", "Qty", "Unit", "Net Weight", "Unit", "Country", "Supplierinfo"],
                            ["98% COTTON", "1300", "YRD", "0", "KGM", "Bangladesh", "PIONEER DENIM LIMITED"],
                            ["98% COTTON", "5333", "YRD", "0", "KGM", "Bangladesh", "DO"],
                            ["Total", "6633", "YRD", "", "", "", ""],
                        ],
                    },
                ],
            }
        ],
    }


def _amendment_report() -> dict:
    return {
        "combined_text": "Amendment Authenticating Authority",
        "pages": [
            {
                "page_number": 1,
                "searchable_text": "Amendment Authenticating Authority",
                "tables": [
                    {"table_index": 1, "rows": [["01.", "Name"]]},
                    {
                        "table_index": 2,
                        "rows": [
                            ["UD No.: BGMEA/DHK/UD/2026/3420/004", "Date", "2026-01-18"],
                            ["Amendment no. (For office use only)", "BGMEA/DHK/AM/2026/3420/004-010", "Date", "2026-04-12"],
                        ],
                    },
                    {
                        "table_index": 3,
                        "rows": [
                            ["SL No", "Back-to-Back LC/Sight/Usance", "Date", "Value", "Increased/Decreased", "Total Value"],
                            ["7", "201260400935", "2026-03-09", "USD 89,675.00", "USD 69,734.70", "USD 159,409.70"],
                        ],
                    },
                    {
                        "table_index": 4,
                        "rows": [
                            ["Fabric/Yarn Description", "Qty", "Unit", "Net Weight", "Unit", "Country Name", "Supplier Info"],
                            ["DENIM", "410", "YRD", "0", "KGM", "Bangladesh", "PIONEER DENIM LIMITED"],
                            ["DENIM", "20980", "YRD", "0", "KGM", "Bangladesh", "DO"],
                        ],
                    },
                ],
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
