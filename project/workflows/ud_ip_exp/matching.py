from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
import heapq
from typing import Any

from project.erp.normalization import normalize_lc_sc_date
from project.workbook import WorkbookSnapshot, resolve_ud_ip_exp_header_mapping
from project.workflows.ud_ip_exp.payloads import normalize_quantity_unit

DEFAULT_UD_EXCESS_THRESHOLD = Decimal("50")
DEFAULT_UD_VALUE_TOLERANCE = Decimal("0.01")
MTR_QUANTITY_NUMBER_FORMAT = '#,###.00 "Mtr"'
MAX_UD_SELECTION_REPORT_CANDIDATES = 200


@dataclass(slots=True, frozen=True)
class UDCandidateRow:
    row_index: int
    lc_sc_number: str
    quantity: Decimal
    quantity_unit: str
    export_amount: Decimal | None = None
    ud_ip_shared_value: str = ""
    lc_amnd_no: str = ""
    lc_amnd_date: str = ""

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
    candidates_truncated: bool = False


@dataclass(slots=True)
class _BoundedStructuredCandidateState:
    kept_candidates: list[tuple["_ReverseCandidateKey", tuple, UDAllocationCandidate, str | None]]
    total_candidate_count: int = 0
    best_viable_candidate: tuple[tuple, UDAllocationCandidate] | None = None


@dataclass(slots=True, frozen=True)
class _ReverseCandidateKey:
    value: tuple

    def __lt__(self, other: "_ReverseCandidateKey") -> bool:
        return self.value > other.value


