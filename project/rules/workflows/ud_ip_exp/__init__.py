from project.models import FinalDecision
from project.models.enums import RuleStage
from project.rules.types import RuleDefinition, RuleDiscrepancy, RuleEvaluationResult
from project.workflows.ud_ip_exp.payloads import (
    UDDocumentPayload,
    UDIPEXPDocumentKind,
    UDIPEXPDocumentPayload,
    UDIPEXPWorkflowPayload,
    format_shared_column_values,
)

RULE_PACK_ID = "ud_ip_exp.default"
RULE_PACK_VERSION = "1.0.0"


def evaluate_ud_file_number_present(context) -> RuleEvaluationResult:
    payload = _require_ud_ip_exp_payload(context.workflow_payload)
    export_payload = payload.export_payload
    if export_payload is None:
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.file_number_present.v1",
            outcome=FinalDecision.PASS,
            rationale="ERP-family payload was not supplied for this isolated UD validation path.",
        )
    if export_payload.file_numbers:
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.file_number_present.v1",
            outcome=FinalDecision.PASS,
            rationale="UD/IP/EXP mail body yielded canonical file numbers for ERP family resolution.",
        )
    return RuleEvaluationResult(
        rule_id="ud_ip_exp.file_number_present.v1",
        outcome=FinalDecision.HARD_BLOCK,
        rationale="UD/IP/EXP processing requires at least one canonical file number from the mail body.",
        discrepancies=(
            RuleDiscrepancy(
                code="ud_file_number_missing",
                severity=FinalDecision.HARD_BLOCK,
                message="No canonical file numbers were extracted from the UD/IP/EXP mail body.",
                subject_scope="mail",
                target_ref=context.mail.mail_id,
                details={
                    "mail_id": context.mail.mail_id,
                    "body_text": context.mail.body_text,
                },
            ),
        ),
    )


def evaluate_ud_erp_rows_present(context) -> RuleEvaluationResult:
    payload = _require_ud_ip_exp_payload(context.workflow_payload)
    export_payload = payload.export_payload
    if export_payload is None:
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.erp_rows_present.v1",
            outcome=FinalDecision.PASS,
            rationale="ERP-family payload was not supplied for this isolated UD validation path.",
        )
    missing = [match.file_number for match in export_payload.erp_matches if match.canonical_row is None]
    if not missing:
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.erp_rows_present.v1",
            outcome=FinalDecision.PASS,
            rationale="All extracted UD/IP/EXP file numbers resolved to canonical ERP rows.",
        )
    return RuleEvaluationResult(
        rule_id="ud_ip_exp.erp_rows_present.v1",
        outcome=FinalDecision.HARD_BLOCK,
        rationale="Every extracted UD/IP/EXP file number must resolve to a canonical ERP row.",
        discrepancies=(
            RuleDiscrepancy(
                code="ud_erp_row_missing",
                severity=FinalDecision.HARD_BLOCK,
                message="One or more extracted UD/IP/EXP file numbers did not resolve to ERP rows.",
                subject_scope="mail",
                target_ref=context.mail.mail_id,
                details={
                    "mail_id": context.mail.mail_id,
                    "missing_file_numbers": missing,
                },
            ),
        ),
    )


def evaluate_ud_family_consistent(context) -> RuleEvaluationResult:
    payload = _require_ud_ip_exp_payload(context.workflow_payload)
    export_payload = payload.export_payload
    if export_payload is None:
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.family_consistent.v1",
            outcome=FinalDecision.PASS,
            rationale="ERP-family payload was not supplied for this isolated UD validation path.",
        )
    canonical_rows = [match.canonical_row for match in export_payload.erp_matches if match.canonical_row is not None]
    if not canonical_rows:
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.family_consistent.v1",
            outcome=FinalDecision.PASS,
            rationale="UD/IP/EXP family consistency awaits ERP row resolution.",
        )
    if export_payload.verified_family is not None:
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.family_consistent.v1",
            outcome=FinalDecision.PASS,
            rationale="Resolved UD/IP/EXP ERP rows belong to one LC/SC family.",
        )
    return RuleEvaluationResult(
        rule_id="ud_ip_exp.family_consistent.v1",
        outcome=FinalDecision.HARD_BLOCK,
        rationale="Resolved UD/IP/EXP ERP rows must belong to one LC/SC family.",
        discrepancies=(
            RuleDiscrepancy(
                code="ud_family_inconsistent",
                severity=FinalDecision.HARD_BLOCK,
                message="Resolved UD/IP/EXP ERP rows do not share one LC/SC number, buyer, and LC/SC date.",
                subject_scope="mail",
                target_ref=context.mail.mail_id,
                details={
                    "mail_id": context.mail.mail_id,
                    "families": [
                        {
                            "file_number": match.file_number,
                            "lc_sc_number": match.canonical_row.lc_sc_number if match.canonical_row else None,
                            "buyer_name": match.canonical_row.buyer_name if match.canonical_row else None,
                            "lc_sc_date": match.canonical_row.lc_sc_date if match.canonical_row else None,
                        }
                        for match in export_payload.erp_matches
                    ],
                },
            ),
        ),
    )


