from project.models import FinalDecision
from project.models.enums import RuleStage
from project.rules.types import RuleDefinition, RuleDiscrepancy, RuleEvaluationResult
from project.workflows.ud_ip_exp.payloads import UDDocumentPayload, UDIPEXPWorkflowPayload

RULE_PACK_ID = "ud_ip_exp.default"
RULE_PACK_VERSION = "1.0.0"


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
        if document.quantity is None:
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
    if allocation is not None and allocation.final_decision == "selected":
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.ud_allocation_selected.v1",
            outcome=FinalDecision.PASS,
            rationale="UD allocation selected deterministic workbook candidate rows.",
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


RULE_DEFINITIONS = (
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