def allocate_structured_ud_rows(
    *,
    workbook_snapshot: WorkbookSnapshot,
    lc_sc_number: str,
    lc_sc_value: Decimal | int | str,
    quantity_by_unit: dict[str, Decimal | int | str],
    header_mapping: dict[str, int] | None = None,
    excluded_row_indexes: set[int] | None = None,
    expected_shared_value: str | None = None,
    expected_ud_date: str | None = None,
    value_tolerance: Decimal = DEFAULT_UD_VALUE_TOLERANCE,
    excess_threshold: Decimal = DEFAULT_UD_EXCESS_THRESHOLD,
) -> UDAllocationResult:
    mapping = header_mapping or resolve_ud_ip_exp_header_mapping(workbook_snapshot)
    if mapping is None or "export_amount" not in mapping:
        return UDAllocationResult(
            required_quantity="",
            quantity_unit="",
            candidate_count=0,
            candidates=[],
            final_decision="hard_block",
            final_decision_reason="workbook_header_mapping_invalid",
            discrepancy_code="workbook_header_mapping_invalid",
        )

    target_value = Decimal(str(lc_sc_value))
    expected_lc_sc = _normalize_match_text(lc_sc_number)
    excluded = set(excluded_row_indexes or set())
    normalized_ud_quantities = {
        normalize_quantity_unit(unit): Decimal(str(amount))
        for unit, amount in quantity_by_unit.items()
    }
    all_family_rows = [
        row
        for row in sorted(workbook_snapshot.rows, key=lambda item: item.row_index)
        if _normalize_match_text(row.values.get(mapping["lc_sc_no"], "")) == expected_lc_sc
    ]
    already_recorded_result = _allocate_already_recorded_structured_rows(
        rows=all_family_rows,
        mapping=mapping,
        target_value=target_value,
        quantity_by_unit=quantity_by_unit,
        expected_shared_value=expected_shared_value,
        expected_ud_date=expected_ud_date,
        value_tolerance=value_tolerance,
        excess_threshold=excess_threshold,
    )
    if already_recorded_result is not None:
        return already_recorded_result

    eligible_rows = [
        row
        for row in all_family_rows
        if not row.values.get(mapping["ud_ip_shared"], "").strip()
        and row.row_index not in excluded
    ]
    exact_value_groups = _select_structured_value_groups(
        workbook_rows=eligible_rows,
        mapping=mapping,
        target_value=target_value,
        value_tolerance=value_tolerance,
    )
    if not exact_value_groups:
        conflict_result = _allocate_conflicting_structured_rows(
            rows=all_family_rows,
            mapping=mapping,
            target_value=target_value,
            quantity_by_unit=normalized_ud_quantities,
            excluded_row_indexes=excluded,
            value_tolerance=value_tolerance,
            excess_threshold=excess_threshold,
        )
        if conflict_result is not None:
            return conflict_result
        return UDAllocationResult(
            required_quantity="",
            quantity_unit="",
            candidate_count=0,
            candidates=[],
            final_decision="hard_block",
            final_decision_reason="ud_lc_value_match_unresolved",
            discrepancy_code="ud_lc_value_match_unresolved",
        )

    candidate_state = _empty_bounded_structured_candidate_state()
    for selected_rows in exact_value_groups:
        workbook_value_sum = sum(
            (row.export_amount or Decimal("0") for row in selected_rows),
            Decimal("0"),
        )
        quantity_totals = _quantity_totals_by_unit(selected_rows)
        quantity_error = (
            "ud_quantity_below_workbook"
            if not quantity_totals
            else _structured_quantity_error(
                workbook_quantities=quantity_totals,
                ud_quantities=normalized_ud_quantities,
                excess_threshold=excess_threshold,
            )
        )
        _record_bounded_structured_candidate(
            state=candidate_state,
            candidate=_structured_candidate(
                selected_rows=selected_rows,
                workbook_value_sum=workbook_value_sum,
                lc_sc_value=target_value,
                workbook_quantities=quantity_totals,
                ud_quantities=normalized_ud_quantities,
                selected=False,
                rejection_reason=quantity_error,
            ),
            quantity_error=quantity_error,
        )

    if candidate_state.best_viable_candidate is not None:
        selected_candidates, best_candidate, candidates_truncated = _finalize_structured_selected_candidates(
            state=candidate_state,
        )
        return UDAllocationResult(
            required_quantity=_format_quantity_map(normalized_ud_quantities),
            quantity_unit="MULTI",
            candidate_count=candidate_state.total_candidate_count,
            candidates=selected_candidates,
            final_decision="selected",
            final_decision_reason="selected_structured_lc_value_and_quantity",
            selected_candidate_id=best_candidate.candidate_id if best_candidate is not None else None,
            candidates_truncated=candidates_truncated,
        )

    final_candidates, primary_candidate, primary_error, candidates_truncated = _finalize_structured_hard_block_candidates(
        state=candidate_state,
    )
    code = (
        "ud_quantity_below_workbook"
        if primary_error == "ud_quantity_below_workbook"
        else "ud_quantity_excess_below_threshold"
    )
    return UDAllocationResult(
        required_quantity=_format_quantity_map(normalized_ud_quantities),
        quantity_unit="MULTI",
        candidate_count=candidate_state.total_candidate_count,
        candidates=[candidate for candidate, _quantity_error in final_candidates],
        final_decision="hard_block",
        final_decision_reason=primary_error or "ud_quantity_below_workbook",
        discrepancy_code=code,
        candidates_truncated=candidates_truncated,
    )


