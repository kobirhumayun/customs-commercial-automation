from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
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
from project.rules.types import RuleDiscrepancy
from project.storage import AttachmentContentProvider, DocumentSaveIssue, DocumentSaveResult, save_export_mail_documents
from project.utils.hashing import canonical_json_hash
from project.utils.json import to_jsonable
from project.utils.time import utc_timestamp
from project.workbook import WorkbookRow, WorkbookSnapshot, resolve_export_header_mapping, resolve_ud_ip_exp_header_mapping
from project.workflows.export_lc_sc.document_classification import (
    ClassifiedDocumentSet,
    DocumentClassificationDiscrepancy,
    classify_saved_export_documents,
)
from project.workflows.duplicate_handling import classify_write_disposition
from project.workflows.export_lc_sc.payloads import ExportMailPayload, build_export_mail_payload
from project.workflows.export_lc_sc.staging import (
    ExportStagingDiscrepancy,
    ExportWriteStagingResult,
    stage_export_append_operations,
)
from project.workflows.payloads import build_workflow_payload
from project.workflows.registry import WorkflowDescriptor
from project.workflows.ud_ip_exp.payloads import UDDocumentPayload, UDIPEXPWorkflowPayload
from project.workflows.ud_ip_exp.providers import UDDocumentPayloadProvider
from project.workflows.ud_ip_exp.staging import UDIPEXPWriteStagingResult
from project.workflows.ud_ip_exp.live_documents import prepare_live_ud_ip_exp_documents
from project.erp.normalization import normalize_lc_sc_date


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
    ud_document_provider: UDDocumentPayloadProvider | None = None,
) -> ValidationBatchResult:
    mail_outcomes: list[MailOutcomeRecord] = []
    mail_reports: list[MailReport] = []
    discrepancy_reports: list[DiscrepancyReport] = []
    staged_write_plan: list[WriteOperation] = []
    summary = {"pass": 0, "warning": 0, "hard_block": 0}
    working_workbook_snapshot = workbook_snapshot

    for mail in run_report.mail_snapshot:
        ud_family_payload = (
            build_export_mail_payload(mail, erp_row_provider=erp_row_provider)
            if descriptor.workflow_id == WorkflowId.UD_IP_EXP
            else None
        )
        workflow_documents = (
            ud_document_provider.get_documents(mail)
            if descriptor.workflow_id == WorkflowId.UD_IP_EXP and ud_document_provider is not None
            else None
        )
        document_save_result, document_classification_result, workflow_documents = (
            _prepare_ud_ip_exp_documents_if_configured(
                run_report=run_report,
                mail=mail,
                workbook_snapshot=working_workbook_snapshot,
                attachment_content_provider=attachment_content_provider,
                document_root=document_root,
                document_analysis_provider=document_analysis_provider,
                workflow_documents=workflow_documents,
                export_payload=ud_family_payload,
            )
            if descriptor.workflow_id == WorkflowId.UD_IP_EXP
            else (
                DocumentSaveResult(saved_documents=[], issues=[], decision_reasons=[]),
                ClassifiedDocumentSet(saved_documents=[], decision_reasons=[], discrepancies=[]),
                None,
            )
        )
        context, aggregated, staging_result, ud_selection = _evaluate_mail_for_workflow(
            descriptor=descriptor,
            run_report=run_report,
            rule_pack=rule_pack,
            mail=mail,
            workbook_snapshot=working_workbook_snapshot,
            baseline_workbook_snapshot=workbook_snapshot,
            erp_row_provider=erp_row_provider,
            ud_document_provider=ud_document_provider,
            workflow_documents=workflow_documents,
            saved_documents=document_classification_result.saved_documents,
            ud_family_payload=ud_family_payload,
        )
        if descriptor.workflow_id != WorkflowId.UD_IP_EXP:
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
        mail_outcome = _build_mail_outcome(
            descriptor=descriptor,
            run_report=run_report,
            mail=mail,
            aggregated=aggregated,
            workflow_payload=context.workflow_payload,
            staging_result=staging_result,
            document_save_result=document_save_result,
            document_classification_result=document_classification_result,
            ud_selection=ud_selection,
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


def _prepare_ud_ip_exp_documents_if_configured(
    *,
    run_report: RunReport,
    mail: EmailMessage,
    workbook_snapshot: WorkbookSnapshot | None,
    attachment_content_provider: AttachmentContentProvider | None,
    document_root: Path | None,
    document_analysis_provider: SavedDocumentAnalysisProvider | None,
    workflow_documents: list | None,
    export_payload: ExportMailPayload | None,
) -> tuple[DocumentSaveResult, ClassifiedDocumentSet, list | None]:
    if attachment_content_provider is None or document_root is None:
        return (
            DocumentSaveResult(saved_documents=[], issues=[], decision_reasons=[]),
            ClassifiedDocumentSet(saved_documents=[], decision_reasons=[], discrepancies=[]),
            workflow_documents,
        )

    prepared = prepare_live_ud_ip_exp_documents(
        run_id=run_report.run_id,
        mail=mail,
        workbook_snapshot=workbook_snapshot,
        document_root=document_root,
        provider=attachment_content_provider,
        analysis_provider=document_analysis_provider or NullSavedDocumentAnalysisProvider(),
        documents_override=list(workflow_documents or []),
        verified_family=export_payload.verified_family if export_payload is not None else None,
        export_payload=export_payload,
        require_verified_family=True,
    )
    return (
        prepared.document_save_result,
        ClassifiedDocumentSet(
            saved_documents=list(prepared.classified_documents.saved_documents),
            decision_reasons=list(prepared.classified_documents.decision_reasons),
            discrepancies=list(prepared.classified_documents.discrepancies),
        ),
        workflow_documents if workflow_documents else list(prepared.classified_documents.documents),
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
    ud_selection: dict | None = None,
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
        ud_selection=to_jsonable(ud_selection),
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
        ud_selection=mail_outcome.ud_selection,
    )


def _evaluate_mail_for_workflow(
    *,
    descriptor: WorkflowDescriptor,
    run_report: RunReport,
    rule_pack: LoadedRulePack,
    mail: EmailMessage,
    workbook_snapshot: WorkbookSnapshot | None,
    baseline_workbook_snapshot: WorkbookSnapshot | None,
    erp_row_provider: ERPRowProvider | None,
    ud_document_provider: UDDocumentPayloadProvider | None,
    workflow_documents: list | None = None,
    saved_documents: list[SavedDocument] | None = None,
    ud_family_payload: ExportMailPayload | None = None,
) -> tuple[WorkflowValidationContext, AggregatedRuleEvaluation, object, dict | None]:
    if descriptor.workflow_id == WorkflowId.UD_IP_EXP:
        return _evaluate_ud_ip_exp_mail(
            descriptor=descriptor,
            run_report=run_report,
            rule_pack=rule_pack,
            mail=mail,
            workbook_snapshot=workbook_snapshot,
            baseline_workbook_snapshot=baseline_workbook_snapshot,
            ud_document_provider=ud_document_provider,
            workflow_documents=workflow_documents,
            saved_documents=saved_documents,
            export_payload=ud_family_payload,
        )

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
    aggregated = evaluate_rule_pack(context, rule_pack)
    staging_result = _stage_mail_if_eligible(
        descriptor=descriptor,
        run_report=run_report,
        mail=mail,
        aggregated=aggregated,
        workflow_payload=context.workflow_payload,
        workbook_snapshot=workbook_snapshot,
        baseline_workbook_snapshot=baseline_workbook_snapshot,
    )
    return context, aggregated, staging_result, None


def _evaluate_ud_ip_exp_mail(
    *,
    descriptor: WorkflowDescriptor,
    run_report: RunReport,
    rule_pack: LoadedRulePack,
    mail: EmailMessage,
    workbook_snapshot: WorkbookSnapshot | None,
    baseline_workbook_snapshot: WorkbookSnapshot | None,
    ud_document_provider: UDDocumentPayloadProvider | None,
    workflow_documents: list | None = None,
    saved_documents: list[SavedDocument] | None = None,
    export_payload: ExportMailPayload | None = None,
) -> tuple[WorkflowValidationContext, AggregatedRuleEvaluation, UDIPEXPWriteStagingResult, dict | None]:
    documents = list(workflow_documents or [])
    if not documents and ud_document_provider is not None:
        documents = ud_document_provider.get_documents(mail)
    duplicate_resolution = _resolve_same_mail_ud_documents(
        documents=documents,
        saved_documents=saved_documents or [],
        mail_id=mail.mail_id,
    )
    duplicate_conflict = next(
        (
            discrepancy
            for discrepancy in duplicate_resolution.discrepancies
            if discrepancy.severity == FinalDecision.HARD_BLOCK
        ),
        None,
    )
    if duplicate_conflict is not None:
        workflow_payload = UDIPEXPWorkflowPayload(documents=documents, export_payload=export_payload)
        context = WorkflowValidationContext(
            run_id=run_report.run_id,
            workflow_id=run_report.workflow_id,
            rule_pack_id=rule_pack.rule_pack_id,
            rule_pack_version=rule_pack.rule_pack_version,
            state_timezone=run_report.state_timezone,
            operator_context=run_report.operator_context,
            mail=mail,
            workflow_payload=workflow_payload,
        )
        return (
            context,
            AggregatedRuleEvaluation(
                applied_rule_ids=[],
                discrepancies=[duplicate_conflict],
                final_decision=FinalDecision.HARD_BLOCK,
                decision_reasons=list(duplicate_resolution.decision_reasons),
            ),
            UDIPEXPWriteStagingResult(
                staged_write_operations=[],
                discrepancies=[],
                decision_reasons=["UD staging skipped because same-mail duplicate evidence hard-blocked."],
            ),
            {
                "document_count": len([document for document in documents if isinstance(document, UDDocumentPayload)]),
                "final_decision": "hard_block",
                "documents": [],
            },
        )
    documents = duplicate_resolution.documents
    ud_documents = _ordered_ud_documents(documents)
    if ud_documents:
        from project.workflows.ud_ip_exp.validation import assemble_ud_validation, workflow_date_from_started_at

        ud_receive_date = workflow_date_from_started_at(
            run_report.started_at_utc,
            state_timezone=run_report.state_timezone,
        )
        working_snapshot = workbook_snapshot
        operation_index_start = 0
        excluded_row_indexes: set[int] = set()
        rule_evaluations: list[AggregatedRuleEvaluation] = []
        staging_results: list[UDIPEXPWriteStagingResult] = []
        ud_selection_items: list[dict] = []
        staged_write_operations: list[WriteOperation] = []
        additional_discrepancies = list(duplicate_resolution.discrepancies)
        additional_reasons = list(duplicate_resolution.decision_reasons)

        for document_index, ud_document in enumerate(ud_documents):
            current_documents = [
                document
                for document in documents
                if not isinstance(document, UDDocumentPayload)
            ] + [ud_document]
            assembly = assemble_ud_validation(
                run_id=run_report.run_id,
                mail=mail,
                rule_pack=rule_pack,
                ud_document=ud_document,
                workbook_snapshot=working_snapshot,
                documents=current_documents,
                saved_documents=[],
                state_timezone=run_report.state_timezone,
                export_payload=export_payload,
                ud_receive_date=ud_receive_date,
                operation_index_start=operation_index_start,
                excluded_row_indexes=excluded_row_indexes,
            )
            rule_evaluations.append(assembly.rule_evaluation)
            staging_results.append(assembly.staging_result)
            ud_selection_items.append(
                {
                    "document_index": document_index,
                    "document_number": ud_document.document_number.value,
                    "document_date": ud_document.document_date.value if ud_document.document_date else None,
                    "source_saved_document_id": ud_document.source_saved_document_id,
                    "selection": assembly.ud_selection,
                }
            )

            document_passed = (
                assembly.rule_evaluation.final_decision != FinalDecision.HARD_BLOCK
                and not assembly.staging_result.discrepancies
            )
            if document_passed:
                staged_write_operations.extend(assembly.staging_result.staged_write_operations)
                operation_index_start += len(assembly.staging_result.staged_write_operations)
                excluded_row_indexes.update(
                    operation.row_index
                    for operation in assembly.staging_result.staged_write_operations
                )
                excluded_row_indexes.update(_selected_ud_selection_rows(assembly.ud_selection))
                working_snapshot = _advance_workbook_snapshot_for_staged_writes(
                    descriptor=descriptor,
                    workbook_snapshot=working_snapshot,
                    staged_write_operations=assembly.staging_result.staged_write_operations,
                )
                duplicate_warning = _same_run_ud_duplicate_warning(
                    assembly=assembly,
                    working_snapshot=working_snapshot,
                    baseline_workbook_snapshot=baseline_workbook_snapshot,
                    expected_document_number=ud_document.document_number.value,
                    mail_id=mail.mail_id,
                )
                if duplicate_warning is not None:
                    additional_discrepancies.append(duplicate_warning)
                    additional_reasons.append(
                        f"Ignored duplicate UD/AM document {ud_document.document_number.value} because the same document was already staged earlier in this run."
                    )
                    staging_results[-1] = replace(
                        staging_results[-1],
                        decision_reasons=[
                            f"Skipped UD shared-column write for {ud_document.document_number.value} because the same document was already staged earlier in this run."
                            if reason
                            == f"Skipped UD shared-column write for {ud_document.document_number.value} because it is already recorded in the workbook."
                            else reason
                            for reason in staging_results[-1].decision_reasons
                        ],
                    )

        aggregated = _combine_aggregated_rule_evaluations(rule_evaluations)
        if additional_discrepancies:
            aggregated = _extend_aggregated_rule_evaluation(
                aggregated=aggregated,
                discrepancies=additional_discrepancies,
                decision_reasons=additional_reasons,
            )
        any_staging_discrepancy = any(result.discrepancies for result in staging_results)
        all_documents_passed = (
            aggregated.final_decision != FinalDecision.HARD_BLOCK
            and not any_staging_discrepancy
        )
        if all_documents_passed:
            staging_result = UDIPEXPWriteStagingResult(
                staged_write_operations=staged_write_operations,
                discrepancies=[],
                decision_reasons=[
                    reason
                    for result in staging_results
                    for reason in result.decision_reasons
                ],
            )
        else:
            staging_result = UDIPEXPWriteStagingResult(
                staged_write_operations=[],
                discrepancies=[
                    discrepancy
                    for result in staging_results
                    for discrepancy in result.discrepancies
                ],
                decision_reasons=[
                    reason
                    for result in staging_results
                    for reason in result.decision_reasons
                    if not reason.startswith("Staged UD shared-column write")
                ] + [
                    "UD multi-document staging returned no writes because at least one UD document hard-blocked."
                ],
            )

        workflow_payload = UDIPEXPWorkflowPayload(
            documents=documents,
            export_payload=export_payload,
        )
        context = WorkflowValidationContext(
            run_id=run_report.run_id,
            workflow_id=run_report.workflow_id,
            rule_pack_id=rule_pack.rule_pack_id,
            rule_pack_version=rule_pack.rule_pack_version,
            state_timezone=run_report.state_timezone,
            operator_context=run_report.operator_context,
            mail=mail,
            workflow_payload=workflow_payload,
        )
        return context, aggregated, staging_result, _build_ud_multi_selection_report(ud_selection_items)

    workflow_payload = UDIPEXPWorkflowPayload(documents=documents, export_payload=export_payload)
    context = WorkflowValidationContext(
        run_id=run_report.run_id,
        workflow_id=run_report.workflow_id,
        rule_pack_id=rule_pack.rule_pack_id,
        rule_pack_version=rule_pack.rule_pack_version,
        state_timezone=run_report.state_timezone,
        operator_context=run_report.operator_context,
        mail=mail,
        workflow_payload=workflow_payload,
    )
    return (
        context,
        evaluate_rule_pack(context, rule_pack),
        UDIPEXPWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[],
            decision_reasons=["UD staging skipped because no deterministic UD payload was supplied."],
        ),
        None,
    )


def _ordered_ud_documents(documents: list) -> list[UDDocumentPayload]:
    indexed_documents = [
        (index, document)
        for index, document in enumerate(documents)
        if isinstance(document, UDDocumentPayload)
    ]
    return [
        document
        for _index, document in sorted(
            indexed_documents,
            key=lambda item: (
                _ud_document_date_sort_key(item[1]),
                item[1].document_number.value.strip().upper(),
                item[0],
            ),
        )
    ]


def _ud_document_date_sort_key(document: UDDocumentPayload) -> str:
    if document.document_date is None:
        return "9999-12-31"
    normalized = normalize_lc_sc_date(document.document_date.value)
    return normalized or "9999-12-31"


def _combine_aggregated_rule_evaluations(
    evaluations: list[AggregatedRuleEvaluation],
) -> AggregatedRuleEvaluation:
    applied_rule_ids: list[str] = []
    decision_reasons: list[str] = []
    discrepancies = []
    seen_warning = False
    seen_hard_block = False
    for evaluation in evaluations:
        for rule_id in evaluation.applied_rule_ids:
            if rule_id not in applied_rule_ids:
                applied_rule_ids.append(rule_id)
        decision_reasons.extend(evaluation.decision_reasons)
        discrepancies.extend(evaluation.discrepancies)
        if evaluation.final_decision == FinalDecision.WARNING:
            seen_warning = True
        if evaluation.final_decision == FinalDecision.HARD_BLOCK:
            seen_hard_block = True
    if seen_hard_block:
        final_decision = FinalDecision.HARD_BLOCK
    elif seen_warning:
        final_decision = FinalDecision.WARNING
    else:
        final_decision = FinalDecision.PASS
    return AggregatedRuleEvaluation(
        applied_rule_ids=applied_rule_ids,
        discrepancies=discrepancies,
        final_decision=final_decision,
        decision_reasons=decision_reasons or ["No rule discrepancies were emitted."],
    )


