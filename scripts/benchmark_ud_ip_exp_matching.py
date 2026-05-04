from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from decimal import Decimal
from math import comb
from statistics import mean, median
from time import perf_counter

from project.workbook import WorkbookHeader, WorkbookRow, WorkbookSnapshot
from project.workflows.ud_ip_exp import allocate_structured_ud_rows


MATCHING_HEADERS = [
    WorkbookHeader(column_index=1, text="L/C & S/C No."),
    WorkbookHeader(column_index=2, text="Quantity of Fabrics (Yds/Mtr)"),
    WorkbookHeader(column_index=3, text="UD No. & IP No."),
    WorkbookHeader(column_index=4, text="L/C Amnd No."),
    WorkbookHeader(column_index=5, text="L/C Amnd Date"),
    WorkbookHeader(column_index=6, text="Amount"),
    WorkbookHeader(column_index=7, text="UD & IP Date"),
    WorkbookHeader(column_index=8, text="UD Recv. Date"),
]


@dataclass(slots=True, frozen=True)
class Scenario:
    name: str
    workbook_snapshot: WorkbookSnapshot
    lc_sc_number: str
    lc_sc_value: Decimal
    quantity_by_unit: dict[str, Decimal]
    expected_final_decision: str
    expected_selected_candidate_id: str | None
    expected_candidate_count: int | None = None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark structured UD matcher scenarios in-process.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=250,
        help="Number of matcher calls per scenario. Defaults to 250.",
    )
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=[12, 24, 36, 48],
        help="Synthetic family sizes to benchmark. Defaults to 12 24 36 48.",
    )
    parser.add_argument(
        "--include-dense",
        action="store_true",
        help="Include dense many-exact-match scenarios that stress candidate enumeration.",
    )
    parser.add_argument(
        "--dense-sizes",
        type=int,
        nargs="+",
        default=[10, 12, 14, 16],
        help="Family sizes for dense many-match scenarios when --include-dense is enabled.",
    )
    parser.add_argument(
        "--dense-iterations",
        type=int,
        default=20,
        help="Iterations per dense scenario. Defaults to 20.",
    )
    args = parser.parse_args()

    benchmark_plan = build_benchmark_plan(
        sparse_sizes=args.sizes,
        sparse_iterations=args.iterations,
        include_dense=args.include_dense,
        dense_sizes=args.dense_sizes,
        dense_iterations=args.dense_iterations,
    )
    results = [
        benchmark_scenario(scenario, iterations=iterations)
        for scenario, iterations in benchmark_plan
    ]
    payload = {
        "sparse_iterations_per_scenario": args.iterations,
        "dense_iterations_per_scenario": args.dense_iterations if args.include_dense else 0,
        "scenario_count": len(results),
        "results": results,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_benchmark_plan(
    *,
    sparse_sizes: list[int],
    sparse_iterations: int,
    include_dense: bool,
    dense_sizes: list[int],
    dense_iterations: int,
) -> list[tuple[Scenario, int]]:
    benchmark_plan: list[tuple[Scenario, int]] = []
    for size in sparse_sizes:
        benchmark_plan.append((_build_sparse_unique_match_scenario(size=size), sparse_iterations))
        benchmark_plan.append((_build_sparse_conflict_match_scenario(size=size), sparse_iterations))
    if include_dense:
        for size in dense_sizes:
            benchmark_plan.append((_build_dense_many_matches_scenario(size=size), dense_iterations))
            benchmark_plan.append((_build_dense_conflict_many_matches_scenario(size=size), dense_iterations))
    return benchmark_plan


def benchmark_scenario(scenario: Scenario, *, iterations: int) -> dict[str, object]:
    durations_ms: list[float] = []
    last_result = None
    for _ in range(iterations):
        started = perf_counter()
        last_result = allocate_structured_ud_rows(
            workbook_snapshot=scenario.workbook_snapshot,
            lc_sc_number=scenario.lc_sc_number,
            lc_sc_value=scenario.lc_sc_value,
            quantity_by_unit=scenario.quantity_by_unit,
        )
        durations_ms.append((perf_counter() - started) * 1000)

    if last_result is None:
        raise AssertionError("Benchmark iterations did not execute.")
    if last_result.final_decision != scenario.expected_final_decision:
        raise AssertionError(
            f"{scenario.name}: expected final_decision {scenario.expected_final_decision!r}, "
            f"got {last_result.final_decision!r}."
        )
    if last_result.selected_candidate_id != scenario.expected_selected_candidate_id:
        raise AssertionError(
            f"{scenario.name}: expected selected_candidate_id {scenario.expected_selected_candidate_id!r}, "
            f"got {last_result.selected_candidate_id!r}."
        )
    if (
        scenario.expected_candidate_count is not None
        and last_result.candidate_count != scenario.expected_candidate_count
    ):
        raise AssertionError(
            f"{scenario.name}: expected candidate_count {scenario.expected_candidate_count}, "
            f"got {last_result.candidate_count}."
        )

    return {
        "scenario": scenario.name,
        "iterations": iterations,
        "row_count": len(scenario.workbook_snapshot.rows),
        "candidate_count": last_result.candidate_count,
        "final_decision": last_result.final_decision,
        "selected_candidate_id": last_result.selected_candidate_id,
        "mean_ms": round(mean(durations_ms), 4),
        "median_ms": round(median(durations_ms), 4),
        "min_ms": round(min(durations_ms), 4),
        "max_ms": round(max(durations_ms), 4),
    }


def _build_sparse_unique_match_scenario(*, size: int) -> Scenario:
    rows = _build_sparse_family_rows(size=size, claimed_match=False)
    return Scenario(
        name=f"sparse_unique_match_{size}",
        workbook_snapshot=_structured_snapshot(rows),
        lc_sc_number="LC-BENCH-001",
        lc_sc_value=Decimal("1500"),
        quantity_by_unit={"YDS": Decimal("1500")},
        expected_final_decision="selected",
        expected_selected_candidate_id="11-12",
    )


def _build_sparse_conflict_match_scenario(*, size: int) -> Scenario:
    rows = _build_sparse_family_rows(size=size, claimed_match=True)
    return Scenario(
        name=f"sparse_conflict_match_{size}",
        workbook_snapshot=_structured_snapshot(rows),
        lc_sc_number="LC-BENCH-001",
        lc_sc_value=Decimal("1500"),
        quantity_by_unit={"YDS": Decimal("1500")},
        expected_final_decision="hard_block",
        expected_selected_candidate_id="11-12",
    )


def _build_dense_many_matches_scenario(*, size: int) -> Scenario:
    if size % 2 != 0:
        raise ValueError("Dense many-match benchmark size must be even.")
    target_row_count = size // 2
    row_indexes = list(range(11, 11 + size))
    rows = [
        WorkbookRow(
            row_index=row_index,
            values={
                1: "LC-BENCH-001",
                2: "100 YDS",
                3: "",
                4: "",
                5: "",
                6: "100",
                7: "",
                8: "",
            },
        )
        for row_index in row_indexes
    ]
    return Scenario(
        name=f"dense_many_matches_{size}",
        workbook_snapshot=_structured_snapshot(rows),
        lc_sc_number="LC-BENCH-001",
        lc_sc_value=Decimal(str(target_row_count * 100)),
        quantity_by_unit={"YDS": Decimal(str(target_row_count * 100))},
        expected_final_decision="selected",
        expected_selected_candidate_id="-".join(str(row_index) for row_index in row_indexes[:target_row_count]),
        expected_candidate_count=comb(size, target_row_count),
    )


def _build_dense_conflict_many_matches_scenario(*, size: int) -> Scenario:
    if size % 2 != 0:
        raise ValueError("Dense conflict benchmark size must be even.")
    target_row_count = (size // 2) + 1
    claimed_row_count = size - (target_row_count - 1)
    row_indexes = list(range(11, 11 + size))
    rows = [
        WorkbookRow(
            row_index=row_index,
            values={
                1: "LC-BENCH-001",
                2: "100 YDS",
                3: "BGMEA/DHK/UD/2026/9999/001" if offset < claimed_row_count else "",
                4: "",
                5: "",
                6: "100",
                7: "",
                8: "",
            },
        )
        for offset, row_index in enumerate(row_indexes)
    ]
    return Scenario(
        name=f"dense_conflict_many_matches_{size}",
        workbook_snapshot=_structured_snapshot(rows),
        lc_sc_number="LC-BENCH-001",
        lc_sc_value=Decimal(str(target_row_count * 100)),
        quantity_by_unit={"YDS": Decimal(str(target_row_count * 100))},
        expected_final_decision="hard_block",
        expected_selected_candidate_id="-".join(str(row_index) for row_index in row_indexes[:target_row_count]),
        expected_candidate_count=comb(size, target_row_count),
    )


def _build_sparse_family_rows(*, size: int, claimed_match: bool) -> list[WorkbookRow]:
    if size < 4:
        raise ValueError("Benchmark family size must be at least 4.")

    base_rows = [
        WorkbookRow(
            row_index=11,
            values={
                1: "LC-BENCH-001",
                2: "1000 YDS",
                3: "BGMEA/DHK/UD/2026/9999/001" if claimed_match else "",
                4: "",
                5: "",
                6: "1000",
                7: "",
                8: "",
            },
        ),
        WorkbookRow(
            row_index=12,
            values={
                1: "LC-BENCH-001",
                2: "500 YDS",
                3: "",
                4: "",
                5: "",
                6: "500",
                7: "",
                8: "",
            },
        ),
    ]
    decoy_amounts = [
        ("700 YDS", "701"),
        ("800 YDS", "803"),
        ("900 YDS", "907"),
        ("1100 YDS", "1103"),
        ("1200 YDS", "1211"),
        ("600 YDS", "607"),
        ("1300 YDS", "1301"),
        ("400 YDS", "409"),
        ("1400 YDS", "1409"),
        ("300 YDS", "307"),
    ]
    row_index = 13
    while len(base_rows) < size:
        quantity_text, amount_text = decoy_amounts[(len(base_rows) - 2) % len(decoy_amounts)]
        base_rows.append(
            WorkbookRow(
                row_index=row_index,
                values={
                    1: "LC-BENCH-001",
                    2: quantity_text,
                    3: "",
                    4: "",
                    5: "",
                    6: amount_text,
                    7: "",
                    8: "",
                },
            )
        )
        row_index += 1
    return base_rows


def _structured_snapshot(rows: list[WorkbookRow]) -> WorkbookSnapshot:
    return WorkbookSnapshot(
        sheet_name="Benchmark",
        headers=list(MATCHING_HEADERS),
        rows=rows,
    )


if __name__ == "__main__":
    raise SystemExit(main())