def _allocate_conflicting_structured_rows(
    *,
    rows: list,
    mapping: dict[str, int],
    target_value: Decimal,
    quantity_by_unit: dict[str, Decimal],
    excluded_row_indexes: set[int],
    value_tolerance: Decimal,
    excess_threshold: Decimal,
) -> UDAllocationResult | None:
    exact_value_groups = _select_structured_value_groups(
        workbook_rows=rows,
        mapping=mapping,
        target_value=target_value,
        value_tolerance=value_tolerance,
    )
    if not exact_value_groups:
        return None

    candidate_state = _empty_bounded_structured_candidate_state()
    for selected_rows in exact_value_groups:
        claimed_rows = [
            row
            for row in selected_rows
            if row.row_index in excluded_row_indexes or row.ud_ip_shared_value.strip()
        ]
        if not claimed_rows:
            continue
        quantity_totals = _quantity_totals_by_unit(selected_rows)
        if not quantity_totals:
            continue
        quantity_error = _structured_quantity_error(
            workbook_quantities=quantity_totals,
            ud_quantities=quantity_by_unit,
            excess_threshold=excess_threshold,
        )
        if quantity_error is not None:
            continue
        workbook_value_sum = sum(
            (row.export_amount or Decimal("0") for row in selected_rows),
            Decimal("0"),
        )
        _record_bounded_structured_candidate(
            state=candidate_state,
            candidate=_structured_candidate(
                selected_rows=selected_rows,
                workbook_value_sum=workbook_value_sum,
                lc_sc_value=target_value,
                workbook_quantities=quantity_totals,
                ud_quantities=quantity_by_unit,
                selected=False,
                rejection_reason="target_row_conflict",
            ),
            quantity_error="target_row_conflict",
        )

    if candidate_state.total_candidate_count == 0:
        return None

    final_candidates, best_candidate, _primary_error, candidates_truncated = _finalize_structured_hard_block_candidates(
        state=candidate_state,
    )
    return UDAllocationResult(
        required_quantity=_format_quantity_map(quantity_by_unit),
        quantity_unit="MULTI",
        candidate_count=candidate_state.total_candidate_count,
        candidates=[
            replace(
                candidate,
                selected=False,
                rejection_reason=(
                    "target_row_conflict"
                    if best_candidate is not None and candidate.candidate_id == best_candidate.candidate_id
                    else "lower_priority_score"
                ),
            )
            for candidate, _quantity_error in final_candidates
        ],
        final_decision="hard_block",
        final_decision_reason="target_row_conflict",
        selected_candidate_id=best_candidate.candidate_id if best_candidate is not None else None,
        discrepancy_code="ud_target_row_conflict",
        candidates_truncated=candidates_truncated,
    )


def _allocate_already_recorded_structured_rows(
    *,
    rows: list,
    mapping: dict[str, int],
    target_value: Decimal,
    quantity_by_unit: dict[str, Decimal | int | str],
    expected_shared_value: str | None,
    expected_ud_date: str | None,
    value_tolerance: Decimal,
    excess_threshold: Decimal,
) -> UDAllocationResult | None:
    if expected_shared_value is None or "ud_ip_date" not in mapping:
        return None
    expected_ud_date_key = normalize_lc_sc_date(expected_ud_date or "")
    if expected_ud_date_key is None:
        return None

    matching_rows = [
        workbook_row
        for workbook_row in rows
        if _shared_value_matches(
            workbook_row.values.get(mapping["ud_ip_shared"], ""),
            expected_shared_value,
        )
        and normalize_lc_sc_date(workbook_row.values.get(mapping["ud_ip_date"], "")) == expected_ud_date_key
    ]
    exact_value_groups = _select_structured_value_groups(
        workbook_rows=matching_rows,
        mapping=mapping,
        target_value=target_value,
        value_tolerance=value_tolerance,
    )
    if not exact_value_groups:
        return None
    normalized_ud_quantities = {
        normalize_quantity_unit(unit): Decimal(str(amount))
        for unit, amount in quantity_by_unit.items()
    }
    candidate_state = _empty_bounded_structured_candidate_state()
    for selected_rows in exact_value_groups:
        quantity_totals = _quantity_totals_by_unit(selected_rows)
        if not quantity_totals:
            continue
        quantity_error = _structured_quantity_error(
            workbook_quantities=quantity_totals,
            ud_quantities=normalized_ud_quantities,
            excess_threshold=excess_threshold,
        )
        if quantity_error is not None:
            continue
        workbook_value_sum = sum(
            (row.export_amount or Decimal("0") for row in selected_rows),
            Decimal("0"),
        )
        _record_bounded_structured_candidate(
            state=candidate_state,
            candidate=_structured_candidate(
                selected_rows=selected_rows,
                workbook_value_sum=workbook_value_sum,
                lc_sc_value=target_value,
                workbook_quantities=quantity_totals,
                ud_quantities=normalized_ud_quantities,
                selected=True,
                rejection_reason=None,
            ),
            quantity_error=None,
        )
    if candidate_state.best_viable_candidate is None:
        return None

    candidates, candidate, candidates_truncated = _finalize_structured_selected_candidates(
        state=candidate_state,
    )
    return UDAllocationResult(
        required_quantity=_format_quantity_map(normalized_ud_quantities),
        quantity_unit="MULTI",
        candidate_count=candidate_state.total_candidate_count,
        candidates=candidates,
        final_decision="already_recorded",
        final_decision_reason="ud_already_recorded",
        selected_candidate_id=candidate.candidate_id if candidate is not None else None,
        candidates_truncated=candidates_truncated,
    )


