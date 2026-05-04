from __future__ import annotations

from decimal import Decimal
import unittest

from project.workbook import WorkbookHeader, WorkbookRow, WorkbookSnapshot
from project.workflows.ud_ip_exp import (
    MAX_UD_SELECTION_REPORT_CANDIDATES,
    allocate_structured_ud_rows,
)


class UDIPEXPMatchingTests(unittest.TestCase):
    def test_allocate_structured_ud_rows_selects_exact_value_group_then_checks_quantity(self) -> None:
        snapshot = _structured_snapshot(
            rows=[
                WorkbookRow(row_index=11, values={1: "1345260400434", 2: "1000 YDS", 3: "", 4: "", 5: "", 6: "10000"}),
                WorkbookRow(row_index=12, values={1: "1345260400434", 2: "2000 YDS", 3: "", 4: "", 5: "", 6: "7375.80"}),
                WorkbookRow(row_index=13, values={1: "1345260400434", 2: "500 YDS", 3: "", 4: "", 5: "", 6: "99"}),
            ]
        )

        result = allocate_structured_ud_rows(
            workbook_snapshot=snapshot,
            lc_sc_number="1345260400434",
            lc_sc_value=Decimal("17375.80"),
            quantity_by_unit={"YDS": Decimal("3050")},
        )

        self.assertEqual(result.final_decision, "selected")
        self.assertEqual(result.selected_candidate_id, "11-12")
        self.assertEqual(result.candidates[0].score_keys["workbook_value_sum"], "17375.8")

    def test_allocate_structured_ud_rows_reports_all_exact_value_candidates_in_priority_order(self) -> None:
        snapshot = _structured_snapshot(
            rows=[
                WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000 YDS", 3: "", 4: "", 5: "", 6: "1000"}),
                WorkbookRow(row_index=12, values={1: "LC-0043", 2: "500 YDS", 3: "", 4: "", 5: "", 6: "500"}),
                WorkbookRow(row_index=13, values={1: "LC-0043", 2: "700 YDS", 3: "", 4: "", 5: "", 6: "700"}),
                WorkbookRow(row_index=14, values={1: "LC-0043", 2: "800 YDS", 3: "", 4: "", 5: "", 6: "800"}),
            ]
        )

        result = allocate_structured_ud_rows(
            workbook_snapshot=snapshot,
            lc_sc_number="LC-0043",
            lc_sc_value=Decimal("1500"),
            quantity_by_unit={"YDS": Decimal("1500")},
        )

        self.assertEqual(result.final_decision, "selected")
        self.assertEqual(result.selected_candidate_id, "11-12")
        self.assertEqual(result.candidate_count, 2)
        self.assertEqual([candidate.candidate_id for candidate in result.candidates], ["11-12", "13-14"])
        self.assertEqual([candidate.selected for candidate in result.candidates], [True, False])

    def test_allocate_structured_ud_rows_uses_number_format_for_numeric_only_workbook_quantity(self) -> None:
        snapshot = _structured_snapshot(
            rows=[
                WorkbookRow(
                    row_index=475,
                    values={1: "1550260400113", 2: "26548.0", 3: "", 4: "-", 5: "-", 6: "100882.4"},
                    number_formats={2: '#,###.00 "Mtr"'},
                ),
            ]
        )

        result = allocate_structured_ud_rows(
            workbook_snapshot=snapshot,
            lc_sc_number="1550260400113",
            lc_sc_value=Decimal("100882.40"),
            quantity_by_unit={"MTR": Decimal("26548")},
        )

        self.assertEqual(result.final_decision, "selected")
        self.assertEqual(result.selected_candidate_id, "475")
        self.assertEqual(result.candidates[0].score_keys["workbook_quantity_by_unit"], "MTR:26548")

    def test_allocate_structured_ud_rows_hard_blocks_when_value_group_not_found(self) -> None:
        result = allocate_structured_ud_rows(
            workbook_snapshot=_structured_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000 YDS", 3: "", 4: "", 5: "", 6: "999"}),
                ]
            ),
            lc_sc_number="LC-0043",
            lc_sc_value=Decimal("1000"),
            quantity_by_unit={"YDS": Decimal("1100")},
        )

        self.assertEqual(result.final_decision, "hard_block")
        self.assertEqual(result.discrepancy_code, "ud_lc_value_match_unresolved")

    def test_allocate_structured_ud_rows_skips_earlier_oversized_family_row(self) -> None:
        result = allocate_structured_ud_rows(
            workbook_snapshot=_structured_snapshot(
                rows=[
                    WorkbookRow(row_index=232, values={1: "2159260400035", 2: "33350 YDS", 3: "", 4: "", 5: "", 6: "106711"}),
                    WorkbookRow(row_index=361, values={1: "2159260400035", 2: "16700 YDS", 3: "", 4: "", 5: "", 6: "43491"}),
                    WorkbookRow(row_index=452, values={1: "2159260400035", 2: "6000 YDS", 3: "", 4: "", 5: "", 6: "14700"}),
                ]
            ),
            lc_sc_number="2159260400035",
            lc_sc_value=Decimal("14700"),
            quantity_by_unit={"YDS": Decimal("6000")},
        )

        self.assertEqual(result.final_decision, "selected")
        self.assertEqual(result.selected_candidate_id, "452")

    def test_allocate_structured_ud_rows_can_select_non_prefix_exact_value_combination(self) -> None:
        result = allocate_structured_ud_rows(
            workbook_snapshot=_structured_snapshot(
                rows=[
                    WorkbookRow(row_index=308, values={1: "DPCBDA032674", 2: "10400 MTR", 3: "", 4: "", 5: "", 6: "38480"}),
                    WorkbookRow(row_index=523, values={1: "DPCBDA032674", 2: "21100 MTR", 3: "", 4: "", 5: "", 6: "82290"}),
                    WorkbookRow(row_index=560, values={1: "DPCBDA032674", 2: "8000 MTR", 3: "", 4: "", 5: "", 6: "29600"}),
                ]
            ),
            lc_sc_number="DPCBDA032674",
            lc_sc_value=Decimal("111890"),
            quantity_by_unit={"MTR": Decimal("29100")},
        )

        self.assertEqual(result.final_decision, "selected")
        self.assertEqual(result.selected_candidate_id, "523-560")

    def test_allocate_structured_ud_rows_handles_larger_family_with_exact_inner_combination(self) -> None:
        rows = [
            WorkbookRow(row_index=400 + index, values={1: "LC-9000", 2: f"{quantity} YDS", 3: "", 4: "", 5: "", 6: str(value)})
            for index, (quantity, value) in enumerate(
                [
                    (800, 1500),
                    (1200, 2200),
                    (600, 900),
                    (950, 1800),
                    (1100, 2050),
                    (700, 1200),
                    (1300, 2400),
                    (1000, 1750),
                    (1500, 2600),
                    (900, 1650),
                    (1400, 2550),
                    (650, 1100),
                ],
                start=1,
            )
        ]

        result = allocate_structured_ud_rows(
            workbook_snapshot=_structured_snapshot(rows=rows),
            lc_sc_number="LC-9000",
            lc_sc_value=Decimal("3500"),
            quantity_by_unit={"YDS": Decimal("1950")},
        )

        self.assertEqual(result.final_decision, "selected")
        self.assertEqual(result.selected_candidate_id, "407-412")

    def test_allocate_structured_ud_rows_reports_row_conflict_when_only_viable_value_group_is_claimed(self) -> None:
        result = allocate_structured_ud_rows(
            workbook_snapshot=_structured_snapshot(
                rows=[
                    WorkbookRow(
                        row_index=11,
                        values={1: "LC-0043", 2: "1000 YDS", 3: "BGMEA/DHK/UD/2026/5483/001", 4: "", 5: "", 6: "1000"},
                    ),
                    WorkbookRow(
                        row_index=12,
                        values={1: "LC-0043", 2: "500 YDS", 3: "", 4: "", 5: "", 6: "500"},
                    ),
                ]
            ),
            lc_sc_number="LC-0043",
            lc_sc_value=Decimal("1500"),
            quantity_by_unit={"YDS": Decimal("1500")},
        )

        self.assertEqual(result.final_decision, "hard_block")
        self.assertEqual(result.final_decision_reason, "target_row_conflict")
        self.assertEqual(result.discrepancy_code, "ud_target_row_conflict")
        self.assertEqual(result.selected_candidate_id, "11-12")

    def test_allocate_structured_ud_rows_bounds_dense_many_match_candidate_reports(self) -> None:
        rows = [
            WorkbookRow(row_index=11 + index, values={1: "LC-0043", 2: "100 YDS", 3: "", 4: "", 5: "", 6: "100"})
            for index in range(10)
        ]

        result = allocate_structured_ud_rows(
            workbook_snapshot=_structured_snapshot(rows=rows),
            lc_sc_number="LC-0043",
            lc_sc_value=Decimal("500"),
            quantity_by_unit={"YDS": Decimal("500")},
        )

        self.assertEqual(result.final_decision, "selected")
        self.assertEqual(result.selected_candidate_id, "11-12-13-14-15")
        self.assertEqual(result.candidate_count, 252)
        self.assertTrue(result.candidates_truncated)
        self.assertEqual(len(result.candidates), MAX_UD_SELECTION_REPORT_CANDIDATES)
        self.assertIn(
            "11-12-13-14-15",
            [candidate.candidate_id for candidate in result.candidates],
        )

    def test_allocate_structured_ud_rows_recognizes_already_recorded_ud(self) -> None:
        result = allocate_structured_ud_rows(
            workbook_snapshot=_structured_snapshot(
                rows=[
                    WorkbookRow(
                        row_index=11,
                        values={
                            1: "LC-0043",
                            2: "1000 YDS",
                            3: "BGMEA/DHK/UD/2026/5483/003",
                            4: "",
                            5: "",
                            6: "1000",
                            7: "2026-03-31T00:00:00",
                            8: "22/04/2026",
                        },
                    ),
                ]
            ),
            lc_sc_number="LC-0043",
            lc_sc_value=Decimal("1000"),
            quantity_by_unit={"YDS": Decimal("1000")},
            expected_shared_value="BGMEA/DHK/UD/2026/5483/003",
            expected_ud_date="2026-03-31",
        )

        self.assertEqual(result.final_decision, "already_recorded")
        self.assertEqual(result.final_decision_reason, "ud_already_recorded")
        self.assertEqual(result.selected_candidate_id, "11")

    def test_allocate_structured_ud_rows_hard_blocks_quantity_excess_below_threshold(self) -> None:
        result = allocate_structured_ud_rows(
            workbook_snapshot=_structured_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000 YDS", 3: "", 4: "", 5: "", 6: "1000"}),
                ]
            ),
            lc_sc_number="LC-0043",
            lc_sc_value=Decimal("1000"),
            quantity_by_unit={"YDS": Decimal("1049")},
        )

        self.assertEqual(result.final_decision, "hard_block")
        self.assertEqual(result.discrepancy_code, "ud_quantity_excess_below_threshold")


def _structured_snapshot(*, rows: list[WorkbookRow]) -> WorkbookSnapshot:
    return WorkbookSnapshot(
        sheet_name="Sheet1",
        headers=[
            WorkbookHeader(column_index=1, text="L/C & S/C No."),
            WorkbookHeader(column_index=2, text="Quantity of Fabrics (Yds/Mtr)"),
            WorkbookHeader(column_index=3, text="UD No. & IP No."),
            WorkbookHeader(column_index=4, text="L/C Amnd No."),
            WorkbookHeader(column_index=5, text="L/C Amnd Date"),
            WorkbookHeader(column_index=6, text="Amount"),
            WorkbookHeader(column_index=7, text="UD & IP Date"),
            WorkbookHeader(column_index=8, text="UD Recv. Date"),
        ],
        rows=rows,
    )


if __name__ == "__main__":
    unittest.main()
