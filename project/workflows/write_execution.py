from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from dataclasses import replace
from pathlib import Path
from typing import Callable

from project.models import (
    DiscrepancyReport,
    FinalDecision,
    MailOutcomeRecord,
    MailProcessingStatus,
    OperatorContext,
    RunReport,
    WorkbookTargetProbe,
    WorkbookTargetPrevalidationSummary,
    WriteCommitMarker,
    WriteOperation,
    WritePhaseStatus,
)
from project.utils.hashing import canonical_json_hash, sha256_file
from project.utils.time import utc_timestamp
from project.workbook import (
    WorkbookMutationSession,
    WorkbookMutationSessionProvider,
    WorkbookTargetPrevalidationResult,
    XLWingsWorkbookMutationProvider,
    prevalidate_staged_write_plan,
)
from project.workflows.validation import ValidationBatchResult


def execute_live_write_batch(
    *,
    validation_result: ValidationBatchResult,
    workbook_path: Path,
    operator_context: OperatorContext | None,
    session_provider: WorkbookMutationSessionProvider | None = None,
    run_report_persistor: Callable[[RunReport], None] | None = None,
    target_probe_persistor: Callable[[list[WorkbookTargetProbe]], None] | None = None,
) -> ValidationBatchResult:
    if not validation_result.staged_write_plan:
        return validation_result

    provider = session_provider or XLWingsWorkbookMutationProvider(workbook_path)
    open_result = provider.open_write_session(operator_context=operator_context)
    blocked_mail_outcomes = _block_downstream_eligibility(
        validation_result.mail_outcomes,
        "Workbook write phase could not complete safely; downstream phases are blocked pending recovery.",
    )

    if open_result.discrepancy_code is not None or open_result.session is None:
        updated_run_report = replace(
            validation_result.run_report,
            write_phase_status=WritePhaseStatus.HARD_BLOCKED_NO_WRITE,
            workbook_session_preflight=open_result.preflight,
            target_prevalidation_summary=WorkbookTargetPrevalidationSummary(
                total_targets=len(validation_result.staged_write_plan),
                matches_pre_write=0,
                matches_post_write=0,
                mismatch_unknown=0,
                status="not_run",
            ),
        )
        _persist_run_report(run_report_persistor, updated_run_report)
        return ValidationBatchResult(
            run_report=updated_run_report,
            mail_outcomes=blocked_mail_outcomes,
            mail_reports=validation_result.mail_reports,
            discrepancy_reports=list(validation_result.discrepancy_reports)
            + [
                _build_run_level_discrepancy(
                    run_report=validation_result.run_report,
                    code=open_result.discrepancy_code or "excel_adapter_unavailable",
                    message=open_result.discrepancy_message
                    or "Workbook write session could not be opened safely.",
                    details={
                        **open_result.discrepancy_details,
                        "preflight": open_result.preflight.details,
                    },
                )
            ],
            staged_write_plan=validation_result.staged_write_plan,
            target_probes=[],
            commit_marker=None,
        )

    session = open_result.session
    all_target_probes: list[WorkbookTargetProbe] = []
    try:
        prevalidating_report = replace(
            validation_result.run_report,
            write_phase_status=WritePhaseStatus.PREVALIDATING_TARGETS,
            workbook_session_preflight=open_result.preflight,
        )
        _persist_run_report(run_report_persistor, prevalidating_report)

        prevalidation_result = prevalidate_staged_write_plan(
            workflow_id=validation_result.run_report.workflow_id,
            run_id=validation_result.run_report.run_id,
            workbook_snapshot=session.capture_snapshot(),
            staged_write_plan=validation_result.staged_write_plan,
        )
        all_target_probes.extend(prevalidation_result.probes)
        _persist_target_probes(target_probe_persistor, all_target_probes)

        if prevalidation_result.discrepancy_reports:
            updated_run_report = replace(
                prevalidating_report,
                write_phase_status=WritePhaseStatus.HARD_BLOCKED_NO_WRITE,
                target_prevalidation_summary=prevalidation_result.summary,
            )
            _persist_run_report(run_report_persistor, updated_run_report)
            return ValidationBatchResult(
                run_report=updated_run_report,
                mail_outcomes=blocked_mail_outcomes,
                mail_reports=validation_result.mail_reports,
                discrepancy_reports=list(validation_result.discrepancy_reports)
                + list(prevalidation_result.discrepancy_reports),
                staged_write_plan=validation_result.staged_write_plan,
                target_probes=all_target_probes,
                commit_marker=None,
            )

        prevalidated_report = replace(
            prevalidating_report,
            write_phase_status=WritePhaseStatus.PREVALIDATED,
            target_prevalidation_summary=prevalidation_result.summary,
        )
        _persist_run_report(run_report_persistor, prevalidated_report)

        applying_report = replace(
            prevalidated_report,
            write_phase_status=WritePhaseStatus.APPLYING,
        )
        _persist_run_report(run_report_persistor, applying_report)

        operation_probe_map = {
            probe.write_operation_id: probe for probe in prevalidation_result.probes
        }
        for operation in validation_result.staged_write_plan:
            probe = operation_probe_map[operation.write_operation_id]
            if probe.column_index is None:
                raise ValueError(
                    f"Missing resolved workbook column index for operation {operation.write_operation_id}"
                )
            session.write_cell(
                sheet_name=operation.sheet_name,
                row_index=operation.row_index,
                column_index=probe.column_index,
                value=_coerce_write_value(
                    operation.expected_post_write_value,
                    number_format=operation.number_format,
                ),
                number_format=operation.number_format,
            )

        post_write_probes = _collect_post_write_probes(
            session=session,
            staged_write_plan=validation_result.staged_write_plan,
            prevalidation_result=prevalidation_result,
        )
        all_target_probes.extend(post_write_probes)
        _persist_target_probes(target_probe_persistor, all_target_probes)

        mismatched_post_probes = [
            probe for probe in post_write_probes if probe.classification != "matches_post_write"
        ]
        if mismatched_post_probes:
            updated_run_report = replace(
                applying_report,
                write_phase_status=WritePhaseStatus.UNCERTAIN_NOT_COMMITTED,
            )
            _persist_run_report(run_report_persistor, updated_run_report)
            return ValidationBatchResult(
                run_report=updated_run_report,
                mail_outcomes=blocked_mail_outcomes,
                mail_reports=validation_result.mail_reports,
                discrepancy_reports=list(validation_result.discrepancy_reports)
                + [
                    _build_post_write_probe_discrepancy(
                        run_report=validation_result.run_report,
                        probe=probe,
                    )
                    for probe in mismatched_post_probes
                ],
                staged_write_plan=validation_result.staged_write_plan,
                target_probes=all_target_probes,
                commit_marker=None,
            )

        try:
            session.save()
        except Exception as exc:
            updated_run_report = replace(
                applying_report,
                write_phase_status=WritePhaseStatus.UNCERTAIN_NOT_COMMITTED,
            )
            _persist_run_report(run_report_persistor, updated_run_report)
            return ValidationBatchResult(
                run_report=updated_run_report,
                mail_outcomes=blocked_mail_outcomes,
                mail_reports=validation_result.mail_reports,
                discrepancy_reports=list(validation_result.discrepancy_reports)
                + [
                    _build_run_level_discrepancy(
                        run_report=validation_result.run_report,
                        code="workbook_save_conflict",
                        message="Workbook save failed after staged writes were applied.",
                        details={"error": str(exc)},
                    )
                ],
                staged_write_plan=validation_result.staged_write_plan,
                target_probes=all_target_probes,
                commit_marker=None,
            )

        current_workbook_hash = (
            sha256_file(workbook_path)
            if workbook_path.exists()
            else validation_result.run_report.current_workbook_hash
        )
        commit_marker = WriteCommitMarker(
            run_id=validation_result.run_report.run_id,
            workflow_id=validation_result.run_report.workflow_id,
            tool_version=validation_result.run_report.tool_version,
            rule_pack_version=validation_result.run_report.rule_pack_version,
            committed_at_utc=utc_timestamp(),
            operation_count=len(validation_result.staged_write_plan),
            mail_iteration_order_hash=canonical_json_hash(
                validation_result.run_report.mail_iteration_order
            ),
            staged_write_plan_hash=validation_result.run_report.staged_write_plan_hash,
            run_start_backup_hash=validation_result.run_report.run_start_backup_hash,
            post_write_probe_summary=_summarize_probe_classifications(post_write_probes),
        )
        committed_report = replace(
            applying_report,
            write_phase_status=WritePhaseStatus.COMMITTED,
            current_workbook_hash=current_workbook_hash,
        )
        _persist_run_report(run_report_persistor, committed_report)
        written_mail_outcomes = _mark_written_mail_outcomes(
            validation_result.mail_outcomes,
            staged_write_plan=validation_result.staged_write_plan,
        )
        return ValidationBatchResult(
            run_report=committed_report,
            mail_outcomes=written_mail_outcomes,
            mail_reports=validation_result.mail_reports,
            discrepancy_reports=validation_result.discrepancy_reports,
            staged_write_plan=validation_result.staged_write_plan,
            target_probes=all_target_probes,
            commit_marker=commit_marker,
        )
    except Exception as exc:
        uncertain_report = replace(
            validation_result.run_report,
            write_phase_status=WritePhaseStatus.UNCERTAIN_NOT_COMMITTED,
            workbook_session_preflight=open_result.preflight,
        )
        _persist_run_report(run_report_persistor, uncertain_report)
        return ValidationBatchResult(
            run_report=uncertain_report,
            mail_outcomes=blocked_mail_outcomes,
            mail_reports=validation_result.mail_reports,
            discrepancy_reports=list(validation_result.discrepancy_reports)
            + [
                _build_run_level_discrepancy(
                    run_report=validation_result.run_report,
                    code="workbook_apply_runtime_error",
                    message="A runtime error occurred after workbook write application began.",
                    details={"error": str(exc)},
                )
            ],
            staged_write_plan=validation_result.staged_write_plan,
            target_probes=all_target_probes,
            commit_marker=None,
        )
    finally:
        try:
            session.close()
        except Exception:
            pass