def _select_structured_value_groups(
    *,
    workbook_rows: list,
    mapping: dict[str, int],
    target_value: Decimal,
    value_tolerance: Decimal,
) -> list[list[UDCandidateRow]]:
    candidate_rows: list[UDCandidateRow] = []
    for workbook_row in workbook_rows:
        candidate_row = _build_structured_candidate_row(workbook_row, mapping)
        if candidate_row is None:
            continue
        if candidate_row.export_amount is None:
            continue
        if candidate_row.export_amount > target_value + value_tolerance:
            continue
        candidate_rows.append(candidate_row)

    return _search_structured_value_groups(
        candidate_rows=candidate_rows,
        target_value=target_value,
        value_tolerance=value_tolerance,
    )


def _search_structured_value_groups(
    *,
    candidate_rows: list[UDCandidateRow],
    target_value: Decimal,
    value_tolerance: Decimal,
) -> list[list[UDCandidateRow]]:
    if not candidate_rows:
        return []

    tolerance_minor_units = _decimal_to_minor_units(value_tolerance)
    target_minor_units = _decimal_to_minor_units(target_value)
    amounts = [
        _decimal_to_minor_units(row.export_amount or Decimal("0"))
        for row in candidate_rows
    ]
    suffix_amounts = [0] * (len(amounts) + 1)
    for index in range(len(amounts) - 1, -1, -1):
        suffix_amounts[index] = suffix_amounts[index + 1] + amounts[index]

    memo: dict[tuple[int, int], bool] = {}

    def _can_match(index: int, remaining_minor_units: int) -> bool:
        if abs(remaining_minor_units) <= tolerance_minor_units:
            return True
        if index >= len(candidate_rows):
            return False
        if remaining_minor_units < -tolerance_minor_units:
            return False
        if remaining_minor_units > suffix_amounts[index] + tolerance_minor_units:
            return False
        key = (index, remaining_minor_units)
        cached = memo.get(key)
        if cached is not None:
            return cached
        include_matches = _can_match(index + 1, remaining_minor_units - amounts[index])
        if include_matches:
            memo[key] = True
            return True
        exclude_matches = _can_match(index + 1, remaining_minor_units)
        memo[key] = exclude_matches
        return exclude_matches

    matching_groups: list[list[UDCandidateRow]] = []

    def _collect(
        index: int,
        remaining_minor_units: int,
        selected_rows: list[UDCandidateRow],
    ) -> None:
        if abs(remaining_minor_units) <= tolerance_minor_units:
            matching_groups.append(list(selected_rows))
            return
        if index >= len(candidate_rows):
            return
        if remaining_minor_units < -tolerance_minor_units:
            return
        if remaining_minor_units > suffix_amounts[index] + tolerance_minor_units:
            return
        if memo.get((index, remaining_minor_units)) is False:
            return

        next_remaining = remaining_minor_units - amounts[index]
        if _can_match(index + 1, next_remaining):
            selected_rows.append(candidate_rows[index])
            _collect(index + 1, next_remaining, selected_rows)
            selected_rows.pop()
        if _can_match(index + 1, remaining_minor_units):
            _collect(index + 1, remaining_minor_units, selected_rows)

    if _can_match(0, target_minor_units):
        _collect(0, target_minor_units, [])
    return matching_groups


