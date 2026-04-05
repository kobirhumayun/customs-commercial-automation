from __future__ import annotations

import re


DUPLICATE_IN_WORKBOOK_REASON_PATTERN = re.compile(
    r"^Skipped workbook append for (?P<file_number>P/\d{2}/\d{4}) because the file number already exists in the workbook\.$"
)
DUPLICATE_IN_RUN_REASON_PATTERN = re.compile(
    r"^Skipped workbook append for (?P<file_number>P/\d{2}/\d{4}) because the file number was already staged earlier in this run\.$"
)


def summarize_duplicate_decision_reasons(decision_reasons: list[str]) -> dict[str, int]:
    duplicate_in_workbook_file_count = 0
    duplicate_in_run_file_count = 0
    for reason in decision_reasons:
        if DUPLICATE_IN_WORKBOOK_REASON_PATTERN.match(reason):
            duplicate_in_workbook_file_count += 1
        elif DUPLICATE_IN_RUN_REASON_PATTERN.match(reason):
            duplicate_in_run_file_count += 1
    return {
        "duplicate_file_skip_count": duplicate_in_workbook_file_count + duplicate_in_run_file_count,
        "duplicate_in_workbook_file_count": duplicate_in_workbook_file_count,
        "duplicate_in_run_file_count": duplicate_in_run_file_count,
    }


def classify_write_disposition(
    *,
    decision_reasons: list[str],
    staged_write_operations: list[object],
) -> str:
    duplicate_summary = summarize_duplicate_decision_reasons(decision_reasons)
    has_duplicates = duplicate_summary["duplicate_file_skip_count"] > 0
    has_staged_writes = bool(staged_write_operations)
    if has_duplicates and has_staged_writes:
        return "mixed_duplicate_and_new_writes"
    if has_duplicates:
        return "duplicate_only_noop"
    if has_staged_writes:
        return "new_writes_staged"
    return "no_write_noop"