def evaluate_ud_document_present(context) -> RuleEvaluationResult:
    payload = _require_ud_ip_exp_payload(context.workflow_payload)
    if _ud_documents(payload):
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.ud_document_present.v1",
            outcome=FinalDecision.PASS,
            rationale="At least one UD document payload is available for deterministic UD processing.",
        )
    return RuleEvaluationResult(
        rule_id="ud_ip_exp.ud_document_present.v1",
        outcome=FinalDecision.HARD_BLOCK,
        rationale="UD processing requires at least one UD document payload.",
        discrepancies=(
            RuleDiscrepancy(
                code="ud_required_document_missing",
                severity=FinalDecision.HARD_BLOCK,
                message="No UD document payload was available for deterministic UD processing.",
                subject_scope="mail",
                target_ref=context.mail.mail_id,
                details={
                    "mail_id": context.mail.mail_id,
                    "document_count": len(payload.documents),
                    "document_kinds": [document.document_kind.value for document in payload.documents],
                },
            ),
        ),
    )


def evaluate_ud_required_fields_present(context) -> RuleEvaluationResult:
    payload = _require_ud_ip_exp_payload(context.workflow_payload)
    missing_by_document = []
    for index, document in enumerate(_ud_documents(payload)):
        missing_fields = []
        if not document.document_number.value.strip():
            missing_fields.append("document_number")
        if document.document_date is None or not document.document_date.value.strip():
            missing_fields.append("document_date")
        if not document.lc_sc_number.value.strip():
            missing_fields.append("lc_sc_number")
        if _is_structured_bgmea_ud(document):
            if document.lc_sc_date is None or not document.lc_sc_date.value.strip():
                missing_fields.append("lc_sc_date")
            if document.lc_sc_value is None or not document.lc_sc_value.value.strip():
                missing_fields.append("lc_sc_value")
            if not document.quantity_by_unit:
                missing_fields.append("quantity_by_unit")
        elif document.quantity is None:
            missing_fields.append("quantity")
        if missing_fields:
            missing_by_document.append(
                {
                    "document_index": index,
                    "document_number": document.document_number.value,
                    "missing_fields": missing_fields,
                }
            )

    if not missing_by_document:
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.ud_required_fields_present.v1",
            outcome=FinalDecision.PASS,
            rationale="UD document payloads contain the confirmed required fields.",
        )
    return RuleEvaluationResult(
        rule_id="ud_ip_exp.ud_required_fields_present.v1",
        outcome=FinalDecision.HARD_BLOCK,
        rationale="UD document payloads must contain all confirmed required fields.",
        discrepancies=(
            RuleDiscrepancy(
                code="ud_required_field_missing",
                severity=FinalDecision.HARD_BLOCK,
                message="One or more UD document payloads are missing confirmed required fields.",
                subject_scope="mail",
                target_ref=context.mail.mail_id,
                details={
                    "mail_id": context.mail.mail_id,
                    "missing_by_document": missing_by_document,
                },
            ),
        ),
    )


def evaluate_ud_allocation_selected(context) -> RuleEvaluationResult:
    payload = _require_ud_ip_exp_payload(context.workflow_payload)
    allocation = payload.ud_allocation_result
    if allocation is not None and allocation.final_decision in {"selected", "already_recorded"}:
        rationale = (
            "UD workbook rows already contain the requested UD document values."
            if allocation.final_decision == "already_recorded"
            else "UD allocation selected deterministic workbook candidate rows."
        )
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.ud_allocation_selected.v1",
            outcome=FinalDecision.PASS,
            rationale=rationale,
        )

    details = {
        "mail_id": context.mail.mail_id,
        "allocation_present": allocation is not None,
        "final_decision": allocation.final_decision if allocation is not None else None,
        "final_decision_reason": allocation.final_decision_reason if allocation is not None else None,
        "candidate_count": allocation.candidate_count if allocation is not None else 0,
        "selected_candidate_id": allocation.selected_candidate_id if allocation is not None else None,
    }
    return RuleEvaluationResult(
        rule_id="ud_ip_exp.ud_allocation_selected.v1",
        outcome=FinalDecision.HARD_BLOCK,
        rationale="UD allocation must select deterministic workbook candidate rows before staging.",
        discrepancies=(
            RuleDiscrepancy(
                code=(allocation.discrepancy_code if allocation is not None and allocation.discrepancy_code else "ud_allocation_unresolved"),
                severity=FinalDecision.HARD_BLOCK,
                message="UD allocation did not produce a selected workbook row combination.",
                subject_scope="mail",
                target_ref=context.mail.mail_id,
                details=details,
            ),
        ),
    )