def _empty_bounded_structured_candidate_state() -> _BoundedStructuredCandidateState:
    return _BoundedStructuredCandidateState(kept_candidates=[])


def _record_bounded_structured_candidate(
    *,
    state: _BoundedStructuredCandidateState,
    candidate: UDAllocationCandidate,
    quantity_error: str | None,
    limit: int = MAX_UD_SELECTION_REPORT_CANDIDATES,
) -> None:
    candidate_key = _candidate_sort_key(candidate)
    state.total_candidate_count += 1
    if quantity_error is None:
        if (
            state.best_viable_candidate is None
            or candidate_key < state.best_viable_candidate[0]
        ):
            state.best_viable_candidate = (candidate_key, candidate)
    _append_bounded_candidate(
        kept_candidates=state.kept_candidates,
        candidate_key=candidate_key,
        candidate=candidate,
        quantity_error=quantity_error,
        limit=limit,
    )


def _append_bounded_candidate(
    *,
    kept_candidates: list[tuple[_ReverseCandidateKey, tuple, UDAllocationCandidate, str | None]],
    candidate_key: tuple,
    candidate: UDAllocationCandidate,
    quantity_error: str | None,
    limit: int,
) -> None:
    entry = (_ReverseCandidateKey(candidate_key), candidate_key, candidate, quantity_error)
    if len(kept_candidates) < limit:
        heapq.heappush(kept_candidates, entry)
        return
    if candidate_key < kept_candidates[0][1]:
        heapq.heapreplace(kept_candidates, entry)


def _ensure_candidate_in_bounded_entries(
    *,
    entries: list[tuple[tuple, UDAllocationCandidate, str | None]],
    candidate: UDAllocationCandidate | None,
    quantity_error: str | None,
    limit: int = MAX_UD_SELECTION_REPORT_CANDIDATES,
) -> None:
    if candidate is None:
        return
    candidate_key = _candidate_sort_key(candidate)
    if any(existing.candidate_id == candidate.candidate_id for _key, existing, _error in entries):
        return
    if len(entries) < limit:
        entries.append((candidate_key, candidate, quantity_error))
        return
    worst_index = max(
        range(len(entries)),
        key=lambda index: entries[index][0],
    )
    entries[worst_index] = (candidate_key, candidate, quantity_error)


def _finalize_structured_selected_candidates(
    *,
    state: _BoundedStructuredCandidateState,
) -> tuple[list[UDAllocationCandidate], UDAllocationCandidate | None, bool]:
    best_candidate = state.best_viable_candidate[1] if state.best_viable_candidate is not None else None
    entries = [
        (candidate_key, candidate, quantity_error)
        for _reverse_key, candidate_key, candidate, quantity_error in state.kept_candidates
    ]
    _ensure_candidate_in_bounded_entries(
        entries=entries,
        candidate=best_candidate,
        quantity_error=None,
    )
    viable_candidate_ids = {
        candidate.candidate_id
        for _key, candidate, quantity_error in entries
        if quantity_error is None
    }
    selected_candidates = [
        replace(
            candidate,
            selected=best_candidate is not None and candidate.candidate_id == best_candidate.candidate_id,
            rejection_reason=None
            if best_candidate is not None and candidate.candidate_id == best_candidate.candidate_id
            else (
                "lower_priority_score"
                if candidate.candidate_id in viable_candidate_ids
                else quantity_error
            ),
        )
        for _key, candidate, quantity_error in sorted(entries, key=lambda item: item[0])
    ]
    return selected_candidates, best_candidate, state.total_candidate_count > len(selected_candidates)


