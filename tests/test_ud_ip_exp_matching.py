from __future__ import annotations

from decimal import Decimal
import unittest

from project.workbook import WorkbookHeader, WorkbookRow, WorkbookSnapshot, resolve_ud_ip_exp_header_mapping
from project.workflows.ud_ip_exp import (
    UDCandidateRow,
    allocate_structured_ud_rows,
    allocate_ud_rows,
    collect_ud_candidate_rows,
)


class UDIPEXPMatchingTests(unittest.TestCase):
    def test_collect_ud_candidate_rows_matches_family_quantity_and_unit(self) -> None:
        snapshot = _snapshot(
            rows=[
                WorkbookRow(row_index=3, values={1: "LC-0043", 2: "1,000 YDS", 3: "", 4: "1", 5: "2026-01-02"}),
                WorkbookRow(row_index=4, values={1: "LC-0044", 2: "1,000 YDS", 3: "", 4: "1", 5: "2026-01-02"}),
                WorkbookRow(row_index=5, values={1: "LC-0043", 2: "500 MTR", 3: "", 4: "1", 5: "2026-01-02"}),
            ]
        )

        rows = collect_ud_candidate_rows(
            workbook_snapshot=snapshot,
            lc_sc_number="lc-0043",
            quantity_unit="YDS",
        )

        self.assertEqual([row.row_index for row in rows], [3])
        self.assertEqual(rows[0].quantity, Decimal("1000"))

    def test_allocate_ud_exact_quantity_maps_to_one_row(self) -> None:
        result = allocate_ud_rows(
            required_quantity=Decimal("1000"),
            quantity_unit="YDS",
            candidate_rows=[
                UDCandidateRow(row_index=11, lc_sc_number="LC-1", quantity=Decimal("1000"), quantity_unit="YDS"),
                UDCandidateRow(row_index=12, lc_sc_number="LC-1", quantity=Decimal("500"), quantity_unit="YDS"),
            ],
        )

        self.assertEqual(result.final_decision, "selected")
        self.assertEqual(result.selected_candidate_id, "11")

    def test_allocate_ud_exact_quantity_maps_to_non_sequential_rows(self) -> None:
        result = allocate_ud_rows(
            required_quantity=Decimal("3000"),
            quantity_unit="YDS",
            candidate_rows=[
                UDCandidateRow(row_index=11, lc_sc_number="LC-1", quantity=Decimal("1000"), quantity_unit="YDS"),
                UDCandidateRow(row_index=14, lc_sc_number="LC-1", quantity=Decimal("500"), quantity_unit="YDS"),
                UDCandidateRow(row_index=19, lc_sc_number="LC-1", quantity=Decimal("2000"), quantity_unit="YDS"),
            ],
        )

        self.assertEqual(result.final_decision, "selected")
        self.assertEqual(result.selected_candidate_id, "11-19")

    def test_allocate_ud_uses_multiset_quantities_and_lowest_row_index_sequence(self) -> None:
        result = allocate_ud_rows(
            required_quantity=Decimal("3000"),
            quantity_unit="YDS",
            candidate_rows=[
                UDCandidateRow(
                    row_index=11,
                    lc_sc_number="LC-1",
                    quantity=Decimal("1000"),
                    quantity_unit="YDS",
                    lc_amnd_no="1",
                    lc_amnd_date="2026-01-02",
                ),
                UDCandidateRow(
                    row_index=14,
                    lc_sc_number="LC-1",
                    quantity=Decimal("1000"),
                    quantity_unit="YDS",
                    lc_amnd_no="1",
                    lc_amnd_date="2026-01-02",
                ),
                UDCandidateRow(
                    row_index=19,
                    lc_sc_number="LC-1",
                    quantity=Decimal("2000"),
                    quantity_unit="YDS",
                    lc_amnd_no="2",
                    lc_amnd_date="2026-02-10",
                ),
                UDCandidateRow(
                    row_index=27,
                    lc_sc_number="LC-1",
                    quantity=Decimal("2000"),
                    quantity_unit="YDS",
                    lc_amnd_no="2",
                    lc_amnd_date="2026-02-10",
                ),
            ],
        )

        self.assertEqual(result.candidate_count, 4)
        self.assertEqual(result.selected_candidate_id, "11-19")
        selected = [candidate for candidate in result.candidates if candidate.selected]
        self.assertEqual(selected[0].row_indexes, [11, 19])

    def test_allocate_ud_ignores_excess_at_threshold_when_no_exact_match_exists(self) -> None:
        result = allocate_ud_rows(
            required_quantity=Decimal("1000"),
            quantity_unit="YDS",
            candidate_rows=[
                UDCandidateRow(row_index=11, lc_sc_number="LC-1", quantity=Decimal("950"), quantity_unit="YDS"),
            ],
        )

        self.assertEqual(result.final_decision, "selected")
        self.assertEqual(result.selected_candidate_id, "11")
        self.assertEqual(result.candidates[0].ignored_excess_quantity, "50")

    def test_allocate_ud_hard_blocks_excess_below_threshold(self) -> None:
        result = allocate_ud_rows(
            required_quantity=Decimal("1000"),
            quantity_unit="YDS",
            candidate_rows=[
                UDCandidateRow(row_index=11, lc_sc_number="LC-1", quantity=Decimal("951"), quantity_unit="YDS"),
            ],
        )

        self.assertEqual(result.final_decision, "hard_block")
        self.assertEqual(result.final_decision_reason, "quantity_excess_below_threshold")
        self.assertIsNone(result.selected_candidate_id)

    def test_allocate_ud_hard_blocks_full_tiebreak_tie(self) -> None:
        result = allocate_ud_rows(
            required_quantity=Decimal("1000"),
            quantity_unit="YDS",
            candidate_rows=[
                UDCandidateRow(row_index=11, lc_sc_number="LC-1", quantity=Decimal("1000"), quantity_unit="YDS"),
                UDCandidateRow(row_index=11, lc_sc_number="LC-1", quantity=Decimal("1000"), quantity_unit="YDS"),
            ],
        )

        self.assertEqual(result.final_decision, "hard_block")
        self.assertEqual(result.discrepancy_code, "ud_candidate_tie_after_full_tiebreak")

    def test_allocate_ud_rows_recognizes_already_recorded_ud(self) -> None:
        result = allocate_ud_rows(
            required_quantity=Decimal("1000"),
            quantity_unit="YDS",
            expected_shared_value="BGMEA/DHK/UD/2026/5483/003",
            candidate_rows=[
                UDCandidateRow(
                    row_index=11,
                    lc_sc_number="LC-1",
                    quantity=Decimal("1000"),
                    quantity_unit="YDS",
                    ud_ip_shared_value="BGMEA/DHK/UD/2026/5483/003",
                ),
                UDCandidateRow(
                    row_index=12,
                    lc_sc_number="LC-1",
                    quantity=Decimal("1000"),
                    quantity_unit="YDS",
                ),
            ],
        )

        self.assertEqual(result.final_decision, "already_recorded")
        self.assertEqual(result.final_decision_reason, "ud_already_recorded")
        self.assertEqual(result.selected_candidate_id, "11")

    def test_allocate_ud_rows_prefers_blank_rows_over_conflicting_occupied_rows(self) -> None:
        result = allocate_ud_rows(
            required_quantity=Decimal("1000"),
            quantity_unit="YDS",
            expected_shared_value="BGMEA/DHK/UD/2026/5483/004",
            candidate_rows=[
                UDCandidateRow(
                    row_index=11,
                    lc_sc_number="LC-1",
                    quantity=Decimal("1000"),
                    quantity_unit="YDS",
                    ud_ip_shared_value="BGMEA/DHK/UD/2026/5483/003",
                ),
                UDCandidateRow(
                    row_index=12,
                    lc_sc_number="LC-1",
                    quantity=Decimal("1000"),
                    quantity_unit="YDS",
                ),
            ],
        )

        self.assertEqual(result.final_decision, "selected")
        self.assertEqual(result.selected_candidate_id, "12")

    def test_allocate_ud_rows_reports_row_conflict_when_only_occupied_match_exists(self) -> None:
        result = allocate_ud_rows(
            required_quantity=Decimal("1000"),
            quantity_unit="YDS",
            expected_shared_value="BGMEA/DHK/UD/2026/5483/004",
            candidate_rows=[
                UDCandidateRow(
                    row_index=11,
                    lc_sc_number="LC-1",
                    quantity=Decimal("1000"),
                    quantity_unit="YDS",
                    ud_ip_shared_value="BGMEA/DHK/UD/2026/5483/003",
                ),
            ],
        )

        self.assertEqual(result.final_decision, "hard_block")
        self.assertEqual(result.final_decision_reason, "target_row_conflict")
        self.assertEqual(result.discrepancy_code, "ud_target_row_conflict")

    def test_allocate_structured_ud_rows_selects_contiguous_value_group_then_checks_quantity(self) -> None:
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


def _snapshot(*, rows: list[WorkbookRow]) -> WorkbookSnapshot:
    snapshot = WorkbookSnapshot(
        sheet_name="Sheet1",
        headers=[
            WorkbookHeader(column_index=1, text="L/C & S/C No."),
            WorkbookHeader(column_index=2, text="Quantity of Fabrics (Yds/Mtr)"),
            WorkbookHeader(column_index=3, text="UD No. & IP No."),
            WorkbookHeader(column_index=4, text="L/C Amnd No."),
            WorkbookHeader(column_index=5, text="L/C Amnd Date"),
        ],
        rows=rows,
    )
    self_mapping = resolve_ud_ip_exp_header_mapping(snapshot)
    if self_mapping is None:
        raise AssertionError("UD/IP/EXP header fixture should resolve")
    return snapshot


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
