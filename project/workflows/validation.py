from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from project.documents import NullSavedDocumentAnalysisProvider, SavedDocumentAnalysisProvider
from project.erp import ERPRowProvider
from project.models import (
    DiscrepancyReport,
    EmailMessage,
    FinalDecision,
    MailOutcomeRecord,
    MailProcessingStatus,
    MailReport,
    OperatorContext,
    RunReport,
    WriteCommitMarker,
    WorkbookTargetProbe,
    WorkflowId,
    WriteOperation,
)
from project.rules import AggregatedRuleEvaluation, LoadedRulePack, evaluate_rule_pack
from project.storage import AttachmentContentProvider, DocumentSaveIssue, DocumentSaveResult, save_export_mail_documents
from project.utils.hashing import canonical_json_hash
from project.utils.json import to_jsonable
from project.utils.time import utc_timestamp
from project.workbook import WorkbookRow, WorkbookSnapshot, resolve_export_header_mapping
from project.workflows.export_lc_sc.document_classification import (
    ClassifiedDocumentSet,
    DocumentClassificationDiscrepancy,
    classify_saved_export_documents,
)
from project.workflows.duplicate_handling import classify_write_disposition
from project.workflows.export_lc_sc.payloads import ExportMailPayload
from project.workflows.export_lc_sc.staging import (
    ExportStagingDiscrepancy,
    ExportWriteStagingResult,
    stage_export_append_operations,
)
from project.workflows.payloads import build_workflow_payload
from project.workflows.registry import WorkflowDescriptor


@dataclass(slots=True, frozen=True)
class WorkflowValidationContext:
    run_id: str
    workflow_id: WorkflowId
    rule_pack_id: str
    rule_pack_version: str
    state_timezone: str
    operator_context: OperatorContext | None
    mail: EmailMessage
    workflow_payload: object | None


@dataclass(slots=True, frozen=True)
class ValidationBatchResult:
    run_report: RunReport
    mail_outcomes: list[MailOutcomeRecord]
    mail_reports: list[MailReport]
    discrepancy_reports: list[DiscrepancyReport]
    staged_write_plan: list[WriteOperation]
    target_probes: list[WorkbookTargetProbe]
    commit_marker: WriteCommitMarker | None


def validate_run_snapshot(
    *,
    descriptor: WorkflowDescriptor,
    run_report: RunReport,
    rule_pack: LoadedRulePack,
    erp_row_provider: ERPRowProvider | None = None,
    workbook_snapshot: WorkbookSnapshot | None = None,
    attachment_content_provider: AttachmentContentProvider | None = None,
    document_root: Path | None = None,
    document_analysis_provider: SavedDocumentAnalysisProvider | None = None,
) -> ValidationBatchResult:
    mail_outcomes: list[MailOutcomeRecord] = []
    mail_reports: list[MailReport] = []
    discrepancy_reports: list[DiscrepancyReport] = []
    staged_write_plan: list[WriteOperation] = []
    summary = {"pass": 0, "warning": 0, "hard_block": 0}
    working_workbook_snapshot = workbook_snapshot

    for mail in run_report.mail_snapshot:
        context = WorkflowValidationContext(
            run_id=run_report.run_id,
            workflow_id=run_report.workflow_id,
            rule_pack_id=rule_pack.rule_pack_id,
            rule_pack_version=rule_pack.rule_pack_version,
            state_timezone=run_report.state_timezone,
            operator_context=run_report.operator_context,
            mail=mail,
            workflow_payload=build_workflow_payload(
                run_report.workflow_id,
                mail,
                erp_row_provider=erp_row_provider,
            ),
        )
        document_save_result = _save_mail_documents_if_configured(
            descriptor=descriptor,
            mail=mail,
            workflow_payload=context.workflow_payload,
            attachment_content_provider=attachment_content_provider,
            document_root=document_root,
        )
        document_classification_result = _classify_saved_documents_for_workflow(
            descriptor=descriptor,
            workflow_payload=context.workflow_payload,
            document_save_result=document_save_result,
            document_analysis_provider=document_analysis_provider,
        )
        aggregated = evaluate_rule_pack(context, rule_pack)
        staging_result = _stage_mail_if_eligible(
            descriptor=descriptor,
            run_report=run_report,
            mail=mail,
            aggregated=aggregated,
            workflow_payload=context.workflow_payload,
            workbook_snapshot=working_workbook_snapshot,
            baseline_workbook_snapshot=workbook_snapshot,
        )
        mail_outcome = _build_mail_outcome(
            descriptor=descriptor,
            run_report=run_report,
            mail=mail,
            aggregated=aggregated,
            workflow_payload=context.workflow_payload,
            staging_result=staging_result,
            document_save_result=document_save_result,
            document_classification_result=document_classification_result,
        )
        mail_report = _build_mail_report(run_report, rule_pack, mail_outcome)
        mail_discrepancies = _build_discrepancy_reports(
            run_report=run_report,
            mail=mail,
            aggregated=aggregated,
            staging_discrepancies=staging_result.discrepancies,
            document_save_issues=document_save_result.issues,
            document_classification_discrepancies=document_classification_result.discrepancies,
        )

        mail_outcomes.append(mail_outcome)
        mail_reports.append(mail_report)
        discrepancy_reports.extend(mail_discrepancies)
        staged_write_plan.extend(staging_result.staged_write_operations)
        summary[mail_outcome.final_decision.value] += 1
        working_workbook_snapshot = _advance_workbook_snapshot_for_staged_writes(
            descriptor=descriptor,
            workbook_snapshot=working_workbook_snapshot,
            staged_write_operations=staging_result.staged_write_operations,
        )

    staged_write_plan_hash = canonical_json_hash(to_jsonable(staged_write_plan))

    updated_run_report = RunReport(
        run_id=run_report.run_id,
        workflow_id=run_report.workflow_id,
        tool_version=run_report.tool_version,
        rule_pack_id=run_report.rule_pack_id,
        rule_pack_version=run_report.rule_pack_version,
        started_at_utc=run_report.started_at_utc,
        completed_at_utc=run_report.completed_at_utc,
        state_timezone=run_report.state_timezone,
        mail_iteration_order=list(run_report.mail_iteration_order),
        print_group_order=list(run_report.print_group_order),
        write_phase_status=run_report.write_phase_status,
        print_phase_status=run_report.print_phase_status,
        mail_move_phase_status=run_report.mail_move_phase_status,
        hash_algorithm=run_report.hash_algorithm,
        run_start_backup_hash=run_report.run_start_backup_hash,
        current_workbook_hash=run_report.current_workbook_hash,
        staged_write_plan_hash=staged_write_plan_hash,
        summary=summary,
        operator_context=run_report.operator_context,
        mail_snapshot=list(run_report.mail_snapshot),
        resolved_source_folder_entry_id=run_report.resolved_source_folder_entry_id,
        resolved_destination_folder_entry_id=run_report.resolved_destination_folder_entry_id,
        folder_resolution_mode=run_report.folder_resolution_mode,
        workbook_session_preflight=run_report.workbook_session_preflight,
        target_prevalidation_summary=run_report.target_prevalidation_summary,
    )
    return ValidationBatchResult(
        run_report=updated_run_report,
        mail_outcomes=mail_outcomes,
        mail_reports=mail_reports,
        discrepancy_reports=discrepancy_reports,
        staged_write_plan=staged_write_plan,
        target_probes=[],
        commit_marker=None,
    )


