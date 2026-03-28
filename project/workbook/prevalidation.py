from __future__ import annotations

from dataclasses import dataclass

from project.models import (
    DiscrepancyReport,
    FinalDecision,
    WorkbookTargetPrevalidationSummary,
    WorkbookTargetProbe,
    WorkflowId,
    WriteOperation,
)
from project.utils.time import utc_timestamp
from project.workbook.mapping import EXPORT_HEADER_SPECS, resolve_header_mapping
from project.workbook.models import WorkbookRow, WorkbookSnapshot


@dataclass(slots=True, frozen=True)
class WorkbookTargetPrevalidationResult:
    probes: list[WorkbookTargetProbe]
    discrepancy_reports: list[DiscrepancyReport]
    summary: WorkbookTargetPrevalidationSummary


def prevalidate_staged_write_plan(
    *,
    workflow_id: WorkflowId,
    run_id: str,
    workbook_snapshot: WorkbookSnapshot,
    staged_write_plan: list[WriteOperation],
) -> WorkbookTargetPrevalidationResult:
    mapping = _resolve_workflow_header_mapping(workflow_id, workbook_snapshot)
    if mapping is None:
        return WorkbookTargetPrevalidationResult(
            probes=[],
            discrepancy_reports=[
                DiscrepancyReport(
                    run_id=run_id,
                    workflow_id=workflow_id,
                    severity=FinalDecision.HARD_BLOCK,
                    code="workbook_header_mapping_invalid",
                    message="Required workbook headers could not be resolved deterministically during target prevalidation.",
                    created_at_utc=utc_timestamp(),
                    details={"sheet_name": workbook_snapshot.sheet_name},
                )
            ],
            summary=WorkbookTargetPrevalidationSummary(
                total_targets=len(staged_write_plan),
                matches_pre_write=0,
                matches_post_write=0,
                mismatch_unknown=0,
                status="hard_blocked",
            ),
        )

    rows_by_index = {row.row_index: row for row in workbook_snapshot.rows}
    max_existing_row = max((row.row_index for row in workbook_snapshot.rows), default=2)
    probes: list[WorkbookTargetProbe] = []
    discrepancy_reports: list[DiscrepancyReport] = []
    created_at_utc = utc_timestamp()

    for operation in staged_write_plan:
        column_index = mapping.get(operation.column_key)
        row = rows_by_index.get(operation.row_index)
        observed_value = _read_observed_value(row, column_index)
        failure_reason: str | None = None

        if operation.sheet_name != workbook_snapshot.sheet_name:
            failure_reason = "sheet_name_mismatch"
        elif column_index is None:
            failure_reason = "column_key_unmapped"
        elif not _row_eligibility_satisfied(operation, row, max_existing_row, observed_value):
            failure_reason = "row_eligibility_failed"

        classification = _classify_probe(
            expected_pre_write_value=operation.expected_pre_write_value,
            expected_post_write_value=operation.expected_post_write_value,
            observed_value=observed_value,
            failure_reason=failure_reason,
        )
        probes.append(
            WorkbookTargetProbe(
                write_operation_id=operation.write_operation_id,
                run_id=operation.run_id,
                mail_id=operation.mail_id,
                sheet_name=operation.sheet_name,
                row_index=operation.row_index,
                column_key=operation.column_key,
                column_index=column_index,
                expected_pre_write_value=operation.expected_pre_write_value,
                expected_post_write_value=operation.expected_post_write_value,
                observed_value=observed_value,
                classification=classification,
            )
        )
        if classification != "matches_pre_write":
            discrepancy_reports.append(
                DiscrepancyReport(
                    run_id=run_id,
                    mail_id=operation.mail_id,
                    workflow_id=workflow_id,
                    severity=FinalDecision.HARD_BLOCK,
                    code="workbook_target_prevalidation_failed",
                    message="Staged workbook target failed prevalidation against the live workbook snapshot.",
                    created_at_utc=created_at_utc,
                    details={
                        "write_operation_id": operation.write_operation_id,
                        "sheet_name": operation.sheet_name,
                        "row_index": operation.row_index,
                        "column_key": operation.column_key,
                        "column_index": column_index,
                        "observed_value": observed_value,
                        "expected_pre_write_value": operation.expected_pre_write_value,
                        "expected_post_write_value": operation.expected_post_write_value,
                        "classification": classification,
                        "failure_reason": failure_reason,
                    },
                )
            )

    matches_pre_write = sum(1 for probe in probes if probe.classification == "matches_pre_write")
    matches_post_write = sum(1 for probe in probes if probe.classification == "matches_post_write")
    mismatch_unknown = sum(1 for probe in probes if probe.classification == "mismatch_unknown")
    summary = WorkbookTargetPrevalidationSummary(
        total_targets=len(probes),
        matches_pre_write=matches_pre_write,
        matches_post_write=matches_post_write,
        mismatch_unknown=mismatch_unknown,
        status="passed" if not discrepancy_reports else "hard_blocked",
    )
    return WorkbookTargetPrevalidationResult(
        probes=probes,
        discrepancy_reports=discrepancy_reports,
        summary=summary,
    )


def _resolve_workflow_header_mapping(
    workflow_id: WorkflowId,
    workbook_snapshot: WorkbookSnapshot,
) -> dict[str, int] | None:
    if workflow_id == WorkflowId.EXPORT_LC_SC:
        return resolve_header_mapping(workbook_snapshot, EXPORT_HEADER_SPECS)
    return {}


def _read_observed_value(row: WorkbookRow | None, column_index: int | None) -> str | None:
    if row is None or column_index is None:
        return None
    return row.values.get(column_index, "")


def _row_eligibility_satisfied(
    operation: WriteOperation,
    row: WorkbookRow | None,
    max_existing_row: int,
    observed_value: str | None,
) -> bool:
    checks = set(operation.row_eligibility_checks)
    if "append_target_row_is_new" in checks:
        if row is not None:
            return False
        if operation.row_index <= max_existing_row:
            return False
    if "target_cell_blank_by_construction" in checks:
        if _normalize_value(observed_value) not in {"", None}:
            return False
    return True


def _classify_probe(
    *,
    expected_pre_write_value: str | int | float | None,
    expected_post_write_value: str | int | float | None,
    observed_value: str | int | float | None,
    failure_reason: str | None,
) -> str:
    if failure_reason is not None:
        return "mismatch_unknown"
    if _normalize_value(observed_value) == _normalize_value(expected_post_write_value):
        return "matches_post_write"
    if _normalize_value(observed_value) == _normalize_value(expected_pre_write_value):
        return "matches_pre_write"
    if expected_pre_write_value is None and _normalize_value(observed_value) in {"", None}:
        return "matches_pre_write"
    return "mismatch_unknown"


def _normalize_value(value: str | int | float | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized
