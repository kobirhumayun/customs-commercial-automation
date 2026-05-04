from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from project.erp.normalization import normalize_lc_sc_number
from project.erp.normalization import normalize_lc_sc_date
from project.models import FinalDecision, WriteOperation
from project.utils.ids import build_write_operation_id
from project.workbook import WorkbookSnapshot, resolve_ud_ip_exp_header_mapping
from project.workflows.ud_ip_exp.matching import UDAllocationResult
from project.workflows.ud_ip_exp.parsing import is_bgmea_ud_am_document_number
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
        if _has_ud_target_row_conflict(
            nonblank_targets=nonblank_targets,
            target_values=target_values,
            expected_document_date=ud_document.document_date.value if ud_document.document_date is not None else None,
        ):
            return UDIPEXPWriteStagingResult(
                staged_write_operations=[],
                discrepancies=[
                    UDIPEXPStagingDiscrepancy(
                        code="ud_target_row_conflict",
                        severity=FinalDecision.HARD_BLOCK,
                        message=(
                            "Selected UD target row already contains a different UD/AM document "
                            "assignment or a conflicting UD date."
                        ),
                        details={
                            "selected_candidate_id": selected_candidate.candidate_id,
                            "target_column_keys": list(target_values),
                            "target_rows": nonblank_targets,
                            "expected_shared_value": target_values.get("ud_ip_shared"),
                            "expected_document_date": ud_document.document_date.value
                            if ud_document.document_date is not None
                            else None,
                        },
                    )
                ],
                decision_reasons=[
                    "UD shared-column staging blocked because selected rows already belong to a different UD/AM assignment."
                ],
            )
        if _selected_rows_already_match_ud_targets(
            nonblank_targets=nonblank_targets,
            target_values=target_values,
            expected_document_date=ud_document.document_date.value if ud_document.document_date is not None else None,
        ):
            return UDIPEXPWriteStagingResult(
                staged_write_operations=[],
                discrepancies=[],
                decision_reasons=[
                    f"Skipped UD shared-column write for {ud_document.document_number.value} "
                    "because it is already recorded in the workbook."
                ],
            )
        return UDIPEXPWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[
                UDIPEXPStagingDiscrepancy(
                    code="ud_shared_column_nonblank_policy_unresolved",
                    severity=FinalDecision.HARD_BLOCK,
                    message=(
                        "Selected UD target row has a non-blank target cell; phase 1 does not write to "
                        "any workbook target cell that already contains a value."
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
    family_lc_sc_number: str | None = None,
    ip_exp_receive_date: str | None = None,
    operation_index_start: int = 0,
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

    target_values = _ip_exp_target_values(
        documents=ip_exp_documents,
        header_mapping=header_mapping,
        ip_exp_receive_date=ip_exp_receive_date,
    )
    if isinstance(target_values, UDIPEXPStagingDiscrepancy):
        return UDIPEXPWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[target_values],
            decision_reasons=["IP/EXP staging blocked because required shared-column/date targets were not available."],
        )

    resolved_target_row_indexes = list(target_row_indexes or [])
    if not resolved_target_row_indexes and family_lc_sc_number is not None:
        resolved_target_row_indexes = _collect_family_row_indexes(
            workbook_snapshot=workbook_snapshot,
            header_mapping=header_mapping,
            family_lc_sc_number=family_lc_sc_number,
        )
    if not resolved_target_row_indexes:
        return UDIPEXPWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[
                UDIPEXPStagingDiscrepancy(
                    code="ip_exp_family_row_missing",
                    severity=FinalDecision.HARD_BLOCK,
                    message="IP/EXP family-wide staging requires existing workbook rows in the verified ERP family.",
                    details={
                        "run_id": run_id,
                        "mail_id": mail_id,
                        "sheet_name": workbook_snapshot.sheet_name,
                        "family_lc_sc_number": family_lc_sc_number,
                        "documents": [_document_summary(document) for document in ip_exp_documents],
                    },
                )
            ],
            decision_reasons=["IP/EXP staging blocked because the verified ERP family has no workbook rows."],
        )

    rows_by_index = {row.row_index: row for row in workbook_snapshot.rows}
    if _family_rows_already_match_ip_exp_targets(
        rows_by_index=rows_by_index,
        target_row_indexes=resolved_target_row_indexes,
        header_mapping=header_mapping,
        target_values=target_values,
    ):
        return UDIPEXPWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[],
            decision_reasons=[
                "Skipped IP/EXP family-wide write because the requested shared-column value is already recorded in the workbook."
            ],
        )

    nonblank_targets = _family_nonblank_targets(
        rows_by_index=rows_by_index,
        target_row_indexes=resolved_target_row_indexes,
        header_mapping=header_mapping,
        target_values=target_values,
    )
    if nonblank_targets:
        return UDIPEXPWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[
                UDIPEXPStagingDiscrepancy(
                    code="ip_exp_target_row_conflict",
                    severity=FinalDecision.HARD_BLOCK,
                    message=(
                        "One or more family-wide IP/EXP target rows already contain a different non-blank "
                        "shared/date value, so phase 1 staging cannot append, merge, or replace them."
                    ),
                    details={
                        "run_id": run_id,
                        "mail_id": mail_id,
                        "sheet_name": workbook_snapshot.sheet_name,
                        "target_column_keys": list(target_values),
                        "target_row_indexes": resolved_target_row_indexes,
                        "target_rows": nonblank_targets,
                        "proposed_shared_column_value": target_values["ud_ip_shared"],
                        "documents": [_document_summary(document) for document in ip_exp_documents],
                    },
                )
            ],
            decision_reasons=[
                "IP/EXP staging blocked because at least one family-wide target cell is already populated with a different value."
            ],
        )

    staged_write_operations: list[WriteOperation] = []
    operation_index = operation_index_start
    for row_index in resolved_target_row_indexes:
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
            f"Staged IP/EXP family-wide write to rows {resolved_target_row_indexes}."
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
    if not is_bgmea_ud_am_document_number(ud_document.document_number.value):
        return UDIPEXPStagingDiscrepancy(
            code="ud_required_field_invalid",
            severity=FinalDecision.HARD_BLOCK,
            message="UD workbook writes require an extracted BGMEA UD/AM document number.",
            details={
                "invalid_fields": ["document_number"],
                "document_number": ud_document.document_number.value,
                "expected_pattern": "BGMEA/<office>/<UD-or-AM>/...",
            },
        )
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
    ud_ip_date = _format_ddmmyyyy(ud_document.document_date.value)
    recv_date = _format_ddmmyyyy(ud_receive_date)
    invalid_fields = []
    if ud_ip_date is None:
        invalid_fields.append("document_date")
    if recv_date is None:
        invalid_fields.append("ud_receive_date")
    if invalid_fields:
        return UDIPEXPStagingDiscrepancy(
            code="ud_required_field_invalid",
            severity=FinalDecision.HARD_BLOCK,
            message="Structured UD writes require parseable UD/AM and receive dates.",
            details={
                "invalid_fields": invalid_fields,
                "document_date": ud_document.document_date.value,
                "ud_receive_date": ud_receive_date,
            },
        )
    values["ud_ip_date"] = ud_ip_date
    values["ud_recv_date"] = recv_date
    return values


