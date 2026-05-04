from project.models import FinalDecision
from project.models.enums import RuleStage
from project.rules.types import RuleDefinition, RuleDiscrepancy, RuleEvaluationResult
from project.erp.normalization import normalize_lc_sc_date, normalize_lc_sc_number
from project.workflows.ud_ip_exp.parsing import is_bgmea_ud_am_document_number
from project.workflows.ud_ip_exp.payloads import (
    UDDocumentPayload,
    UDIPEXPDocumentKind,
    UDIPEXPDocumentPayload,
    UDIPEXPWorkflowPayload,
    format_shared_column_values,
)

RULE_PACK_ID = "ud_ip_exp.default"
RULE_PACK_VERSION = "1.1.0"


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
    if payload.documents:
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.ud_document_present.v1",
            outcome=FinalDecision.PASS,
            rationale="At least one deterministic UD/IP/EXP document payload is available for processing.",
        )
    return RuleEvaluationResult(
        rule_id="ud_ip_exp.ud_document_present.v1",
        outcome=FinalDecision.HARD_BLOCK,
        rationale="UD/IP/EXP processing requires at least one deterministic document payload.",
        discrepancies=(
            RuleDiscrepancy(
                code="ud_required_document_missing",
                severity=FinalDecision.HARD_BLOCK,
                message="No deterministic UD/IP/EXP document payload was available for processing.",
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
    if not _ud_documents(payload):
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.ud_required_fields_present.v1",
            outcome=FinalDecision.PASS,
            rationale="No UD documents are present, so UD-only field validation is not required.",
        )
    missing_by_document = []
    invalid_by_document = []
    for index, document in enumerate(_ud_documents(payload)):
        missing_fields = []
        invalid_fields = []
        if not document.document_number.value.strip():
            missing_fields.append("document_number")
        elif not is_bgmea_ud_am_document_number(document.document_number.value):
            invalid_fields.append("document_number")
        if document.document_date is None or not document.document_date.value.strip():
            missing_fields.append("document_date")
        elif normalize_lc_sc_date(document.document_date.value) is None:
            invalid_fields.append("document_date")
        if not document.lc_sc_number.value.strip():
            missing_fields.append("lc_sc_number")
        if _is_structured_bgmea_ud(document):
            if document.lc_sc_date is None or not document.lc_sc_date.value.strip():
                missing_fields.append("lc_sc_date")
            elif normalize_lc_sc_date(document.lc_sc_date.value) is None:
                invalid_fields.append("lc_sc_date")
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
        if invalid_fields:
            invalid_by_document.append(
                {
                    "document_index": index,
                    "document_number": document.document_number.value,
                    "invalid_fields": invalid_fields,
                }
            )

    if not missing_by_document and not invalid_by_document:
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.ud_required_fields_present.v1",
            outcome=FinalDecision.PASS,
            rationale="UD document payloads contain the confirmed required fields.",
        )
    discrepancies = []
    if missing_by_document:
        discrepancies.append(
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
            )
        )
    if invalid_by_document:
        discrepancies.append(
            RuleDiscrepancy(
                code="ud_required_field_invalid",
                severity=FinalDecision.HARD_BLOCK,
                message="One or more UD document payloads contain invalid confirmed required fields.",
                subject_scope="mail",
                target_ref=context.mail.mail_id,
                details={
                    "mail_id": context.mail.mail_id,
                    "invalid_by_document": invalid_by_document,
                    "expected_document_number_pattern": "BGMEA/<office>/<UD-or-AM>/...",
                },
            )
        )
    return RuleEvaluationResult(
        rule_id="ud_ip_exp.ud_required_fields_present.v1",
        outcome=FinalDecision.HARD_BLOCK,
        rationale="UD document payloads must contain all confirmed required fields.",
        discrepancies=tuple(discrepancies),
    )