@dataclass(slots=True, frozen=True)
class _UDSameMailResolution:
    documents: list
    discrepancies: list[RuleDiscrepancy]
    decision_reasons: list[str]


def _resolve_same_mail_ud_documents(
    *,
    documents: list,
    saved_documents: list[SavedDocument],
    mail_id: str,
) -> _UDSameMailResolution:
    saved_documents_by_id = {
        document.saved_document_id: document
        for document in saved_documents
    }
    indexed_ud_documents = [
        (index, document)
        for index, document in enumerate(documents)
        if isinstance(document, UDDocumentPayload)
    ]
    kept_indexes = {index for index, _document in indexed_ud_documents}
    discrepancies: list[RuleDiscrepancy] = []
    decision_reasons: list[str] = []
    handled_indexes: set[int] = set()

    groups_by_number: dict[str, list[tuple[int, UDDocumentPayload]]] = defaultdict(list)
    for index, document in indexed_ud_documents:
        document_number = document.document_number.value.strip()
        if document_number:
            groups_by_number[document_number].append((index, document))
    for document_number, group in groups_by_number.items():
        if len(group) < 2:
            continue
        resolution = _resolve_same_mail_duplicate_group(
            group=group,
            duplicate_basis="document_number",
            duplicate_label=document_number,
            saved_documents_by_id=saved_documents_by_id,
            mail_id=mail_id,
        )
        discrepancies.extend(resolution["discrepancies"])
        decision_reasons.extend(resolution["decision_reasons"])
        handled_indexes.update(index for index, _document in group)
        kept_indexes.intersection_update(resolution["kept_indexes"])

    groups_by_filename: dict[str, list[tuple[int, UDDocumentPayload]]] = defaultdict(list)
    for index, document in indexed_ud_documents:
        if index in handled_indexes:
            continue
        saved_document = saved_documents_by_id.get(document.source_saved_document_id or "")
        filename = saved_document.normalized_filename if saved_document is not None else ""
        if filename:
            groups_by_filename[filename].append((index, document))
    for filename, group in groups_by_filename.items():
        if len(group) < 2:
            continue
        resolution = _resolve_same_mail_duplicate_group(
            group=group,
            duplicate_basis="filename",
            duplicate_label=filename,
            saved_documents_by_id=saved_documents_by_id,
            mail_id=mail_id,
        )
        discrepancies.extend(resolution["discrepancies"])
        decision_reasons.extend(resolution["decision_reasons"])
        kept_indexes.intersection_update(resolution["kept_indexes"])

    kept_documents = [
        document
        for index, document in enumerate(documents)
        if not isinstance(document, UDDocumentPayload) or index in kept_indexes
    ]
    return _UDSameMailResolution(
        documents=kept_documents,
        discrepancies=discrepancies,
        decision_reasons=decision_reasons,
    )


