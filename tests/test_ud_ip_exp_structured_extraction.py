from __future__ import annotations

import unittest

from project.workflows.ud_ip_exp.structured_extraction import (
    StructuredUDExtractionContext,
    extract_structured_ud_analysis,
)


class UDIPEXPStructuredExtractionTests(unittest.TestCase):
    def test_merged_page_tables_preserve_all_base_and_amendment_values(self) -> None:
        for amendment, lc in ((False, "1345260400434"), (True, "201260400935")):
            with self.subTest(amendment=amendment):
                old_report = _amendment_report() if amendment else _base_report()
                context = StructuredUDExtractionContext(erp_lc_sc_number=lc)
                old = extract_structured_ud_analysis(report=old_report, context=context)
                new = extract_structured_ud_analysis(report=_merged_report(amendment), context=context)
                for field in (
                    "extracted_document_subtype", "extracted_document_number",
                    "extracted_document_date", "extracted_lc_sc_number",
                    "extracted_lc_sc_date", "extracted_lc_sc_value",
                    "extracted_lc_sc_value_currency", "extracted_quantity_by_unit",
                ):
                    self.assertEqual(getattr(new, field), getattr(old, field), field)
                self.assertEqual(new.extracted_lc_sc_provenance["row_index"], 4)
                self.assertEqual(new.extracted_lc_sc_provenance["value_column_index"], 10 if amendment else 5)

    def test_merged_amendment_zero_uses_original_value_column(self) -> None:
        report = _merged_report(True)
        report["pages"][0]["tables"][0]["rows"][4][10] = "USD 0.00"
        analysis = extract_structured_ud_analysis(
            report=report, context=StructuredUDExtractionContext("201260400935"),
        )
        self.assertEqual(analysis.extracted_lc_sc_value, "89675")
        self.assertEqual(analysis.extracted_lc_sc_value_provenance["value_column_index"], 7)

    def test_merged_base_ud_repeated_foreign_and_local_lc_sections(self) -> None:
        report = _merged_report(False)
        rows = report["pages"][0]["tables"][0]["rows"]
        header, local_row = list(rows[3]), list(rows[4])
        rows[4][2] = "FOREIGN-LC"
        foreign_label = [""] * len(header)
        foreign_label[0] = "Foreign"
        local_label = [""] * len(header)
        local_label[0] = "31. Local"
        rows.insert(4, foreign_label)
        rows[6:6] = [local_label, header, local_row]
        for lc, expected_row in (("FOREIGN-LC", 5), ("1345260400434", 8)):
            with self.subTest(lc=lc):
                analysis = extract_structured_ud_analysis(
                    report=report, context=StructuredUDExtractionContext(lc),
                )
                self.assertEqual(analysis.extracted_lc_sc_value, "17375.8")
                self.assertEqual(analysis.extracted_lc_sc_provenance["row_index"], expected_row)

    def test_merged_blank_increase_does_not_shift_total_into_value(self) -> None:
        report = _merged_report(True)
        report["pages"][0]["tables"][0]["rows"][4][10] = ""
        analysis = extract_structured_ud_analysis(
            report=report, context=StructuredUDExtractionContext("201260400935"),
        )
        self.assertIsNone(analysis.extracted_lc_sc_value)

    def test_merged_missing_office_number_does_not_use_date_or_other_row(self) -> None:
        report = _merged_report(True)
        report["pages"][0]["tables"][0]["rows"][1][2] = ""
        analysis = extract_structured_ud_analysis(
            report=report, context=StructuredUDExtractionContext("201260400935"),
        )
        self.assertIsNone(analysis.extracted_document_number)
        self.assertEqual(analysis.extracted_document_date, "2026-04-12")

    def test_merged_table_stops_at_next_section(self) -> None:
        report = _merged_report(True)
        rows = report["pages"][0]["tables"][0]["rows"]
        rows[4][1] = "OTHER-LC"
        rows[6] = list(rows[4])
        rows[6][1] = "201260400935"
        analysis = extract_structured_ud_analysis(
            report=report, context=StructuredUDExtractionContext("201260400935"),
        )
        self.assertIsNone(analysis.extracted_lc_sc_value)

    def test_merged_quantity_headers_repeat_without_double_counting(self) -> None:
        report = _merged_report(True)
        rows = report["pages"][0]["tables"][0]["rows"]
        header = list(rows[7])
        second_supplier_row = rows.pop(9)
        report["pages"].append({"page_number": 2, "tables": [{
            "table_index": 1, "rows": [header, second_supplier_row],
        }]})
        analysis = extract_structured_ud_analysis(
            report=report, context=StructuredUDExtractionContext("201260400935"),
        )
        self.assertEqual(analysis.extracted_quantity_by_unit, {"YDS": "21390"})

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

    def test_office_use_only_row_is_the_only_base_ud_number_source(self) -> None:
        report = _base_report()
        report["pages"][0]["tables"][1]["rows"].insert(
            0,
            ["03A. Tracking", "BGMEA/DHK/UD/2026/9999/999", "Date", "2026-03-30"],
        )

        analysis = extract_structured_ud_analysis(
            report=report,
            context=StructuredUDExtractionContext(
                erp_lc_sc_number="1345260400434",
                erp_ship_remarks="",
            ),
        )

        self.assertEqual(analysis.extracted_document_number, "BGMEA/DHK/UD/2026/5483/003")
        self.assertEqual(analysis.extracted_document_date, "2026-03-31")

    def test_base_ud_requires_for_office_use_only_label_without_fallback(self) -> None:
        report = _base_report()
        report["pages"][0]["tables"][1]["rows"][1] = [
            "04. UD No",
            "BGMEA/DHK/UD/2026/5483/003",
            "Date",
            "2026-03-31",
        ]
        report["pages"][0]["tables"][1]["rows"].append(
            ["05. Duplicate", "BGMEA/DHK/UD/2026/9999/999", "Date", "2026-04-01"]
        )

        analysis = extract_structured_ud_analysis(
            report=report,
            context=StructuredUDExtractionContext(
                erp_lc_sc_number="1345260400434",
                erp_ship_remarks="",
            ),
        )

        self.assertIsNotNone(analysis)
        self.assertIsNone(analysis.extracted_document_number)
        self.assertIsNone(analysis.extracted_document_date)

    def test_invalid_office_use_only_ud_number_is_extracted_from_same_row(self) -> None:
        report = _base_report()
        report["pages"][0]["tables"][1]["rows"][1] = [
            "04. UD No (For office use only)",
            "BGMEA/DHK/W/2026/5483/003",
            "Date",
            "2026-03-31",
        ]

        analysis = extract_structured_ud_analysis(
            report=report,
            context=StructuredUDExtractionContext(
                erp_lc_sc_number="1345260400434",
                erp_ship_remarks="",
            ),
        )

        self.assertEqual(analysis.extracted_document_number, "BGMEA/DHK/W/2026/5483/003")
        self.assertEqual(analysis.extracted_document_date, "2026-03-31")

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

    def test_ship_remarks_match_allows_only_left_zero_stripping(self) -> None:
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
        self.assertEqual(analysis.extracted_lc_sc_number, "99999")
        self.assertEqual(analysis.extracted_lc_sc_date, "2026-03-16")
        self.assertEqual(analysis.extracted_lc_sc_value, "17375.8")
        self.assertEqual(analysis.extracted_lc_sc_provenance["matched_identifier"], "00012345")
        self.assertEqual(analysis.extracted_lc_sc_provenance["table_identifier"], "12345")
        self.assertEqual(analysis.extracted_lc_sc_provenance["identifier_source"], "ship_remarks")
        self.assertEqual(analysis.extracted_lc_sc_provenance["match_strategy"], "leading_zero_stripped")

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

    def test_extracts_ctg_amendment_document_number_and_date(self) -> None:
        report = _amendment_report()
        report["pages"][0]["tables"][1]["rows"][1] = [
            "Amendment no. (For office use only)",
            "BGMEA/CTG/AM/2026/6425/020-010",
            "Date",
            "2026-04-16",
        ]

        analysis = extract_structured_ud_analysis(
            report=report,
            context=StructuredUDExtractionContext(
                erp_lc_sc_number="201260400935",
                erp_ship_remarks="",
            ),
        )

        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.extracted_document_subtype, "ud_amendment")
        self.assertEqual(analysis.extracted_document_number, "BGMEA/CTG/AM/2026/6425/020-010")
        self.assertEqual(analysis.extracted_document_date, "2026-04-16")

    def test_amendment_requires_for_office_use_only_label_without_fallback(self) -> None:
        report = _amendment_report()
        report["pages"][0]["tables"][1]["rows"][1] = [
            "Amendment no.",
            "BGMEA/DHK/AM/2026/3420/004-010",
            "Date",
            "2026-04-12",
        ]
        report["pages"][0]["tables"][1]["rows"].append(
            ["Other row", "BGMEA/DHK/AM/2026/9999/999-001", "Date", "2026-04-13"]
        )

        analysis = extract_structured_ud_analysis(
            report=report,
            context=StructuredUDExtractionContext(
                erp_lc_sc_number="201260400935",
                erp_ship_remarks="",
            ),
        )

        self.assertIsNotNone(analysis)
        self.assertIsNone(analysis.extracted_document_number)
        self.assertIsNone(analysis.extracted_document_date)

    def test_amendment_uses_value_column_when_increased_decreased_value_is_zero(self) -> None:
        report = _amendment_report()
        report["pages"][0]["tables"][2]["rows"][1] = [
            "7",
            "201260400935",
            "2026-03-09",
            "USD 89,675.00",
            "USD 0.00",
            "USD 89,675.00",
        ]

        analysis = extract_structured_ud_analysis(
            report=report,
            context=StructuredUDExtractionContext(
                erp_lc_sc_number="201260400935",
                erp_ship_remarks="",
            ),
        )

        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.extracted_lc_sc_value, "89675")
        self.assertEqual(analysis.extracted_lc_sc_value_currency, "USD")
        self.assertEqual(analysis.extracted_lc_sc_value_provenance["value_column_index"], 3)
        self.assertEqual(
            analysis.extracted_lc_sc_value_provenance["value_strategy"],
            "amendment_zero_increased_decreased_used_value_column",
        )

    def test_amendment_matches_lc_row_from_continuation_table(self) -> None:
        report = _amendment_report()
        report["pages"][0]["tables"][2]["rows"] = [
            ["SL No", "Back-to-Back LC/Sight/Usance", "Date", "Value", "Increased/Decreased", "Total Value"],
            ["Authorized Signature", "", "", "", "", ""],
        ]
        report["pages"][0]["tables"].insert(
            3,
            {
                "table_index": 31,
                "rows": [
                    ["2", "1416260401030", "2026-03-29", "USD 438,226.80", "USD 78,255.50", "USD 516,482.30"],
                ],
            },
        )

        analysis = extract_structured_ud_analysis(
            report=report,
            context=StructuredUDExtractionContext(
                erp_lc_sc_number="1416260401030",
                erp_ship_remarks="",
            ),
        )

        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.extracted_lc_sc_number, "1416260401030")
        self.assertEqual(analysis.extracted_lc_sc_date, "2026-03-29")
        self.assertEqual(analysis.extracted_lc_sc_value, "78255.5")
        self.assertEqual(analysis.extracted_lc_sc_provenance["table_index"], 31)
        self.assertEqual(analysis.extracted_lc_sc_provenance["row_index"], 0)


