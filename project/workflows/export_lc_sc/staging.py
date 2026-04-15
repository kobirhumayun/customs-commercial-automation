from __future__ import annotations

from dataclasses import dataclass
import re

from project.models import FinalDecision, WriteOperation
from project.utils.ids import build_write_operation_id
from project.workbook import WorkbookSnapshot, resolve_export_header_mapping
from project.workflows.export_lc_sc.payloads import ExportMailPayload


@dataclass(slots=True, frozen=True)
class ExportStagingDiscrepancy:
    code: str
    severity: FinalDecision
    message: str
    details: dict


@dataclass(slots=True, frozen=True)
class ExportWriteStagingResult:
    staged_write_operations: list[WriteOperation]
    discrepancies: list[ExportStagingDiscrepancy]
    decision_reasons: list[str]


EXPORT_FIELD_VALUE_MAP = (
    ("file_no", lambda match: match.file_number),
    ("lc_sc_no", lambda match: match.canonical_row.lc_sc_number),
    ("buyer_name", lambda match: _format_buyer_name_for_sheet(match.canonical_row.buyer_name)),
    ("lc_issuing_bank", lambda match: _format_bank_name_for_sheet(match.canonical_row.notify_bank)),
    ("lc_issue_date", lambda match: match.canonical_row.lc_sc_date),
    ("export_amount", lambda match: match.canonical_row.current_lc_value),
    ("shipment_date", lambda match: match.canonical_row.ship_date),
    ("expiry_date", lambda match: match.canonical_row.expiry_date),
    ("quantity_fabrics", lambda match: match.canonical_row.lc_qty),
    ("lc_amnd_no", lambda match: match.canonical_row.amd_no),
    ("lc_amnd_date", lambda match: match.canonical_row.amd_date),
    ("lien_bank", lambda match: _format_lien_bank_for_sheet(match.canonical_row.nego_bank)),
    ("master_lc_no", lambda match: match.canonical_row.master_lc_no),
    ("master_lc_issue_date", lambda match: match.canonical_row.master_lc_date),
    ("bangladesh_bank_ref", lambda match: match.canonical_row.ship_remarks),
)
OPTIONAL_EXPORT_FIELD_VALUE_MAP = ()

REQUIRED_ERP_STAGE_FIELDS = ("current_lc_value", "lc_qty")
MTR_QUANTITY_NUMBER_FORMAT = '#,###.00 "Mtr"'