def _resolve_same_mail_duplicate_group(
    *,
    group: list[tuple[int, UDDocumentPayload]],
    duplicate_basis: str,
    duplicate_label: str,
    saved_documents_by_id: dict[str, SavedDocument],
    mail_id: str,
) -> dict[str, object]:
    sorted_group = sorted(group, key=lambda item: item[0])
    canonical_signature = _ud_duplicate_signature(sorted_group[0][1])
    conflicting = [
        (index, document)
        for index, document in sorted_group[1:]
        if _ud_duplicate_signature(document) != canonical_signature
    ]
    if conflicting:
        evidence = [
            _ud_duplicate_evidence(document, saved_documents_by_id)
            for _index, document in sorted_group
        ]
        return {
            "kept_indexes": {sorted_group[0][0]},
            "discrepancies": [
                RuleDiscrepancy(
                    code="ud_live_document_conflict",
                    severity=FinalDecision.HARD_BLOCK,
                    message=(
                        "Multiple UD/AM documents in the same mail were treated as duplicates but disagree "
                        "on required extracted evidence."
                    ),
                    subject_scope="mail",
                    target_ref=mail_id,
                    details={
                        "duplicate_basis": duplicate_basis,
                        "duplicate_label": duplicate_label,
                        "conflicting_document_evidence": evidence,
                    },
                )
            ],
            "decision_reasons": [
                f"UD duplicate resolution hard-blocked because duplicate {duplicate_basis} {duplicate_label} disagreed on required extracted evidence."
            ],
        }

    kept_index = sorted_group[0][0]
    ignored_documents = [
        _ud_duplicate_evidence(document, saved_documents_by_id)
        for index, document in sorted_group
        if index != kept_index
    ]
    return {
        "kept_indexes": {kept_index},
        "discrepancies": [
            RuleDiscrepancy(
                code="ud_duplicate_document_same_mail",
                severity=FinalDecision.WARNING,
                message="Duplicate UD/AM document evidence in the same mail was ignored after deterministic dedupe.",
                subject_scope="mail",
                target_ref=mail_id,
                details={
                    "duplicate_basis": duplicate_basis,
                    "duplicate_label": duplicate_label,
                    "kept_document": _ud_duplicate_evidence(sorted_group[0][1], saved_documents_by_id),
                    "ignored_documents": ignored_documents,
                },
            )
        ],
        "decision_reasons": [
            f"Ignored duplicate UD/AM document {duplicate_label} within the same mail."
        ],
    }