def _collect_post_write_probes(
    *,
    session: WorkbookMutationSession,
    staged_write_plan: list[WriteOperation],
    prevalidation_result: WorkbookTargetPrevalidationResult,
) -> list[WorkbookTargetProbe]:
    prevalidation_probe_map = {
        probe.write_operation_id: probe for probe in prevalidation_result.probes
    }
    probes: list[WorkbookTargetProbe] = []
    for operation in staged_write_plan:
        pre_probe = prevalidation_probe_map[operation.write_operation_id]
        observed_value = session.read_cell(
            sheet_name=operation.sheet_name,
            row_index=operation.row_index,
            column_index=pre_probe.column_index or 0,
        )
        classification = _classify_post_write_probe(
            observed_value=observed_value,
            expected_pre_write_value=operation.expected_pre_write_value,
            expected_post_write_value=operation.expected_post_write_value,
        )
        probes.append(
            WorkbookTargetProbe(
                write_operation_id=operation.write_operation_id,
                run_id=operation.run_id,
                mail_id=operation.mail_id,
                probe_stage="post_write",
                sheet_name=operation.sheet_name,
                row_index=operation.row_index,
                column_key=operation.column_key,
                column_index=pre_probe.column_index,
                expected_pre_write_value=operation.expected_pre_write_value,
                expected_post_write_value=operation.expected_post_write_value,
                observed_value=observed_value,
                classification=classification,
            )
        )
    return probes


