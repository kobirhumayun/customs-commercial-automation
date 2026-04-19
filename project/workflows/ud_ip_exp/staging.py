from __future__ import annotations

from dataclasses import dataclass

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

    rows_by_index = {row.row_index: row for row in workbook_snapshot.rows}
    shared_column_index = header_mapping["ud_ip_shared"]
    nonblank_targets = [
        {
            "row_index": row_index,
            "observed_value": rows_by_index.get(row_index).values.get(shared_column_index, "")
            if rows_by_index.get(row_index) is not None
            else None,
        }
        for row_index in selected_candidate.row_indexes
        if rows_by_index.get(row_index) is None
        or rows_by_index[row_index].values.get(shared_column_index, "").strip()
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
                        "target_column_key": "ud_ip_shared",
                        "target_rows": nonblank_targets,
                    },
                )
            ],
            decision_reasons=[
                "UD shared-column staging blocked because selected target cells are not blank."
            ],
        )

    post_write_value = format_shared_column_entry(
        ud_document.document_kind,
        ud_document.document_number.value,
    )
    staged_write_operations: list[WriteOperation] = []
    for operation_index, row_index in enumerate(selected_candidate.row_indexes):
        staged_write_operations.append(
            WriteOperation(
                write_operation_id=build_write_operation_id(
                    run_id=run_id,
                    mail_id=mail_id,
                    operation_index_within_mail=operation_index,
                    sheet_name=workbook_snapshot.sheet_name,
                    row_index=row_index,
                    column_key="ud_ip_shared",
                ),
                run_id=run_id,
                mail_id=mail_id,
                operation_index_within_mail=operation_index,
                sheet_name=workbook_snapshot.sheet_name,
                row_index=row_index,
                column_key="ud_ip_shared",
                expected_pre_write_value=None,
                expected_post_write_value=post_write_value,
                row_eligibility_checks=["target_cell_blank_by_construction"],
            )
        )

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
        "quantity": str(document.quantity.amount) if document.quantity is not None else None,
        "quantity_unit": document.quantity.unit if document.quantity is not None else None,
        "source_saved_document_id": document.source_saved_document_id,
    }