def _merged_report(amendment: bool) -> dict:
    # Reproduce the September PDFs' page-wide grids, embedded section headers,
    # and spacer-column positions. Data stays aligned to header columns even
    # when a real value is missing (removing empty data cells would be unsafe).
    report = _amendment_report() if amendment else _base_report()
    tables = report["pages"][0]["tables"]
    width = 21

    def spread(row, columns):
        result = [""] * width
        for column, value in zip(columns, row):
            result[column] = value
        return result

    office_columns = [0, 2, 7, 8] if amendment else [0, 5, 14, 17]
    lc_columns = [0, 1, 6, 7, 10, 13] if amendment else [0, 2, 3, 5, 7, 9]
    quantity_columns = [0, 3, 4, 6, 8, 9, 10] if amendment else [0, 5, 7, 10, 12, 13, 16]
    rows = [
        spread(["Application details"], [0]),
        spread(tables[1]["rows"][1], office_columns),
        spread(["Back to Back L/C"], [0]),
        *[spread(row, lc_columns) for row in tables[2]["rows"]],
        spread(["Other section"], [0]),
        spread(["unrelated", "999999"], [0, 1]),
        *[spread(row, quantity_columns) for row in tables[3]["rows"]],
    ]
    report["pages"][0]["tables"] = [{"table_index": 1, "rows": rows}]
    return report


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
