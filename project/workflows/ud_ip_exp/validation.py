from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from project.erp.normalization import normalize_lc_sc_date
from project.models import EmailMessage, FinalDecision, WorkflowId
from project.rules import AggregatedRuleEvaluation, LoadedRulePack, evaluate_rule_pack
from project.workbook import WorkbookSnapshot, resolve_ud_ip_exp_header_mapping
from project.utils.time import validate_timezone
from project.workflows.ud_ip_exp.matching import (
    UDAllocationResult,
    allocate_structured_ud_rows,
    allocate_ud_rows,
    collect_ud_candidate_rows,
)
from project.workflows.ud_ip_exp.payloads import (
    UDDocumentPayload,
    UDIPEXPDocumentPayload,
    UDIPEXPWorkflowPayload,
)
from project.workflows.ud_ip_exp.reporting import build_ud_selection_report
from project.workflows.ud_ip_exp.staging import (
    UDIPEXPWriteStagingResult,
    stage_ud_shared_column_operations,
)
from project.workflows.validation import WorkflowValidationContext


@dataclass(slots=True, frozen=True)
class UDValidationAssemblyResult:
    workflow_payload: UDIPEXPWorkflowPayload
    rule_evaluation: AggregatedRuleEvaluation
    staging_result: UDIPEXPWriteStagingResult
    ud_selection: dict | None


def assemble_ud_validation(
    *,
    run_id: str,
    mail: EmailMessage,
    rule_pack: LoadedRulePack,
    ud_document: UDDocumentPayload,
    workbook_snapshot: WorkbookSnapshot | None,
    documents: list[UDIPEXPDocumentPayload] | None = None,
    saved_documents: list | None = None,
    state_timezone: str = "Asia/Dhaka",
    export_payload=None,
    ud_receive_date: str | None = None,
) -> UDValidationAssemblyResult:
    allocation_result = _build_allocation_result(
        ud_document=ud_document,
        workbook_snapshot=workbook_snapshot,
        export_payload=export_payload,
    )
    workflow_payload = UDIPEXPWorkflowPayload(
        documents=list(documents or [ud_document]),
        saved_documents=list(saved_documents or []),
        ud_allocation_result=allocation_result,
        export_payload=export_payload,
    )
    rule_evaluation = evaluate_rule_pack(
        WorkflowValidationContext(
            run_id=run_id,
            workflow_id=WorkflowId.UD_IP_EXP,
            rule_pack_id=rule_pack.rule_pack_id,
            rule_pack_version=rule_pack.rule_pack_version,
            state_timezone=state_timezone,
            operator_context=None,
            mail=mail,
            workflow_payload=workflow_payload,
        ),
        rule_pack,
    )

    staging_result = _stage_after_rules(
        run_id=run_id,
        mail=mail,
        ud_document=ud_document,
        allocation_result=allocation_result,
        workbook_snapshot=workbook_snapshot,
        rule_evaluation=rule_evaluation,
        ud_receive_date=ud_receive_date,
    )
    return UDValidationAssemblyResult(
        workflow_payload=workflow_payload,
        rule_evaluation=rule_evaluation,
        staging_result=staging_result,
        ud_selection=(
            build_ud_selection_report(allocation_result)
            if allocation_result is not None
            else None
        ),
    )


def _build_allocation_result(
    *,
    ud_document: UDDocumentPayload,
    workbook_snapshot: WorkbookSnapshot | None,
    export_payload=None,
) -> UDAllocationResult | None:
    if workbook_snapshot is None:
        return None
    header_mapping = resolve_ud_ip_exp_header_mapping(workbook_snapshot)
    if header_mapping is None:
        return None
    structured_result = _build_structured_allocation_result(
        ud_document=ud_document,
        workbook_snapshot=workbook_snapshot,
        header_mapping=header_mapping,
        export_payload=export_payload,
    )
    if structured_result is not None:
        return structured_result

    if ud_document.quantity is None:
        return None

    candidate_rows = collect_ud_candidate_rows(
        workbook_snapshot=workbook_snapshot,
        lc_sc_number=ud_document.lc_sc_number.value,
        quantity_unit=ud_document.quantity.unit,
        header_mapping=header_mapping,
    )
    return allocate_ud_rows(
        required_quantity=ud_document.quantity.amount,
        quantity_unit=ud_document.quantity.unit,
        candidate_rows=candidate_rows,
    )