def _finalize_structured_hard_block_candidates(
    *,
    state: _BoundedStructuredCandidateState,
) -> tuple[list[tuple[UDAllocationCandidate, str | None]], UDAllocationCandidate | None, str | None, bool]:
    if not state.kept_candidates:
        return [], None, None, False
    sorted_entries = sorted(
        (
            (candidate_key, candidate, quantity_error)
            for _reverse_key, candidate_key, candidate, quantity_error in state.kept_candidates
        ),
        key=lambda item: item[0],
    )
    _primary_key, primary_candidate, primary_error = sorted_entries[0]
    _ensure_candidate_in_bounded_entries(
        entries=sorted_entries,
        candidate=primary_candidate,
        quantity_error=primary_error,
    )
    final_entries = sorted(sorted_entries, key=lambda item: item[0])
    return (
        [(candidate, quantity_error) for _key, candidate, quantity_error in final_entries],
        primary_candidate,
        primary_error,
        state.total_candidate_count > len(final_entries),
    )


def _build_structured_candidate_row(workbook_row, mapping: dict[str, int]) -> UDCandidateRow | None:
    amount = _parse_decimal(workbook_row.values.get(mapping["export_amount"], ""))
    if amount is None:
        return None
    raw_quantity = workbook_row.values.get(mapping["quantity_fabrics"], "")
    quantity = _parse_quantity(raw_quantity) or Decimal("0")
    quantity_unit = _workbook_quantity_unit(workbook_row, mapping["quantity_fabrics"], raw_quantity)
    return UDCandidateRow(
        row_index=workbook_row.row_index,
        lc_sc_number=workbook_row.values.get(mapping["lc_sc_no"], ""),
        quantity=quantity,
        quantity_unit=quantity_unit,
        export_amount=amount,
        ud_ip_shared_value=workbook_row.values.get(mapping["ud_ip_shared"], ""),
        lc_amnd_no=workbook_row.values.get(mapping["lc_amnd_no"], ""),
        lc_amnd_date=workbook_row.values.get(mapping["lc_amnd_date"], ""),
    )


def _shared_value_matches(observed_value: str, expected_value: str) -> bool:
    return _normalize_match_text(observed_value) == _normalize_match_text(expected_value)


def _candidate_sort_key(candidate: UDAllocationCandidate) -> tuple:
    blank_key = candidate.score_keys["blank_field_priority_key"]
    return (
        tuple(candidate.score_keys["row_index_key"]),
        tuple(tuple(item) for item in candidate.score_keys["amendment_recency_key"]),
        blank_key["blank_target_count_desc"],
        blank_key["nonblank_optional_count_asc"],
        candidate.score_keys["stable_candidate_id_key"],
    )


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
    token = _leading_decimal_token(value)
    if token is None:
        return None
    try:
        return Decimal(token)
    except InvalidOperation:
        return None


def _parse_quantity_unit(value: str) -> str | None:
    normalized = value.upper()
    for token in ("YDS", "YRD", "YRDS", "YD", "YARD", "YARDS", "MTR", "MTRS", "METER", "METERS", "METRE", "METRES"):
        if token in normalized:
            return normalize_quantity_unit(token)
    return None


def _workbook_quantity_unit(
    workbook_row,
    quantity_column_index: int,
    raw_quantity: str,
) -> str:
    if quantity_column_index in workbook_row.number_formats:
        return (
            "MTR"
            if _is_mtr_quantity_number_format(workbook_row.number_formats.get(quantity_column_index, ""))
            else "YDS"
        )
    return _parse_quantity_unit(raw_quantity) or "YDS"


def _is_mtr_quantity_number_format(number_format: str) -> bool:
    return number_format.strip().upper() == MTR_QUANTITY_NUMBER_FORMAT.upper()


def _parse_decimal(value: str) -> Decimal | None:
    token = _leading_decimal_token(value)
    if token is None:
        return None
    try:
        return Decimal(token)
    except InvalidOperation:
        return None