def _build_mail_outcome(
    *,
    descriptor: WorkflowDescriptor,
    run_report: RunReport,
    mail: EmailMessage,
    aggregated: AggregatedRuleEvaluation,
    workflow_payload: object | None,
    staging_result,
    document_save_result: DocumentSaveResult,
    document_classification_result: ClassifiedDocumentSet,
) -> MailOutcomeRecord:
    final_decision = (
        FinalDecision.HARD_BLOCK
        if (
            staging_result.discrepancies
            or document_save_result.issues
            or document_classification_result.discrepancies
        )
        else aggregated.final_decision
    )
    processing_status = (
        MailProcessingStatus.BLOCKED
        if final_decision == FinalDecision.HARD_BLOCK
        else MailProcessingStatus.VALIDATED
    )
    allowed = final_decision != FinalDecision.HARD_BLOCK
    write_disposition = classify_write_disposition(
        decision_reasons=(
            list(document_save_result.decision_reasons)
            + list(document_classification_result.decision_reasons)
            + list(aggregated.decision_reasons)
            + list(staging_result.decision_reasons)
        ),
        staged_write_operations=staging_result.staged_write_operations,
    )
    return MailOutcomeRecord(
        run_id=run_report.run_id,
        mail_id=mail.mail_id,
        workflow_id=run_report.workflow_id,
        snapshot_index=mail.snapshot_index,
        processing_status=processing_status,
        final_decision=final_decision,
        decision_reasons=(
            list(document_save_result.decision_reasons)
            + list(document_classification_result.decision_reasons)
            + list(aggregated.decision_reasons)
            + list(staging_result.decision_reasons)
        ),
        eligible_for_write=allowed and descriptor.write_capable and bool(staging_result.staged_write_operations),
        eligible_for_print=(
            allowed
            and descriptor.supports_print
            and write_disposition in {"new_writes_staged", "mixed_duplicate_and_new_writes"}
        ),
        eligible_for_mail_move=allowed,
        source_entry_id=mail.entry_id,
        subject_raw=mail.subject_raw,
        sender_address=mail.sender_address,
        rule_pack_id=run_report.rule_pack_id,
        rule_pack_version=run_report.rule_pack_version,
        applied_rule_ids=list(aggregated.applied_rule_ids),
        discrepancies=[
            _serialize_discrepancy(discrepancy) for discrepancy in aggregated.discrepancies
        ]
        + [_serialize_document_save_issue(issue) for issue in document_save_result.issues]
        + [
            _serialize_document_classification_discrepancy(discrepancy)
            for discrepancy in document_classification_result.discrepancies
        ]
        + [_serialize_staging_discrepancy(discrepancy) for discrepancy in staging_result.discrepancies],
        file_numbers_extracted=_extract_file_numbers(descriptor, workflow_payload),
        saved_documents=to_jsonable(document_classification_result.saved_documents),
        staged_write_operations=to_jsonable(staging_result.staged_write_operations),
        write_disposition=write_disposition,
    )


def _build_mail_report(
    run_report: RunReport,
    rule_pack: LoadedRulePack,
    mail_outcome: MailOutcomeRecord,
) -> MailReport:
    return MailReport(
        run_id=run_report.run_id,
        mail_id=mail_outcome.mail_id,
        workflow_id=run_report.workflow_id,
        rule_pack_id=rule_pack.rule_pack_id,
        rule_pack_version=rule_pack.rule_pack_version,
        applied_rule_ids=list(mail_outcome.applied_rule_ids),
        final_decision=mail_outcome.final_decision or FinalDecision.PASS,
        decision_reasons=list(mail_outcome.decision_reasons),
        file_numbers_extracted=list(mail_outcome.file_numbers_extracted),
        saved_documents=list(mail_outcome.saved_documents),
        staged_write_operations=list(mail_outcome.staged_write_operations),
        discrepancies=list(mail_outcome.discrepancies),
        import_keyword_revision=mail_outcome.import_keyword_revision,
    )