def _ip_exp_target_values(
    *,
    documents: list[UDIPEXPDocumentPayload],
    header_mapping: dict[str, int],
    ip_exp_receive_date: str | None,
) -> dict[str, str] | UDIPEXPStagingDiscrepancy:
    missing = [
        column_key
        for column_key in ("ud_ip_date", "ud_recv_date")
        if column_key not in header_mapping
    ]
    if missing:
        return UDIPEXPStagingDiscrepancy(
            code="workbook_header_mapping_invalid",
            severity=FinalDecision.HARD_BLOCK,
            message="IP/EXP family-wide writes require UD/IP date and receive-date workbook headers.",
            details={"missing_column_keys": missing},
        )
    if ip_exp_receive_date is None or not ip_exp_receive_date.strip():
        return UDIPEXPStagingDiscrepancy(
            code="ip_exp_required_field_missing",
            severity=FinalDecision.HARD_BLOCK,
            message="IP/EXP family-wide writes require a current workflow receive date.",
            details={"missing_fields": ["ip_exp_receive_date"]},
        )
    normalized_dates = sorted(
        {
            normalized
            for normalized in (
                normalize_lc_sc_date(document.document_date.value)
                if document.document_date is not None and document.document_date.value.strip()
                else None
                for document in documents
            )
            if normalized is not None
        }
    )
    if len(normalized_dates) != 1:
        return UDIPEXPStagingDiscrepancy(
            code="ip_exp_required_field_invalid",
            severity=FinalDecision.HARD_BLOCK,
            message="IP/EXP family-wide writes require one normalized document date across the mail.",
            details={
                "invalid_fields": ["document_date"],
                "document_dates": [
                    document.document_date.value if document.document_date is not None else None
                    for document in documents
                ],
            },
        )
    recv_date = _format_ddmmyyyy(ip_exp_receive_date)
    if recv_date is None:
        return UDIPEXPStagingDiscrepancy(
            code="ip_exp_required_field_invalid",
            severity=FinalDecision.HARD_BLOCK,
            message="IP/EXP family-wide writes require a parseable workflow receive date.",
            details={
                "invalid_fields": ["ip_exp_receive_date"],
                "ip_exp_receive_date": ip_exp_receive_date,
            },
        )
    return {
        "ud_ip_shared": format_shared_column_values(documents),
        "ud_ip_date": date.fromisoformat(normalized_dates[0]).strftime("%d/%m/%Y"),
        "ud_recv_date": recv_date,
    }