def _leading_decimal_token(value: str) -> str | None:
    normalized = value.strip().replace(",", "")
    token = ""
    for character in normalized:
        if character.isdigit() or character == "." or (character == "-" and not token):
            token += character
        elif token:
            break
    return token if token and token not in {"-", "."} else None


def _quantity_totals_by_unit(rows: list[UDCandidateRow]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for row in rows:
        if row.quantity <= 0 or not row.quantity_unit:
            continue
        totals[row.quantity_unit] = totals.get(row.quantity_unit, Decimal("0")) + row.quantity
    return totals


def _structured_quantity_error(
    *,
    workbook_quantities: dict[str, Decimal],
    ud_quantities: dict[str, Decimal],
    excess_threshold: Decimal,
) -> str | None:
    for unit, workbook_quantity in sorted(workbook_quantities.items()):
        ud_quantity = ud_quantities.get(unit)
        if ud_quantity is None or ud_quantity < workbook_quantity:
            return "ud_quantity_below_workbook"
        excess = ud_quantity - workbook_quantity
        if Decimal("0") < excess < excess_threshold:
            return "ud_quantity_excess_below_threshold"
    return None


def _structured_candidate(
    *,
    selected_rows: list[UDCandidateRow],
    workbook_value_sum: Decimal,
    lc_sc_value: Decimal,
    workbook_quantities: dict[str, Decimal],
    ud_quantities: dict[str, Decimal],
    selected: bool,
    rejection_reason: str | None,
) -> UDAllocationCandidate:
    row_indexes = [row.row_index for row in selected_rows]
    candidate_id = "-".join(str(row_index) for row_index in row_indexes)
    blank_count = sum(1 for row in selected_rows if not row.ud_ip_shared_value.strip())
    return UDAllocationCandidate(
        candidate_id=candidate_id,
        row_indexes=row_indexes,
        matched_quantities=[
            f"{unit}:{_format_decimal(amount)}"
            for unit, amount in sorted(workbook_quantities.items())
        ],
        quantity_sum=_format_quantity_map(workbook_quantities),
        ignored_excess_quantity=_format_quantity_excess(
            workbook_quantities=workbook_quantities,
            ud_quantities=ud_quantities,
        ),
        score_keys={
            "row_index_key": row_indexes,
            "amendment_recency_key": [
                _amendment_recency_key(row)
                for row in sorted(selected_rows, key=lambda item: item.row_index)
            ],
            "blank_field_priority_key": {
                "blank_target_count_desc": -blank_count,
                "nonblank_optional_count_asc": 0,
            },
            "stable_candidate_id_key": candidate_id,
            "lc_sc_value": _format_decimal(lc_sc_value),
            "workbook_value_sum": _format_decimal(workbook_value_sum),
            "ud_quantity_by_unit": _format_quantity_map(ud_quantities),
            "workbook_quantity_by_unit": _format_quantity_map(workbook_quantities),
        },
        prewrite_blank_targets_count=blank_count,
        prewrite_nonblank_optional_count=0,
        selected=selected,
        rejection_reason=rejection_reason,
    )


def _format_quantity_map(values: dict[str, Decimal]) -> str:
    return "; ".join(
        f"{unit}:{_format_decimal(amount)}"
        for unit, amount in sorted(values.items())
    )


def _format_quantity_excess(
    *,
    workbook_quantities: dict[str, Decimal],
    ud_quantities: dict[str, Decimal],
) -> str:
    excess_by_unit = {
        unit: ud_quantities.get(unit, Decimal("0")) - amount
        for unit, amount in workbook_quantities.items()
    }
    return _format_quantity_map(excess_by_unit)


def _format_decimal(value: Decimal) -> str:
    normalized = format(value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _decimal_to_minor_units(value: Decimal) -> int:
    return int((value * 100).quantize(Decimal("1")))