def _ud_duplicate_signature(document: UDDocumentPayload) -> tuple:
    quantity = (
        str(document.quantity.amount),
        document.quantity.unit,
    ) if document.quantity is not None else None
    quantity_by_unit = tuple(
        (unit, str(amount))
        for unit, amount in sorted(document.quantity_by_unit.items())
    )
    return (
        document.document_number.value.strip(),
        document.document_date.value.strip() if document.document_date is not None else "",
        document.lc_sc_number.value.strip(),
        document.lc_sc_date.value.strip() if document.lc_sc_date is not None else "",
        document.lc_sc_value.value.strip() if document.lc_sc_value is not None else "",
        document.lc_sc_value_currency or "",
        quantity,
        quantity_by_unit,
    )


def _ud_duplicate_evidence(
    document: UDDocumentPayload,
    saved_documents_by_id: dict[str, SavedDocument],
) -> dict[str, object]:
    saved_document = saved_documents_by_id.get(document.source_saved_document_id or "")
    return {
        "source_saved_document_id": document.source_saved_document_id,
        "normalized_filename": saved_document.normalized_filename if saved_document is not None else None,
        "document_number": document.document_number.value,
        "document_date": document.document_date.value if document.document_date is not None else None,
        "lc_sc_number": document.lc_sc_number.value,
        "lc_sc_date": document.lc_sc_date.value if document.lc_sc_date is not None else None,
        "lc_sc_value": document.lc_sc_value.value if document.lc_sc_value is not None else None,
        "lc_sc_value_currency": document.lc_sc_value_currency,
        "quantity": str(document.quantity.amount) if document.quantity is not None else None,
        "quantity_unit": document.quantity.unit if document.quantity is not None else None,
        "quantity_by_unit": {
            unit: str(amount)
            for unit, amount in sorted(document.quantity_by_unit.items())
        },
    }