def evaluate_ud_allocation_selected(context) -> RuleEvaluationResult:
    payload = _require_ud_ip_exp_payload(context.workflow_payload)
    if not _ud_documents(payload):
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.ud_allocation_selected.v1",
            outcome=FinalDecision.PASS,
            rationale="No UD documents are present, so UD row allocation is not required.",
        )
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


def evaluate_ip_exp_mail_shape_valid(context) -> RuleEvaluationResult:
    payload = _require_ud_ip_exp_payload(context.workflow_payload)
    ip_exp_documents = _ip_exp_documents(payload)
    if not ip_exp_documents:
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.ip_exp_mail_shape_valid.v1",
            outcome=FinalDecision.PASS,
            rationale="No IP/EXP document payloads are present for IP/EXP mail-shape checks.",
        )
    issues = _ip_exp_mail_shape_issues(payload)
    document_kinds = [document.document_kind.value for document in payload.documents]
    if not issues:
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.ip_exp_mail_shape_valid.v1",
            outcome=FinalDecision.PASS,
            rationale="The mail's IP/EXP document composition matches the conservative phase-1 contract.",
        )
    return RuleEvaluationResult(
        rule_id="ud_ip_exp.ip_exp_mail_shape_valid.v1",
        outcome=FinalDecision.HARD_BLOCK,
        rationale="The mail's IP/EXP document composition violates the conservative phase-1 contract.",
        discrepancies=(
            RuleDiscrepancy(
                code="ud_ip_exp_mail_shape_invalid",
                severity=FinalDecision.HARD_BLOCK,
                message="The mail's deterministic IP/EXP document composition is invalid for phase 1.",
                subject_scope="mail",
                target_ref=context.mail.mail_id,
                details={
                    "mail_id": context.mail.mail_id,
                    "document_count": len(payload.documents),
                    "document_kinds": document_kinds,
                    "proposed_shared_column_value": format_shared_column_values(ip_exp_documents),
                    "documents": [_document_summary(document) for document in ip_exp_documents],
                    "issues": issues,
                },
            ),
        ),
    )


