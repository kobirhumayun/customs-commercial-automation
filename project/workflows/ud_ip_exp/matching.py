from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Any

from project.workbook import WorkbookSnapshot, resolve_ud_ip_exp_header_mapping
from project.workflows.ud_ip_exp.payloads import normalize_quantity_unit

DEFAULT_UD_EXCESS_THRESHOLD = Decimal("50")


@dataclass(slots=True, frozen=True)
class UDCandidateRow:
    row_index: int
    lc_sc_number: str
    quantity: Decimal
    quantity_unit: str
    ud_ip_shared_value: str = ""
    lc_amnd_no: str = ""
    lc_amnd_date: str = ""
    optional_values: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", Decimal(str(self.quantity)))
        object.__setattr__(self, "quantity_unit", normalize_quantity_unit(self.quantity_unit))


@dataclass(slots=True, frozen=True)
class UDAllocationCandidate:
    candidate_id: str
    row_indexes: list[int]
    matched_quantities: list[str]
    quantity_sum: str
    ignored_excess_quantity: str
    score_keys: dict[str, Any]
    prewrite_blank_targets_count: int
    prewrite_nonblank_optional_count: int
    selected: bool = False
    rejection_reason: str | None = None


@dataclass(slots=True, frozen=True)
class UDAllocationResult:
    required_quantity: str
    quantity_unit: str
    candidate_count: int
    candidates: list[UDAllocationCandidate]
    final_decision: str
    final_decision_reason: str
    selected_candidate_id: str | None = None
    discrepancy_code: str | None = None


def collect_ud_candidate_rows(
    *,
    workbook_snapshot: WorkbookSnapshot,
    lc_sc_number: str,
    quantity_unit: str,
    header_mapping: dict[str, int] | None = None,
) -> list[UDCandidateRow]:
    mapping = header_mapping or resolve_ud_ip_exp_header_mapping(workbook_snapshot)
    if mapping is None:
        return []

    requested_unit = normalize_quantity_unit(quantity_unit)
    expected_lc_sc = _normalize_match_text(lc_sc_number)
    rows: list[UDCandidateRow] = []
    for workbook_row in sorted(workbook_snapshot.rows, key=lambda row: row.row_index):
        row_lc_sc_number = workbook_row.values.get(mapping["lc_sc_no"], "")
        if _normalize_match_text(row_lc_sc_number) != expected_lc_sc:
            continue

        raw_quantity = workbook_row.values.get(mapping["quantity_fabrics"], "")
        parsed_quantity = _parse_quantity(raw_quantity)
        if parsed_quantity is None:
            continue
        parsed_unit = _parse_quantity_unit(raw_quantity) or requested_unit
        if parsed_unit != requested_unit:
            continue

        rows.append(
            UDCandidateRow(
                row_index=workbook_row.row_index,
                lc_sc_number=row_lc_sc_number,
                quantity=parsed_quantity,
                quantity_unit=parsed_unit,
                ud_ip_shared_value=workbook_row.values.get(mapping["ud_ip_shared"], ""),
                lc_amnd_no=workbook_row.values.get(mapping["lc_amnd_no"], ""),
                lc_amnd_date=workbook_row.values.get(mapping["lc_amnd_date"], ""),
                optional_values={
                    "lc_amnd_no": workbook_row.values.get(mapping["lc_amnd_no"], ""),
                    "lc_amnd_date": workbook_row.values.get(mapping["lc_amnd_date"], ""),
                },
            )
        )
    return rows


def allocate_ud_rows(
    *,
    required_quantity: Decimal | int | str,
    quantity_unit: str,
    candidate_rows: list[UDCandidateRow],
    excess_threshold: Decimal = DEFAULT_UD_EXCESS_THRESHOLD,
) -> UDAllocationResult:
    required = Decimal(str(required_quantity))
    unit = normalize_quantity_unit(quantity_unit)
    compatible_rows = [
        row
        for row in sorted(candidate_rows, key=lambda item: item.row_index)
        if row.quantity_unit == unit and row.quantity > 0
    ]
    exact_candidates = _build_allocation_candidates(
        required_quantity=required,
        candidate_rows=compatible_rows,
        allowed_excess=lambda excess: excess == 0,
    )
    candidate_scope = exact_candidates
    final_reason = "selected_exact_quantity"

    if not candidate_scope:
        threshold_candidates = _build_allocation_candidates(
            required_quantity=required,
            candidate_rows=compatible_rows,
            allowed_excess=lambda excess: excess >= excess_threshold,
        )
        if threshold_candidates:
            minimum_excess = min(
                Decimal(candidate.ignored_excess_quantity)
                for candidate in threshold_candidates
            )
            candidate_scope = [
                candidate
                for candidate in threshold_candidates
                if Decimal(candidate.ignored_excess_quantity) == minimum_excess
            ]
            final_reason = "selected_with_ignored_excess_at_or_above_threshold"

    if not candidate_scope:
        smallest_positive_excess = _smallest_positive_excess(required, compatible_rows)
        if smallest_positive_excess is not None and smallest_positive_excess < excess_threshold:
            return UDAllocationResult(
                required_quantity=_format_decimal(required),
                quantity_unit=unit,
                candidate_count=0,
                candidates=[],
                final_decision="hard_block",
                final_decision_reason="quantity_excess_below_threshold",
            )
        return UDAllocationResult(
            required_quantity=_format_decimal(required),
            quantity_unit=unit,
            candidate_count=0,
            candidates=[],
            final_decision="hard_block",
            final_decision_reason="no_valid_ud_quantity_combination",
        )

    sorted_candidates = sorted(candidate_scope, key=_candidate_sort_key)
    best_candidate = sorted_candidates[0]
    tied_candidates = [
        candidate
        for candidate in sorted_candidates
        if _candidate_sort_key(candidate) == _candidate_sort_key(best_candidate)
    ]
    if len(tied_candidates) > 1:
        return UDAllocationResult(
            required_quantity=_format_decimal(required),
            quantity_unit=unit,
            candidate_count=len(sorted_candidates),
            candidates=[
                replace(candidate, selected=False, rejection_reason="tied_after_full_tiebreak")
                for candidate in sorted_candidates
            ],
            final_decision="hard_block",
            final_decision_reason="ud_candidate_tie_after_full_tiebreak",
            discrepancy_code="ud_candidate_tie_after_full_tiebreak",
        )

    selected_candidates = [
        replace(
            candidate,
            selected=candidate.candidate_id == best_candidate.candidate_id,
            rejection_reason=None
            if candidate.candidate_id == best_candidate.candidate_id
            else "lower_priority_score",
        )
        for candidate in sorted_candidates
    ]
    return UDAllocationResult(
        required_quantity=_format_decimal(required),
        quantity_unit=unit,
        candidate_count=len(selected_candidates),
        candidates=selected_candidates,
        final_decision="selected",
        final_decision_reason=final_reason,
        selected_candidate_id=best_candidate.candidate_id,
    )