def _build_discrepancy_reports(
    *,
    run_report: RunReport,
    mail: EmailMessage,
    aggregated: AggregatedRuleEvaluation,
    staging_discrepancies: list[ExportStagingDiscrepancy],
    document_save_issues: list[DocumentSaveIssue],
    document_classification_discrepancies: list[DocumentClassificationDiscrepancy],
) -> list[DiscrepancyReport]:
    created_at_utc = utc_timestamp()
    reports = [
        DiscrepancyReport(
            run_id=run_report.run_id,
            mail_id=mail.mail_id,
            workflow_id=run_report.workflow_id,
            severity=discrepancy.severity,
            code=discrepancy.code,
            message=discrepancy.message,
            rule_id=discrepancy.source_rule_ids[0] if discrepancy.source_rule_ids else None,
            details={
                **discrepancy.details,
                "subject_scope": discrepancy.subject_scope,
                "target_ref": discrepancy.target_ref,
                "source_rule_ids": list(discrepancy.source_rule_ids),
            },
            created_at_utc=created_at_utc,
        )
        for discrepancy in aggregated.discrepancies
    ]
    reports.extend(
        DiscrepancyReport(
            run_id=run_report.run_id,
            mail_id=mail.mail_id,
            workflow_id=run_report.workflow_id,
            severity=discrepancy.severity,
            code=discrepancy.code,
            message=discrepancy.message,
            rule_id=None,
            details={
                **discrepancy.details,
                "non_rule_source": "document_classification",
            },
            created_at_utc=created_at_utc,
        )
        for discrepancy in document_classification_discrepancies
    )
    reports.extend(
        DiscrepancyReport(
            run_id=run_report.run_id,
            mail_id=mail.mail_id,
            workflow_id=run_report.workflow_id,
            severity=issue.severity,
            code=issue.code,
            message=issue.message,
            rule_id=None,
            details={
                **issue.details,
                "non_rule_source": "document_saving",
            },
            created_at_utc=created_at_utc,
        )
        for issue in document_save_issues
    )
    reports.extend(
        DiscrepancyReport(
            run_id=run_report.run_id,
            mail_id=mail.mail_id,
            workflow_id=run_report.workflow_id,
            severity=discrepancy.severity,
            code=discrepancy.code,
            message=discrepancy.message,
            rule_id=None,
            details={
                **discrepancy.details,
                "non_rule_source": "workbook_staging",
            },
            created_at_utc=created_at_utc,
        )
        for discrepancy in staging_discrepancies
    )
    return reports


def _serialize_discrepancy(discrepancy) -> dict:
    return {
        "code": discrepancy.code,
        "severity": discrepancy.severity,
        "message": discrepancy.message,
        "subject_scope": discrepancy.subject_scope,
        "target_ref": discrepancy.target_ref,
        "details": dict(discrepancy.details),
        "source_rule_ids": list(discrepancy.source_rule_ids),
    }


def _serialize_staging_discrepancy(discrepancy: ExportStagingDiscrepancy) -> dict:
    return {
        "code": discrepancy.code,
        "severity": discrepancy.severity,
        "message": discrepancy.message,
        "subject_scope": "mail",
        "target_ref": None,
        "details": dict(discrepancy.details),
        "source_rule_ids": [],
    }


def _serialize_document_save_issue(issue: DocumentSaveIssue) -> dict:
    return {
        "code": issue.code,
        "severity": issue.severity,
        "message": issue.message,
        "subject_scope": "mail",
        "target_ref": None,
        "details": dict(issue.details),
        "source_rule_ids": [],
    }


def _serialize_document_classification_discrepancy(
    discrepancy: DocumentClassificationDiscrepancy,
) -> dict:
    return {
        "code": discrepancy.code,
        "severity": discrepancy.severity,
        "message": discrepancy.message,
        "subject_scope": "mail",
        "target_ref": None,
        "details": dict(discrepancy.details),
        "source_rule_ids": [],
    }


def _stage_mail_if_eligible(
    *,
    descriptor: WorkflowDescriptor,
    run_report: RunReport,
    mail: EmailMessage,
    aggregated: AggregatedRuleEvaluation,
    workflow_payload: object | None,
    workbook_snapshot: WorkbookSnapshot | None,
    baseline_workbook_snapshot: WorkbookSnapshot | None,
):
    if aggregated.final_decision == FinalDecision.HARD_BLOCK:
        return ExportWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[],
            decision_reasons=[],
        )
    if descriptor.workflow_id != WorkflowId.EXPORT_LC_SC:
        return ExportWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[],
            decision_reasons=[],
        )
    if not isinstance(workflow_payload, ExportMailPayload):
        raise ValueError("Export workflow validation requires ExportMailPayload for staging")
    return stage_export_append_operations(
        run_id=run_report.run_id,
        mail_id=mail.mail_id,
        payload=workflow_payload,
        workbook_snapshot=workbook_snapshot,
        baseline_workbook_snapshot=baseline_workbook_snapshot,
    )