def evaluate_ip_exp_required_fields_present(context) -> RuleEvaluationResult:
    payload = _require_ud_ip_exp_payload(context.workflow_payload)
    ip_exp_documents = _ip_exp_documents(payload)
    if not ip_exp_documents:
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.ip_exp_required_fields_present.v1",
            outcome=FinalDecision.PASS,
            rationale="No IP/EXP document payloads are present for IP/EXP field validation.",
        )
    if _ip_exp_mail_shape_issues(payload):
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.ip_exp_required_fields_present.v1",
            outcome=FinalDecision.PASS,
            rationale="IP/EXP field validation is skipped because the mail-shape contract already failed.",
        )
    missing_by_document = []
    invalid_by_document = []
    normalized_dates: set[str] = set()
    expected_family = _expected_family_lc_sc_number(payload)
    for index, document in enumerate(ip_exp_documents):
        missing_fields = []
        invalid_fields = []
        if not document.document_number.value.strip():
            missing_fields.append("document_number")
        if document.document_date is None or not document.document_date.value.strip():
            missing_fields.append("document_date")
        else:
            normalized_date = normalize_lc_sc_date(document.document_date.value)
            if normalized_date is None:
                invalid_fields.append("document_date")
            else:
                normalized_dates.add(normalized_date)
        if not document.lc_sc_number.value.strip():
            missing_fields.append("lc_sc_number")
        elif expected_family is not None:
            normalized_lc_sc = normalize_lc_sc_number(document.lc_sc_number.value)
            if normalized_lc_sc != expected_family:
                invalid_fields.append("lc_sc_number")
        if missing_fields:
            missing_by_document.append(
                {
                    "document_index": index,
                    "document_kind": document.document_kind.value,
                    "document_number": document.document_number.value,
                    "missing_fields": missing_fields,
                }
            )
        if invalid_fields:
            invalid_by_document.append(
                {
                    "document_index": index,
                    "document_kind": document.document_kind.value,
                    "document_number": document.document_number.value,
                    "invalid_fields": invalid_fields,
                    "expected_family_lc_sc_number": expected_family,
                }
            )
    if len(normalized_dates) > 1:
        invalid_by_document.append(
            {
                "document_index": None,
                "document_kind": "mail",
                "document_number": None,
                "invalid_fields": ["document_date"],
                "normalized_document_dates": sorted(normalized_dates),
                "reason": "same_mail_document_dates_must_match",
            }
        )
    if not missing_by_document and not invalid_by_document:
        return RuleEvaluationResult(
            rule_id="ud_ip_exp.ip_exp_required_fields_present.v1",
            outcome=FinalDecision.PASS,
            rationale="IP/EXP document payloads contain the confirmed phase-1 required fields.",
        )
    discrepancies = []
    if missing_by_document:
        discrepancies.append(
            RuleDiscrepancy(
                code="ip_exp_required_field_missing",
                severity=FinalDecision.HARD_BLOCK,
                message="One or more IP/EXP document payloads are missing confirmed required fields.",
                subject_scope="mail",
                target_ref=context.mail.mail_id,
                details={
                    "mail_id": context.mail.mail_id,
                    "missing_by_document": missing_by_document,
                },
            )
        )
    if invalid_by_document:
        discrepancies.append(
            RuleDiscrepancy(
                code="ip_exp_required_field_invalid",
                severity=FinalDecision.HARD_BLOCK,
                message="One or more IP/EXP document payloads contain invalid or contradictory required fields.",
                subject_scope="mail",
                target_ref=context.mail.mail_id,
                details={
                    "mail_id": context.mail.mail_id,
                    "invalid_by_document": invalid_by_document,
                },
            )
        )
    return RuleEvaluationResult(
        rule_id="ud_ip_exp.ip_exp_required_fields_present.v1",
        outcome=FinalDecision.HARD_BLOCK,
        rationale="IP/EXP document payloads must satisfy the conservative phase-1 field contract before staging.",
        discrepancies=tuple(discrepancies),
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


def _expected_family_lc_sc_number(payload: UDIPEXPWorkflowPayload) -> str | None:
    export_payload = payload.export_payload
    if export_payload is None:
        return None
    for match in export_payload.erp_matches:
        if match.canonical_row is not None:
            return normalize_lc_sc_number(match.canonical_row.lc_sc_number)
    return None


def _ip_exp_mail_shape_issues(payload: UDIPEXPWorkflowPayload) -> list[str]:
    ip_exp_documents = _ip_exp_documents(payload)
    if not ip_exp_documents:
        return []
    issues: list[str] = []
    if _ud_documents(payload):
        issues.append("UD and IP/EXP documents cannot be mixed in one mail.")
    exp_count = sum(1 for document in ip_exp_documents if document.document_kind == UDIPEXPDocumentKind.EXP)
    ip_count = sum(1 for document in ip_exp_documents if document.document_kind == UDIPEXPDocumentKind.IP)
    if ip_count and not exp_count:
        issues.append("IP documents require an EXP document in the same mail.")
    if exp_count > 1:
        issues.append("Phase 1 allows at most one deterministic EXP payload per mail.")
    if ip_count > 1:
        issues.append("Phase 1 allows at most one deterministic IP payload per mail.")
    return issues


def _is_structured_bgmea_ud(document: UDIPEXPDocumentPayload) -> bool:
    return (
        document.lc_sc_date is not None
        or document.lc_sc_value is not None
        or bool(document.quantity_by_unit)
    )


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
        rule_id="ud_ip_exp.ip_exp_mail_shape_valid.v1",
        stage=RuleStage.WORKFLOW_STANDARD,
        evaluator=evaluate_ip_exp_mail_shape_valid,
    ),
    RuleDefinition(
        rule_id="ud_ip_exp.ip_exp_required_fields_present.v1",
        stage=RuleStage.WORKFLOW_STANDARD,
        evaluator=evaluate_ip_exp_required_fields_present,
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