def _format_ddmmyyyy(value: str | object) -> str | None:
    normalized_date = normalize_lc_sc_date(value)
    if normalized_date is None:
        return None
    return date.fromisoformat(normalized_date).strftime("%d/%m/%Y")


def _has_ud_target_row_conflict(
    *,
    nonblank_targets: list[dict[str, object]],
    target_values: dict[str, str],
    expected_document_date: str | None,
) -> bool:
    expected_shared = target_values.get("ud_ip_shared", "").strip()
    expected_ud_date = normalize_lc_sc_date(expected_document_date or "")
    for target in nonblank_targets:
        column_key = str(target.get("column_key", ""))
        observed_value = str(target.get("observed_value", "") or "").strip()
        if column_key == "ud_ip_shared" and observed_value != expected_shared:
            return True
        if (
            column_key == "ud_ip_date"
            and observed_value
            and expected_ud_date is not None
            and normalize_lc_sc_date(observed_value) != expected_ud_date
        ):
            return True
    return False


def _selected_rows_already_match_ud_targets(
    *,
    nonblank_targets: list[dict[str, object]],
    target_values: dict[str, str],
    expected_document_date: str | None,
) -> bool:
    expected_shared = target_values.get("ud_ip_shared", "").strip()
    expected_ud_date = normalize_lc_sc_date(expected_document_date or "")
    saw_shared = False
    for target in nonblank_targets:
        column_key = str(target.get("column_key", ""))
        observed_value = str(target.get("observed_value", "") or "").strip()
        if column_key == "ud_ip_shared":
            if observed_value != expected_shared:
                return False
            saw_shared = True
        elif column_key == "ud_ip_date" and observed_value and expected_ud_date is not None:
            if normalize_lc_sc_date(observed_value) != expected_ud_date:
                return False
    return saw_shared


def _collect_family_row_indexes(
    *,
    workbook_snapshot: WorkbookSnapshot,
    header_mapping: dict[str, int],
    family_lc_sc_number: str,
) -> list[int]:
    expected = normalize_lc_sc_number(family_lc_sc_number or "")
    if expected is None:
        return []
    return [
        row.row_index
        for row in sorted(workbook_snapshot.rows, key=lambda item: item.row_index)
        if normalize_lc_sc_number(row.values.get(header_mapping["lc_sc_no"], "")) == expected
    ]


def _family_rows_already_match_ip_exp_targets(
    *,
    rows_by_index: dict[int, object],
    target_row_indexes: list[int],
    header_mapping: dict[str, int],
    target_values: dict[str, str],
) -> bool:
    expected_shared = target_values["ud_ip_shared"].strip()
    expected_date = normalize_lc_sc_date(target_values["ud_ip_date"])
    if expected_date is None:
        return False
    for row_index in target_row_indexes:
        row = rows_by_index.get(row_index)
        if row is None:
            return False
        shared_value = str(row.values.get(header_mapping["ud_ip_shared"], "") or "").strip()
        if shared_value != expected_shared:
            return False
        observed_date = normalize_lc_sc_date(row.values.get(header_mapping["ud_ip_date"], ""))
        if observed_date != expected_date:
            return False
    return True


def _family_nonblank_targets(
    *,
    rows_by_index: dict[int, object],
    target_row_indexes: list[int],
    header_mapping: dict[str, int],
    target_values: dict[str, str],
) -> list[dict[str, object]]:
    targets: list[dict[str, object]] = []
    for row_index in target_row_indexes:
        row = rows_by_index.get(row_index)
        for column_key in target_values:
            observed_value = (
                row.values.get(header_mapping[column_key], "")
                if row is not None
                else None
            )
            if str(observed_value or "").strip():
                targets.append(
                    {
                        "row_index": row_index,
                        "column_key": column_key,
                        "observed_value": observed_value,
                    }
                )
    return targets
