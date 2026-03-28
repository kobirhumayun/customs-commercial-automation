from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from project.models import (
    DiscrepancyReport,
    FinalDecision,
    MailOutcomeRecord,
    OperatorContext,
    RunReport,
    WorkbookTargetPrevalidationSummary,
    WorkflowId,
    WritePhaseStatus,
)
from project.utils.time import utc_timestamp
from project.workbook import (
    WorkbookWriteSessionProvider,
    XLWingsWorkbookWriteSessionProvider,
    prevalidate_staged_write_plan,
)
from project.workflows.validation import ValidationBatchResult


def prepare_live_write_batch(
    *,
    validation_result: ValidationBatchResult,
    workbook_path: Path,
    operator_context: OperatorContext | None,
    session_provider: WorkbookWriteSessionProvider | None = None,
) -> ValidationBatchResult:
    if not validation_result.staged_write_plan:
        return validation_result

    provider = session_provider or XLWingsWorkbookWriteSessionProvider(workbook_path)
    session_result = provider.open_preflight_session(operator_context=operator_context)
    blocked_mail_outcomes = _block_downstream_eligibility(
        validation_result.mail_outcomes,
        "Workbook write phase is blocked until live preflight/prevalidation succeeds.",
    )

    if session_result.discrepancy_code is not None or session_result.snapshot is None:
        discrepancy_reports = list(validation_result.discrepancy_reports)
        discrepancy_reports.append(
            _build_run_level_discrepancy(
                run_report=validation_result.run_report,
                code=session_result.discrepancy_code or "excel_adapter_unavailable",
                message=session_result.discrepancy_message
                or "Live workbook preflight could not establish a safe write-intent session.",
                details={
                    **session_result.discrepancy_details,
                    "preflight": session_result.preflight.details,
                },
            )
        )
        updated_run_report = replace(
            validation_result.run_report,
            write_phase_status=WritePhaseStatus.HARD_BLOCKED_NO_WRITE,
            workbook_session_preflight=session_result.preflight,
            target_prevalidation_summary=WorkbookTargetPrevalidationSummary(
                total_targets=len(validation_result.staged_write_plan),
                matches_pre_write=0,
                matches_post_write=0,
                mismatch_unknown=0,
                status="not_run",
            ),
        )
        return ValidationBatchResult(
            run_report=updated_run_report,
            mail_outcomes=blocked_mail_outcomes,
            mail_reports=validation_result.mail_reports,
            discrepancy_reports=discrepancy_reports,
            staged_write_plan=validation_result.staged_write_plan,
            target_probes=[],
        )

    prevalidation_result = prevalidate_staged_write_plan(
        workflow_id=validation_result.run_report.workflow_id,
        run_id=validation_result.run_report.run_id,
        workbook_snapshot=session_result.snapshot,
        staged_write_plan=validation_result.staged_write_plan,
    )
    has_prevalidation_failures = bool(prevalidation_result.discrepancy_reports)
    updated_run_report = replace(
        validation_result.run_report,
        write_phase_status=(
            WritePhaseStatus.HARD_BLOCKED_NO_WRITE
            if has_prevalidation_failures
            else WritePhaseStatus.PREVALIDATED
        ),
        workbook_session_preflight=session_result.preflight,
        target_prevalidation_summary=prevalidation_result.summary,
    )
    return ValidationBatchResult(
        run_report=updated_run_report,
        mail_outcomes=(
            blocked_mail_outcomes
            if has_prevalidation_failures
            else validation_result.mail_outcomes
        ),
        mail_reports=validation_result.mail_reports,
        discrepancy_reports=list(validation_result.discrepancy_reports)
        + list(prevalidation_result.discrepancy_reports),
        staged_write_plan=validation_result.staged_write_plan,
        target_probes=prevalidation_result.probes,
    )


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