def _build_allocation_candidates(
    *,
    required_quantity: Decimal,
    candidate_rows: list[UDCandidateRow],
    allowed_excess,
) -> list[UDAllocationCandidate]:
    candidates: list[UDAllocationCandidate] = []
    for size in range(1, len(candidate_rows) + 1):
        for row_group in combinations(candidate_rows, size):
            quantity_sum = sum((row.quantity for row in row_group), Decimal("0"))
            excess = required_quantity - quantity_sum
            if excess < 0 or not allowed_excess(excess):
                continue
            row_indexes = sorted(row.row_index for row in row_group)
            candidate_id = "-".join(str(row_index) for row_index in row_indexes)
            amendment_recency_key = [
                _amendment_recency_key(row)
                for row in sorted(row_group, key=lambda item: item.row_index)
            ]
            blank_count = sum(1 for row in row_group if not row.ud_ip_shared_value.strip())
            nonblank_optional_count = sum(
                1
                for row in row_group
                for value in row.optional_values.values()
                if str(value).strip()
            )
            candidates.append(
                UDAllocationCandidate(
                    candidate_id=candidate_id,
                    row_indexes=row_indexes,
                    matched_quantities=[_format_decimal(row.quantity) for row in row_group],
                    quantity_sum=_format_decimal(quantity_sum),
                    ignored_excess_quantity=_format_decimal(excess),
                    score_keys={
                        "row_index_key": row_indexes,
                        "amendment_recency_key": amendment_recency_key,
                        "blank_field_priority_key": {
                            "blank_target_count_desc": -blank_count,
                            "nonblank_optional_count_asc": nonblank_optional_count,
                        },
                        "stable_candidate_id_key": candidate_id,
                    },
                    prewrite_blank_targets_count=blank_count,
                    prewrite_nonblank_optional_count=nonblank_optional_count,
                )
            )
    return candidates


def _candidate_sort_key(candidate: UDAllocationCandidate) -> tuple:
    blank_key = candidate.score_keys["blank_field_priority_key"]
    return (
        tuple(candidate.score_keys["row_index_key"]),
        tuple(tuple(item) for item in candidate.score_keys["amendment_recency_key"]),
        blank_key["blank_target_count_desc"],
        blank_key["nonblank_optional_count_asc"],
        candidate.score_keys["stable_candidate_id_key"],
    )


def _smallest_positive_excess(
    required_quantity: Decimal,
    candidate_rows: list[UDCandidateRow],
) -> Decimal | None:
    excess_values: list[Decimal] = []
    for size in range(1, len(candidate_rows) + 1):
        for row_group in combinations(candidate_rows, size):
            quantity_sum = sum((row.quantity for row in row_group), Decimal("0"))
            excess = required_quantity - quantity_sum
            if excess > 0:
                excess_values.append(excess)
    if not excess_values:
        return None
    return min(excess_values)


def _amendment_recency_key(row: UDCandidateRow) -> tuple[str, int]:
    normalized_date = _normalize_amendment_date(row.lc_amnd_date)
    amendment_number = _normalize_amendment_number(row.lc_amnd_no)
    return (normalized_date, amendment_number)


def _normalize_amendment_date(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return "0001-01-01"
    try:
        return date.fromisoformat(normalized).isoformat()
    except ValueError:
        return normalized


def _normalize_amendment_number(value: str) -> int:
    normalized = value.strip()
    if not normalized:
        return 0
    digits = "".join(character for character in normalized if character.isdigit())
    return int(digits) if digits else 0


def _normalize_match_text(value: str) -> str:
    return " ".join(value.strip().upper().split())


def _parse_quantity(value: str) -> Decimal | None:
    normalized = value.strip().replace(",", "")
    token = ""
    for character in normalized:
        if character.isdigit() or character == ".":
            token += character
        elif token:
            break
    if not token:
        return None
    try:
        return Decimal(token)
    except InvalidOperation:
        return None


def _parse_quantity_unit(value: str) -> str | None:
    normalized = value.upper()
    for token in ("YDS", "YD", "YARD", "YARDS", "MTR", "METER", "METERS", "METRE", "METRES"):
        if token in normalized:
            return normalize_quantity_unit(token)
    return None


def _format_decimal(value: Decimal) -> str:
    normalized = format(value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"