def _classify_post_write_probe(
    *,
    observed_value: str | int | float | None,
    expected_pre_write_value: str | int | float | None,
    expected_post_write_value: str | int | float | None,
) -> str:
    observed = _normalize_probe_value(observed_value)
    expected_pre = _normalize_probe_value(expected_pre_write_value)
    expected_post = _normalize_probe_value(expected_post_write_value)
    if observed == expected_post:
        return "matches_post_write"
    if observed == expected_pre:
        return "matches_pre_write"
    if expected_pre_write_value is None and observed in {"", None}:
        return "matches_pre_write"
    return "mismatch_unknown"


def _summarize_probe_classifications(probes: list[WorkbookTargetProbe]) -> dict[str, int]:
    return {
        "matches_post_write": sum(1 for probe in probes if probe.classification == "matches_post_write"),
        "matches_pre_write": sum(1 for probe in probes if probe.classification == "matches_pre_write"),
        "mismatch_unknown": sum(1 for probe in probes if probe.classification == "mismatch_unknown"),
    }


def _mark_written_mail_outcomes(
    mail_outcomes: list[MailOutcomeRecord],
    *,
    staged_write_plan: list[WriteOperation],
) -> list[MailOutcomeRecord]:
    written_mail_ids = {operation.mail_id for operation in staged_write_plan}
    updated: list[MailOutcomeRecord] = []
    for outcome in mail_outcomes:
        if outcome.mail_id not in written_mail_ids:
            updated.append(outcome)
            continue
        updated.append(
            replace(
                outcome,
                processing_status=MailProcessingStatus.WRITTEN,
                eligible_for_write=False,
                decision_reasons=list(outcome.decision_reasons)
                + ["Staged workbook writes committed successfully."],
            )
        )
    return updated