def _extend_aggregated_rule_evaluation(
    *,
    aggregated: AggregatedRuleEvaluation,
    discrepancies: list[RuleDiscrepancy],
    decision_reasons: list[str],
) -> AggregatedRuleEvaluation:
    combined_discrepancies = list(aggregated.discrepancies)
    for discrepancy in discrepancies:
        if any(
            existing.code == discrepancy.code
            and existing.subject_scope == discrepancy.subject_scope
            and existing.target_ref == discrepancy.target_ref
            and existing.details == discrepancy.details
            for existing in combined_discrepancies
        ):
            continue
        combined_discrepancies.append(discrepancy)

    final_decision = aggregated.final_decision
    if final_decision != FinalDecision.HARD_BLOCK and any(
        discrepancy.severity == FinalDecision.WARNING
        for discrepancy in discrepancies
    ):
        final_decision = FinalDecision.WARNING
    if any(discrepancy.severity == FinalDecision.HARD_BLOCK for discrepancy in discrepancies):
        final_decision = FinalDecision.HARD_BLOCK

    return AggregatedRuleEvaluation(
        applied_rule_ids=list(aggregated.applied_rule_ids),
        discrepancies=combined_discrepancies,
        final_decision=final_decision,
        decision_reasons=list(aggregated.decision_reasons) + list(decision_reasons),
    )