def stage_export_append_operations(
    *,
    run_id: str,
    mail_id: str,
    payload: ExportMailPayload,
    workbook_snapshot: WorkbookSnapshot | None,
    baseline_workbook_snapshot: WorkbookSnapshot | None = None,
) -> ExportWriteStagingResult:
    if workbook_snapshot is None:
        return ExportWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[],
            decision_reasons=["Workbook snapshot not supplied; write staging deferred."],
        )

    header_mapping = resolve_export_header_mapping(workbook_snapshot)
    if header_mapping is None:
        return ExportWriteStagingResult(
            staged_write_operations=[],
            discrepancies=[
                ExportStagingDiscrepancy(
                    code="workbook_header_mapping_invalid",
                    severity=FinalDecision.HARD_BLOCK,
                    message="Required workbook headers could not be resolved deterministically.",
                    details={"sheet_name": workbook_snapshot.sheet_name},
                )
            ],
            decision_reasons=["Workbook header mapping failed."],
        )

    existing_file_numbers = {
        row.values.get(header_mapping["file_no"], "").strip().upper()
        for row in workbook_snapshot.rows
    }
    baseline_existing_file_numbers = {
        row.values.get(header_mapping["file_no"], "").strip().upper()
        for row in (baseline_workbook_snapshot or workbook_snapshot).rows
    }
    next_row_index = _resolve_next_export_row_index(
        workbook_snapshot=workbook_snapshot,
        buyer_name_column_index=header_mapping["buyer_name"],
    )
    operation_index = 0
    staged_write_operations: list[WriteOperation] = []
    discrepancies: list[ExportStagingDiscrepancy] = []
    decision_reasons: list[str] = []

    for match in payload.erp_matches:
        if match.canonical_row is None:
            continue
        if match.file_number.upper() in existing_file_numbers:
            duplicate_reason = (
                f"Skipped workbook append for {match.file_number} because the file number already exists in the workbook."
                if match.file_number.upper() in baseline_existing_file_numbers
                else (
                    f"Skipped workbook append for {match.file_number} because the file number was already staged earlier in this run."
                )
            )
            decision_reasons.append(
                duplicate_reason
            )
            continue

        missing_fields = [
            field_name
            for field_name in REQUIRED_ERP_STAGE_FIELDS
            if not getattr(match.canonical_row, field_name).strip()
        ]
        if missing_fields:
            discrepancies.append(
                ExportStagingDiscrepancy(
                    code="export_required_erp_field_missing",
                    severity=FinalDecision.HARD_BLOCK,
                    message="Canonical ERP row is missing required fields for export workbook staging.",
                    details={
                        "file_number": match.file_number,
                        "missing_fields": missing_fields,
                    },
                )
            )
            continue

        for column_key, value_getter in EXPORT_FIELD_VALUE_MAP:
            number_format = (
                MTR_QUANTITY_NUMBER_FORMAT
                if column_key == "quantity_fabrics" and match.canonical_row.lc_unit.strip().upper() == "MTR"
                else None
            )
            staged_write_operations.append(
                WriteOperation(
                    write_operation_id=build_write_operation_id(
                        run_id=run_id,
                        mail_id=mail_id,
                        operation_index_within_mail=operation_index,
                        sheet_name=workbook_snapshot.sheet_name,
                        row_index=next_row_index,
                        column_key=column_key,
                    ),
                    run_id=run_id,
                    mail_id=mail_id,
                    operation_index_within_mail=operation_index,
                    sheet_name=workbook_snapshot.sheet_name,
                    row_index=next_row_index,
                    column_key=column_key,
                    expected_pre_write_value=None,
                    expected_post_write_value=value_getter(match),
                    row_eligibility_checks=[
                        "append_target_row_has_blank_buyer_name_or_is_new",
                        "target_cell_blank_by_construction",
                    ],
                    number_format=number_format,
                )
            )
            operation_index += 1
        for column_key, value_getter in OPTIONAL_EXPORT_FIELD_VALUE_MAP:
            if column_key not in header_mapping:
                continue
            staged_write_operations.append(
                WriteOperation(
                    write_operation_id=build_write_operation_id(
                        run_id=run_id,
                        mail_id=mail_id,
                        operation_index_within_mail=operation_index,
                        sheet_name=workbook_snapshot.sheet_name,
                        row_index=next_row_index,
                        column_key=column_key,
                    ),
                    run_id=run_id,
                    mail_id=mail_id,
                    operation_index_within_mail=operation_index,
                    sheet_name=workbook_snapshot.sheet_name,
                    row_index=next_row_index,
                    column_key=column_key,
                    expected_pre_write_value=None,
                    expected_post_write_value=value_getter(match),
                    row_eligibility_checks=[
                        "append_target_row_has_blank_buyer_name_or_is_new",
                        "target_cell_blank_by_construction",
                    ],
                    number_format=None,
                )
            )
            operation_index += 1
        decision_reasons.append(f"Staged workbook append for {match.file_number} at row {next_row_index}.")
        existing_file_numbers.add(match.file_number.upper())
        next_row_index += 1

    if not decision_reasons:
        decision_reasons.append("No workbook staging decisions were produced.")

    return ExportWriteStagingResult(
        staged_write_operations=staged_write_operations,
        discrepancies=discrepancies,
        decision_reasons=decision_reasons,
    )


def _resolve_next_export_row_index(
    *,
    workbook_snapshot: WorkbookSnapshot,
    buyer_name_column_index: int,
) -> int:
    sorted_rows = sorted(workbook_snapshot.rows, key=lambda row: row.row_index)
    for row in sorted_rows:
        if not row.values.get(buyer_name_column_index, "").strip():
            return row.row_index
    return max((row.row_index for row in workbook_snapshot.rows), default=2) + 1


def _format_buyer_name_for_sheet(value: str) -> str:
    return _title_case_words(value.replace("\\", ", "))


def _format_bank_name_for_sheet(value: str) -> str:
    return _title_case_words(value.replace("\\", ", "))


def _format_lien_bank_for_sheet(value: str) -> str:
    primary_value = value.split("\\", 1)[0].strip()
    return _title_case_words(primary_value)


def _title_case_words(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip())
    normalized = re.sub(r"\s*,\s*", ", ", normalized)
    return normalized.title()
