from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from project.models import FinalDecision, WriteOperation
from project.utils.ids import build_write_operation_id
from project.workbook import WorkbookSnapshot, resolve_ud_ip_exp_header_mapping
from project.workflows.ud_ip_exp.matching import UDAllocationResult
from project.workflows.ud_ip_exp.payloads import (
    UDDocumentPayload,
    UDIPEXPDocumentKind,
    UDIPEXPDocumentPayload,
    format_shared_column_entry,
    format_shared_column_values,
)

DATE_NUMBER_FORMAT = "dd/mm/yyyy"


@dataclass(slots=True, frozen=True)
class UDIPEXPStagingDiscrepancy:
    code: str
    severity: FinalDecision
    message: str
    details: dict


@dataclass(slots=True, frozen=True)
class UDIPEXPWriteStagingResult:
    staged_write_operations: list[WriteOperation]
    discrepancies: list[UDIPEXPStagingDiscrepancy]
    decision_reasons: list[str]


def stage_ud_shared_column_operations(
    *,
    run_id: str,
    mail_id: str,
    ud_document: UDDocumentPayload,
    allocation_result: UDAllocationResult,
    workbook_snapshot: WorkbookSnapshot | None,
    ud_receive_date: str | None = None,
    operation_index_start: int = 0,
) -> UDIPEXPWriteStagingResult:
    if workbook_snapshot is None:
        return UDIPEXPWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[],
            decision_reasons=["Workbook snapshot not supplied; UD write staging deferred."],
        )

    header_mapping = resolve_ud_ip_exp_header_mapping(workbook_snapshot)
    if header_mapping is None:
        return UDIPEXPWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[
                UDIPEXPStagingDiscrepancy(
                    code="workbook_header_mapping_invalid",
                    severity=FinalDecision.HARD_BLOCK,
                    message="Required UD/IP/EXP workbook headers could not be resolved deterministically.",
                    details={"sheet_name": workbook_snapshot.sheet_name},
                )
            ],
            decision_reasons=["UD/IP/EXP workbook header mapping failed."],
        )

    selected_candidate = next(
        (candidate for candidate in allocation_result.candidates if candidate.selected),
        None,
    )
    if allocation_result.final_decision == "already_recorded" and selected_candidate is not None:
        return UDIPEXPWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[],
            decision_reasons=[
                f"Skipped UD shared-column write for {ud_document.document_number.value} "
                "because it is already recorded in the workbook."
            ],
        )
    if allocation_result.final_decision != "selected" or selected_candidate is None:
        return UDIPEXPWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[
                UDIPEXPStagingDiscrepancy(
                    code=allocation_result.discrepancy_code or "ud_allocation_unresolved",
                    severity=FinalDecision.HARD_BLOCK,
                    message="UD allocation did not produce a selected workbook row combination.",
                    details={
                        "required_quantity": allocation_result.required_quantity,
                        "quantity_unit": allocation_result.quantity_unit,
                        "candidate_count": allocation_result.candidate_count,
                        "final_decision": allocation_result.final_decision,
                        "final_decision_reason": allocation_result.final_decision_reason,
                        "selected_candidate_id": allocation_result.selected_candidate_id,
                    },
                )
            ],
            decision_reasons=[
                "UD shared-column staging blocked because allocation did not select candidate rows."
            ],
        )

    target_values = _target_values(
        ud_document=ud_document,
        header_mapping=header_mapping,
        ud_receive_date=ud_receive_date,
    )
    if isinstance(target_values, UDIPEXPStagingDiscrepancy):
        return UDIPEXPWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[target_values],
            decision_reasons=["UD staging blocked because structured date target headers were not available."],
        )

    rows_by_index = {row.row_index: row for row in workbook_snapshot.rows}
    nonblank_targets = [
        {
            "row_index": row_index,
            "column_key": column_key,
            "observed_value": rows_by_index.get(row_index).values.get(header_mapping[column_key], "")
            if rows_by_index.get(row_index) is not None
            else None,
        }
        for row_index in selected_candidate.row_indexes
        for column_key in target_values
        if rows_by_index.get(row_index) is None
        or rows_by_index[row_index].values.get(header_mapping[column_key], "").strip()
    ]
    if nonblank_targets:
        return UDIPEXPWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[
                UDIPEXPStagingDiscrepancy(
                    code="ud_shared_column_nonblank_policy_unresolved",
                    severity=FinalDecision.HARD_BLOCK,
                    message=(
                        "Selected UD target row has a non-blank shared-column value; append and duplicate "
                        "handling policy is not yet confirmed for phase 1."
                    ),
                    details={
                        "selected_candidate_id": selected_candidate.candidate_id,
                        "target_column_keys": list(target_values),
                        "target_rows": nonblank_targets,
                    },
                )
            ],
            decision_reasons=[
                "UD shared-column staging blocked because selected target cells are not blank."
            ],
        )

    staged_write_operations: list[WriteOperation] = []
    operation_index = operation_index_start
    for row_index in selected_candidate.row_indexes:
        for column_key, post_write_value in target_values.items():
            staged_write_operations.append(
                WriteOperation(
                    write_operation_id=build_write_operation_id(
                        run_id=run_id,
                        mail_id=mail_id,
                        operation_index_within_mail=operation_index,
                        sheet_name=workbook_snapshot.sheet_name,
                        row_index=row_index,
                        column_key=column_key,
                    ),
                    run_id=run_id,
                    mail_id=mail_id,
                    operation_index_within_mail=operation_index,
                    sheet_name=workbook_snapshot.sheet_name,
                    row_index=row_index,
                    column_key=column_key,
                    expected_pre_write_value=None,
                    expected_post_write_value=post_write_value,
                    row_eligibility_checks=["target_cell_blank_by_construction"],
                    number_format=DATE_NUMBER_FORMAT
                    if column_key in {"ud_ip_date", "ud_recv_date"}
                    else None,
                )
            )
            operation_index += 1

    return UDIPEXPWriteStagingResult(
        staged_write_operations=staged_write_operations,
        discrepancies=[],
        decision_reasons=[
            f"Staged UD shared-column write for {ud_document.document_number.value} "
            f"to rows {selected_candidate.row_indexes}."
        ],
    )