def _same_run_ud_duplicate_warning(
    *,
    assembly,
    working_snapshot: WorkbookSnapshot | None,
    baseline_workbook_snapshot: WorkbookSnapshot | None,
    expected_document_number: str,
    mail_id: str,
) -> RuleDiscrepancy | None:
    if working_snapshot is None or baseline_workbook_snapshot is None or assembly.ud_selection is None:
        return None
    if assembly.ud_selection.get("final_decision") != "already_recorded":
        return None
    row_indexes = sorted(_selected_ud_selection_rows(assembly.ud_selection))
    if not row_indexes:
        return None
    working_mapping = resolve_ud_ip_exp_header_mapping(working_snapshot)
    baseline_mapping = resolve_ud_ip_exp_header_mapping(baseline_workbook_snapshot)
    if working_mapping is None or baseline_mapping is None:
        return None
    working_rows = {row.row_index: row for row in working_snapshot.rows}
    baseline_rows = {row.row_index: row for row in baseline_workbook_snapshot.rows}
    matches_working = all(
        working_rows.get(row_index) is not None
        and working_rows[row_index].values.get(working_mapping["ud_ip_shared"], "").strip() == expected_document_number
        for row_index in row_indexes
    )
    matches_baseline = all(
        baseline_rows.get(row_index) is not None
        and baseline_rows[row_index].values.get(baseline_mapping["ud_ip_shared"], "").strip() == expected_document_number
        for row_index in row_indexes
    )
    if not matches_working or matches_baseline:
        return None
    return RuleDiscrepancy(
        code="ud_duplicate_document_same_run",
        severity=FinalDecision.WARNING,
        message="Duplicate UD/AM document in the same run was ignored because an earlier mail already staged it.",
        subject_scope="mail",
        target_ref=mail_id,
        details={
            "document_number": expected_document_number,
            "row_indexes": row_indexes,
        },
    )