def evaluate_ip_exp_policy_resolved(context) -> RuleEvaluationResult:
    payload = _require_ud_ip_exp_payload(context.workflow_payload)
    ip_exp_documents = _ip_exp_documents(payload)
    if not ip_exp_documents:
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.ip_exp_policy_resolved.v1",
            outcome=FinalDecision.PASS,
            rationale="No IP/EXP document payloads are present for unresolved IP/EXP policy checks.",
        )

    unresolved_policies = [
        "IP/EXP workbook target-row matching keys are not confirmed.",
        "IP/EXP total value and quantity reconciliation is not fully specified.",
        "IP/EXP date column mapping and line-by-line date write policy are not confirmed.",
        "IP/EXP append, replacement, and duplicate handling for the shared column are not confirmed.",
    ]
    return RuleEvaluationResult(
        rule_id="ud_ip_exp.ip_exp_policy_resolved.v1",
        outcome=FinalDecision.HARD_BLOCK,
        rationale="IP/EXP document payloads are present but required processing policies remain unresolved.",
        discrepancies=(
            RuleDiscrepancy(
                code="ip_exp_policy_unresolved",
                severity=FinalDecision.HARD_BLOCK,
                message=(
                    "IP/EXP documents were supplied, but matching, date, total-check, "
                    "and shared-column update policies are not fully confirmed."
                ),
                subject_scope="mail",
                target_ref=context.mail.mail_id,
                details={
                    "mail_id": context.mail.mail_id,
                    "document_count": len(ip_exp_documents),
                    "document_kinds": [document.document_kind.value for document in ip_exp_documents],
                    "proposed_shared_column_value": format_shared_column_values(ip_exp_documents),
                    "documents": [_document_summary(document) for document in ip_exp_documents],
                    "unresolved_policies": unresolved_policies,
                },
            ),
        ),
    )


def _require_ud_ip_exp_payload(payload) -> UDIPEXPWorkflowPayload:
    if not isinstance(payload, UDIPEXPWorkflowPayload):
        raise ValueError("UD/IP/EXP rules require a UDIPEXPWorkflowPayload")
    return payload


def _ud_documents(payload: UDIPEXPWorkflowPayload) -> list[UDDocumentPayload]:
    return [
        document
        for document in payload.documents
        if isinstance(document, UDDocumentPayload)
    ]


def _ip_exp_documents(payload: UDIPEXPWorkflowPayload) -> list[UDIPEXPDocumentPayload]:
    return [
        document
        for document in payload.documents
        if document.document_kind in {UDIPEXPDocumentKind.EXP, UDIPEXPDocumentKind.IP}
    ]


def _document_summary(document: UDIPEXPDocumentPayload) -> dict:
    return {
        "document_kind": document.document_kind.value,
        "document_number": document.document_number.value,
        "document_date": document.document_date.value if document.document_date is not None else None,
        "lc_sc_number": document.lc_sc_number.value,
        "lc_sc_date": document.lc_sc_date.value if document.lc_sc_date is not None else None,
        "lc_sc_value": document.lc_sc_value.value if document.lc_sc_value is not None else None,
        "quantity": str(document.quantity.amount) if document.quantity is not None else None,
        "quantity_unit": document.quantity.unit if document.quantity is not None else None,
        "quantity_by_unit": {
            unit: str(amount)
            for unit, amount in document.quantity_by_unit.items()
        },
        "source_saved_document_id": document.source_saved_document_id,
    }


def _is_structured_bgmea_ud(document: UDIPEXPDocumentPayload) -> bool:
    value = document.document_number.value.upper()
    return "/UD/" in value or "/AM/" in value


RULE_DEFINITIONS = (
    RuleDefinition(
        rule_id="ud_ip_exp.erp_rows_present.v1",
        stage=RuleStage.WORKFLOW_STANDARD,
        evaluator=evaluate_ud_erp_rows_present,
    ),
    RuleDefinition(
        rule_id="ud_ip_exp.family_consistent.v1",
        stage=RuleStage.WORKFLOW_STANDARD,
        evaluator=evaluate_ud_family_consistent,
    ),
    RuleDefinition(
        rule_id="ud_ip_exp.file_number_present.v1",
        stage=RuleStage.WORKFLOW_STANDARD,
        evaluator=evaluate_ud_file_number_present,
    ),
    RuleDefinition(
        rule_id="ud_ip_exp.ip_exp_policy_resolved.v1",
        stage=RuleStage.WORKFLOW_STANDARD,
        evaluator=evaluate_ip_exp_policy_resolved,
    ),
    RuleDefinition(
        rule_id="ud_ip_exp.ud_allocation_selected.v1",
        stage=RuleStage.WORKFLOW_STANDARD,
        evaluator=evaluate_ud_allocation_selected,
    ),
    RuleDefinition(
        rule_id="ud_ip_exp.ud_document_present.v1",
        stage=RuleStage.WORKFLOW_STANDARD,
        evaluator=evaluate_ud_document_present,
    ),
    RuleDefinition(
        rule_id="ud_ip_exp.ud_required_fields_present.v1",
        stage=RuleStage.WORKFLOW_STANDARD,
        evaluator=evaluate_ud_required_fields_present,
    ),
)