def stage_ip_exp_shared_column_operations(
    *,
    run_id: str,
    mail_id: str,
    documents: list[UDIPEXPDocumentPayload],
    workbook_snapshot: WorkbookSnapshot | None,
    target_row_indexes: list[int] | None = None,
) -> UDIPEXPWriteStagingResult:
    ip_exp_documents = [
        document
        for document in documents
        if document.document_kind in {UDIPEXPDocumentKind.EXP, UDIPEXPDocumentKind.IP}
    ]
    if not ip_exp_documents:
        return UDIPEXPWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[],
            decision_reasons=["No IP/EXP document payloads supplied; no IP/EXP staging needed."],
        )

    if workbook_snapshot is None:
        return UDIPEXPWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[],
            decision_reasons=["Workbook snapshot not supplied; IP/EXP write staging deferred."],
        )

    header_mapping = resolve_ud_ip_exp_header_mapping(workbook_snapshot)
    if header_mapping is None:
        return UDIPEXPWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[
                UDIPEXPStagingDiscrepancy(
                    code="workbook_header_mapping_invalid",
                    severity=FinalDecision.HARD_BLOCK,
                    message="Required UD/IP/EXP workbook headers could not be resolved deterministically.",
                    details={"sheet_name": workbook_snapshot.sheet_name},
                )
            ],
            decision_reasons=["UD/IP/EXP workbook header mapping failed."],
        )

    unresolved_policies = [
        "IP/EXP workbook target-row matching keys are not confirmed.",
        "IP/EXP total value and quantity reconciliation is not fully specified.",
        "IP/EXP date column mapping and line-by-line date write policy are not confirmed.",
        "IP/EXP append, replacement, and duplicate handling for the shared column are not confirmed.",
    ]
    return UDIPEXPWriteStagingResult(
        staged_write_operations=[],
        discrepancies=[
            UDIPEXPStagingDiscrepancy(
                code="ip_exp_policy_unresolved",
                severity=FinalDecision.HARD_BLOCK,
                message=(
                    "IP/EXP shared-column staging is blocked because required matching, date, "
                    "total-check, and update policies are not fully confirmed."
                ),
                details={
                    "run_id": run_id,
                    "mail_id": mail_id,
                    "sheet_name": workbook_snapshot.sheet_name,
                    "target_column_key": "ud_ip_shared",
                    "target_column_index": header_mapping["ud_ip_shared"],
                    "target_row_indexes": list(target_row_indexes or []),
                    "proposed_shared_column_value": format_shared_column_values(ip_exp_documents),
                    "documents": [_document_summary(document) for document in ip_exp_documents],
                    "unresolved_policies": unresolved_policies,
                },
            )
        ],
        decision_reasons=[
            "IP/EXP staging blocked because matching, date, total-check, and shared-column update policies remain unresolved."
        ],
    )


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


def _target_values(
    *,
    ud_document: UDDocumentPayload,
    header_mapping: dict[str, int],
    ud_receive_date: str | None,
) -> dict[str, str] | UDIPEXPStagingDiscrepancy:
    values = {
        "ud_ip_shared": format_shared_column_entry(
            ud_document.document_kind,
            ud_document.document_number.value,
        )
    }
    structured_date_write_requested = (
        ud_document.lc_sc_value is not None
        or bool(ud_document.quantity_by_unit)
    )
    if not structured_date_write_requested:
        return values
    missing = [
        column_key
        for column_key in ("ud_ip_date", "ud_recv_date")
        if column_key not in header_mapping
    ]
    if missing:
        return UDIPEXPStagingDiscrepancy(
            code="workbook_header_mapping_invalid",
            severity=FinalDecision.HARD_BLOCK,
            message="Structured UD writes require UD date and receive-date workbook headers.",
            details={"missing_column_keys": missing},
        )
    if ud_document.document_date is None or not ud_document.document_date.value.strip():
        return UDIPEXPStagingDiscrepancy(
            code="ud_required_field_missing",
            severity=FinalDecision.HARD_BLOCK,
            message="Structured UD writes require a UD/AM document date.",
            details={"missing_fields": ["document_date"]},
        )
    if ud_receive_date is None or not ud_receive_date.strip():
        return UDIPEXPStagingDiscrepancy(
            code="ud_required_field_missing",
            severity=FinalDecision.HARD_BLOCK,
            message="Structured UD writes require a current workflow receive date.",
            details={"missing_fields": ["ud_receive_date"]},
        )
    values["ud_ip_date"] = _format_ddmmyyyy(ud_document.document_date.value)
    values["ud_recv_date"] = _format_ddmmyyyy(ud_receive_date)
    return values


def _format_ddmmyyyy(value: str) -> str:
    normalized = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            if fmt == "%Y-%m-%d":
                return date.fromisoformat(normalized).strftime("%d/%m/%Y")
            return datetime.strptime(normalized, fmt).date().strftime("%d/%m/%Y")
        except ValueError:
            continue
    return normalized
