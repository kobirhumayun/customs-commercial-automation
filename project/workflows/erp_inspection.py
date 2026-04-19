from __future__ import annotations

from project.erp import ERPRowProvider
from project.erp.models import ERPRegisterRow
from project.workflows.export_lc_sc.parsing import normalize_file_number


def inspect_erp_rows(
    *,
    provider: ERPRowProvider,
    requested_file_numbers: list[str],
) -> dict[str, object]:
    canonical_file_numbers: list[str] = []
    seen: set[str] = set()
    for raw_value in requested_file_numbers:
        canonical = normalize_file_number(raw_value)
        if canonical is None:
            raise ValueError(f"Invalid file number: {raw_value}")
        if canonical in seen:
            continue
        seen.add(canonical)
        canonical_file_numbers.append(canonical)

    if not canonical_file_numbers:
        raise ValueError("At least one valid --file-number is required")

    matches = provider.lookup_rows(file_numbers=canonical_file_numbers)
    rows_by_file_number: dict[str, list[ERPRegisterRow]] = {
        file_number: list(matches.get(file_number, []))
        for file_number in canonical_file_numbers
    }
    return {
        "requested_file_numbers": list(requested_file_numbers),
        "canonical_file_numbers": canonical_file_numbers,
        "match_count": sum(len(rows) for rows in rows_by_file_number.values()),
        "rows_by_file_number": rows_by_file_number,
    }