def _build_ud_multi_selection_report(selection_items: list[dict]) -> dict | None:
    if not selection_items:
        return None
    if len(selection_items) == 1:
        return selection_items[0]["selection"]
    handled_decisions = [
        item["selection"].get("final_decision")
        for item in selection_items
        if item["selection"] is not None
    ]
    all_handled = len(handled_decisions) == len(selection_items) and all(
        decision in {"selected", "already_recorded"}
        for decision in handled_decisions
    )
    all_selected = all(
        item["selection"] is not None
        and item["selection"].get("final_decision") == "selected"
        for item in selection_items
    )
    return {
        "document_count": len(selection_items),
        "final_decision": "selected" if all_selected else "already_recorded" if all_handled else "hard_block",
        "documents": selection_items,
    }


def _selected_ud_selection_rows(selection: dict | None) -> set[int]:
    if not selection:
        return set()
    rows: set[int] = set()
    for candidate in selection.get("candidates", []):
        if not isinstance(candidate, dict) or not candidate.get("selected"):
            continue
        for row_index in candidate.get("row_indexes", []):
            if isinstance(row_index, int):
                rows.add(row_index)
    return rows


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
    if descriptor.workflow_id not in {WorkflowId.EXPORT_LC_SC, WorkflowId.UD_IP_EXP}:
        return workbook_snapshot

    header_mapping = (
        resolve_export_header_mapping(workbook_snapshot)
        if descriptor.workflow_id == WorkflowId.EXPORT_LC_SC
        else resolve_ud_ip_exp_header_mapping(workbook_snapshot)
    )
    if header_mapping is None:
        return workbook_snapshot

    column_indices_by_key = dict(header_mapping)
    rows_by_index = {
        row.row_index: dict(row.values)
        for row in workbook_snapshot.rows
    }
    number_formats_by_index = {
        row.row_index: dict(row.number_formats)
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
        if operation.number_format:
            row_number_formats = number_formats_by_index.setdefault(operation.row_index, {})
            row_number_formats[column_index] = operation.number_format

    updated_rows = [
        WorkbookRow(
            row_index=row_index,
            values=rows_by_index[row_index],
            number_formats=number_formats_by_index.get(row_index, {}),
        )
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
    if descriptor.workflow_id == WorkflowId.EXPORT_LC_SC and isinstance(workflow_payload, ExportMailPayload):
        return list(workflow_payload.file_numbers)
    if descriptor.workflow_id == WorkflowId.UD_IP_EXP and isinstance(workflow_payload, UDIPEXPWorkflowPayload):
        if workflow_payload.export_payload is not None:
            return list(workflow_payload.export_payload.file_numbers)
    return []