def _advance_workbook_snapshot_for_staged_writes(
    *,
    descriptor: WorkflowDescriptor,
    workbook_snapshot: WorkbookSnapshot | None,
    staged_write_operations: list[WriteOperation],
) -> WorkbookSnapshot | None:
    if workbook_snapshot is None or not staged_write_operations:
        return workbook_snapshot
    if descriptor.workflow_id != WorkflowId.EXPORT_LC_SC:
        return workbook_snapshot

    header_mapping = resolve_export_header_mapping(workbook_snapshot)
    if header_mapping is None:
        return workbook_snapshot

    column_indices_by_key = dict(header_mapping)
    rows_by_index = {
        row.row_index: dict(row.values)
        for row in workbook_snapshot.rows
    }
    for operation in staged_write_operations:
        column_index = column_indices_by_key.get(operation.column_key)
        if column_index is None:
            continue
        row_values = rows_by_index.setdefault(operation.row_index, {})
        row_values[column_index] = (
            "" if operation.expected_post_write_value is None else str(operation.expected_post_write_value)
        )

    updated_rows = [
        WorkbookRow(row_index=row_index, values=rows_by_index[row_index])
        for row_index in sorted(rows_by_index)
    ]
    return WorkbookSnapshot(
        sheet_name=workbook_snapshot.sheet_name,
        headers=list(workbook_snapshot.headers),
        rows=updated_rows,
    )


def _save_mail_documents_if_configured(
    *,
    descriptor: WorkflowDescriptor,
    mail: EmailMessage,
    workflow_payload: object | None,
    attachment_content_provider: AttachmentContentProvider | None,
    document_root: Path | None,
) -> DocumentSaveResult:
    if attachment_content_provider is None or document_root is None:
        return DocumentSaveResult(saved_documents=[], issues=[], decision_reasons=[])
    if descriptor.workflow_id != WorkflowId.EXPORT_LC_SC:
        return DocumentSaveResult(saved_documents=[], issues=[], decision_reasons=[])
    if not isinstance(workflow_payload, ExportMailPayload):
        raise ValueError("Export workflow validation requires ExportMailPayload for document saving")
    return save_export_mail_documents(
        mail=mail,
        verified_family=workflow_payload.verified_family,
        document_root=document_root,
        provider=attachment_content_provider,
    )


def _classify_saved_documents_for_workflow(
    *,
    descriptor: WorkflowDescriptor,
    workflow_payload: object | None,
    document_save_result: DocumentSaveResult,
    document_analysis_provider: SavedDocumentAnalysisProvider | None,
) -> ClassifiedDocumentSet:
    if descriptor.workflow_id != WorkflowId.EXPORT_LC_SC:
        return ClassifiedDocumentSet(
            saved_documents=list(document_save_result.saved_documents),
            decision_reasons=[],
            discrepancies=[],
        )
    if not isinstance(workflow_payload, ExportMailPayload):
        raise ValueError("Export workflow validation requires ExportMailPayload for document classification")
    return classify_saved_export_documents(
        payload=workflow_payload,
        saved_documents=document_save_result.saved_documents,
        analysis_provider=document_analysis_provider or NullSavedDocumentAnalysisProvider(),
    )


def _extract_file_numbers(descriptor: WorkflowDescriptor, workflow_payload: object | None) -> list[str]:
    if descriptor.workflow_id.value != "export_lc_sc":
        return []
    if not isinstance(workflow_payload, ExportMailPayload):
        return []
    return list(workflow_payload.file_numbers)