def _stage_after_rules(
    *,
    run_id: str,
    mail: EmailMessage,
    ud_document: UDDocumentPayload,
    allocation_result: UDAllocationResult | None,
    workbook_snapshot: WorkbookSnapshot | None,
    rule_evaluation: AggregatedRuleEvaluation,
    ud_receive_date: str | None,
) -> UDIPEXPWriteStagingResult:
    if allocation_result is not None and rule_evaluation.final_decision != FinalDecision.HARD_BLOCK:
        return stage_ud_shared_column_operations(
            run_id=run_id,
            mail_id=mail.mail_id,
            ud_document=ud_document,
            allocation_result=allocation_result,
            workbook_snapshot=workbook_snapshot,
            ud_receive_date=ud_receive_date,
        )

    if (
        allocation_result is None
        and ud_document.quantity is not None
        and workbook_snapshot is not None
        and resolve_ud_ip_exp_header_mapping(workbook_snapshot) is None
    ):
        return stage_ud_shared_column_operations(
            run_id=run_id,
            mail_id=mail.mail_id,
            ud_document=ud_document,
            allocation_result=UDAllocationResult(
                required_quantity=_format_decimal(ud_document.quantity.amount),
                quantity_unit=ud_document.quantity.unit,
                candidate_count=0,
                candidates=[],
                final_decision="hard_block",
                final_decision_reason="workbook_header_mapping_invalid",
            ),
            workbook_snapshot=workbook_snapshot,
            ud_receive_date=ud_receive_date,
        )

    return UDIPEXPWriteStagingResult(
        staged_write_operations=[],
        discrepancies=[],
        decision_reasons=["UD staging skipped because rule evaluation did not pass."],
    )


def _format_decimal(value: Decimal) -> str:
    normalized = format(value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _build_structured_allocation_result(
    *,
    ud_document: UDDocumentPayload,
    workbook_snapshot: WorkbookSnapshot,
    header_mapping: dict[str, int],
    export_payload,
) -> UDAllocationResult | None:
    if (
        ud_document.lc_sc_date is None
        or ud_document.lc_sc_value is None
        or not ud_document.quantity_by_unit
    ):
        return None
    erp_row = _canonical_erp_row(export_payload)
    if erp_row is None:
        return None
    document_lc_date = normalize_lc_sc_date(ud_document.lc_sc_date.value)
    erp_lc_date = normalize_lc_sc_date(erp_row.lc_sc_date)
    if document_lc_date != erp_lc_date:
        return UDAllocationResult(
            required_quantity="",
            quantity_unit="",
            candidate_count=0,
            candidates=[],
            final_decision="hard_block",
            final_decision_reason="ud_lc_date_mismatch",
            discrepancy_code="ud_lc_date_mismatch",
        )
    return allocate_structured_ud_rows(
        workbook_snapshot=workbook_snapshot,
        lc_sc_number=erp_row.lc_sc_number,
        lc_sc_value=Decimal(str(ud_document.lc_sc_value.value)),
        quantity_by_unit=ud_document.quantity_by_unit,
        header_mapping=header_mapping,
    )


def _canonical_erp_row(export_payload):
    if export_payload is None:
        return None
    for match in getattr(export_payload, "erp_matches", []):
        if match.canonical_row is not None:
            return match.canonical_row
    return None


def workflow_date_from_started_at(started_at_utc: str, *, state_timezone: str) -> str:
    normalized = started_at_utc.replace("Z", "+00:00")
    value = datetime.fromisoformat(normalized)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    local_date = value.astimezone(validate_timezone(state_timezone)).date()
    return local_date.strftime("%d/%m/%Y")