def _block_downstream_eligibility(
    mail_outcomes: list[MailOutcomeRecord],
    reason: str,
) -> list[MailOutcomeRecord]:
    updated: list[MailOutcomeRecord] = []
    for outcome in mail_outcomes:
        if not (outcome.eligible_for_write or outcome.eligible_for_print or outcome.eligible_for_mail_move):
            updated.append(outcome)
            continue
        updated.append(
            replace(
                outcome,
                eligible_for_write=False,
                eligible_for_print=False,
                eligible_for_mail_move=False,
                decision_reasons=list(outcome.decision_reasons) + [reason],
            )
        )
    return updated


def _build_post_write_probe_discrepancy(
    *,
    run_report: RunReport,
    probe: WorkbookTargetProbe,
) -> DiscrepancyReport:
    return DiscrepancyReport(
        run_id=run_report.run_id,
        workflow_id=run_report.workflow_id,
        severity=FinalDecision.HARD_BLOCK,
        code="workbook_post_write_probe_mismatch",
        message="A post-write target probe did not match the expected committed workbook value.",
        created_at_utc=utc_timestamp(),
        mail_id=probe.mail_id,
        details={
            "write_operation_id": probe.write_operation_id,
            "sheet_name": probe.sheet_name,
            "row_index": probe.row_index,
            "column_key": probe.column_key,
            "column_index": probe.column_index,
            "expected_pre_write_value": probe.expected_pre_write_value,
            "expected_post_write_value": probe.expected_post_write_value,
            "observed_value": probe.observed_value,
            "classification": probe.classification,
        },
    )


def _build_run_level_discrepancy(
    *,
    run_report: RunReport,
    code: str,
    message: str,
    details: dict,
) -> DiscrepancyReport:
    return DiscrepancyReport(
        run_id=run_report.run_id,
        workflow_id=run_report.workflow_id,
        severity=FinalDecision.HARD_BLOCK,
        code=code,
        message=message,
        created_at_utc=utc_timestamp(),
        details=details,
    )


def _persist_run_report(
    persistor: Callable[[RunReport], None] | None,
    run_report: RunReport,
) -> None:
    if persistor is not None:
        persistor(run_report)


def _persist_target_probes(
    persistor: Callable[[list[WorkbookTargetProbe]], None] | None,
    probes: list[WorkbookTargetProbe],
) -> None:
    if persistor is not None:
        persistor(probes)


def _normalize_probe_value(value: str | int | float | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return normalized

    date_value = _try_normalize_date(normalized)
    if date_value is not None:
        return date_value

    numeric_value = _try_normalize_decimal(normalized)
    if numeric_value is not None:
        return numeric_value

    return normalized


def _coerce_write_value(
    value: str | int | float | None,
    *,
    number_format: str | None,
) -> str | int | float | date | None:
    if value is None or number_format is None:
        return value
    if "d" not in number_format.lower() or "y" not in number_format.lower():
        return value
    date_value = _try_parse_date_value(str(value).strip())
    return date_value if date_value is not None else value


def _try_parse_date_value(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%b-%y", "%d-%b-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _try_normalize_date(value: str) -> str | None:
    try:
        parsed_datetime = datetime.fromisoformat(value)
    except ValueError:
        parsed_datetime = None
    if parsed_datetime is not None:
        if parsed_datetime.time() == datetime.min.time():
            return parsed_datetime.date().isoformat()
        return parsed_datetime.isoformat()

    for fmt in ("%Y-%m-%d", "%d-%b-%y", "%d-%b-%Y", "%d/%m/%y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _try_normalize_decimal(value: str) -> str | None:
    candidate = value.replace(",", "")
    try:
        decimal_value = Decimal(candidate)
    except InvalidOperation:
        return None
    normalized = format(decimal_value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"
