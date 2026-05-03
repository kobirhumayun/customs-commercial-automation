from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any
import json
import webbrowser

from project.models import MailOutcomeRecord, RunReport, WorkflowId
from project.reporting.schemas import REPORT_SCHEMA_VERSION
from project.storage import RunArtifactPaths
from project.storage.artifacts import atomic_write_text
from project.utils.json import pretty_json_dumps
from project.utils.time import utc_timestamp
from project.workbook import HeaderMappingSpec, WorkbookSnapshot, resolve_header_mapping

PRINT_ANNOTATION_CHECKLIST_SCHEMA_ID = "print_annotation_checklist"
LC_SC_HEADER_SPEC = HeaderMappingSpec(
    "lc_sc",
    "L/C & S/C No.",
    ("L/C No.", "LC/SC No.", "LC No."),
)
BANGLADESH_BANK_REF_HEADER_SPEC = HeaderMappingSpec(
    "bangladesh_bank_ref",
    "Bangladesh Bank Ref.",
    ("Bangladesh Bank Ref",),
)


class PrintAnnotationChecklistError(ValueError):
    def __init__(self, *, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(slots=True, frozen=True)
class PrintAnnotationChecklistBuildResult:
    payload: dict[str, Any]
    html: str


def build_print_annotation_checklist(
    *,
    run_report: RunReport,
    mail_outcomes: list[MailOutcomeRecord],
    print_batches: list,
    workbook_snapshot: WorkbookSnapshot | None,
    live_workbook_path: Path | None = None,
) -> PrintAnnotationChecklistBuildResult:
    if run_report.workflow_id != WorkflowId.UD_IP_EXP:
        raise PrintAnnotationChecklistError(
            code="print_annotation_generation_failed",
            message="Print-annotation checklist generation is currently supported only for ud_ip_exp.",
            details={"workflow_id": run_report.workflow_id.value},
        )
    if workbook_snapshot is None:
        raise PrintAnnotationChecklistError(
            code="print_annotation_generation_failed",
            message="Print-annotation checklist generation requires a workbook snapshot.",
            details={"run_id": run_report.run_id},
        )

    planned_row_indexes = _planned_row_indexes(mail_outcomes)
    sl_no_column_index = _resolve_sl_no_column_index(workbook_snapshot)
    lc_sc_column_index = _resolve_required_column_index(
        workbook_snapshot=workbook_snapshot,
        spec=LC_SC_HEADER_SPEC,
        error_message="Print-annotation checklist generation requires one deterministic L/C & S/C No. workbook header.",
    )
    bangladesh_bank_ref_column_index = _resolve_required_column_index(
        workbook_snapshot=workbook_snapshot,
        spec=BANGLADESH_BANK_REF_HEADER_SPEC,
        error_message="Print-annotation checklist generation requires one deterministic Bangladesh Bank Ref. workbook header.",
    )
    sl_no_values_by_row = _resolve_column_values_by_row(
        workbook_snapshot=workbook_snapshot,
        column_index=sl_no_column_index,
        live_workbook_path=live_workbook_path,
        row_indexes=planned_row_indexes,
        error_code="print_annotation_sl_no_unresolved",
        error_message="The live workbook SL.No. display text could not be read for a selected checklist row.",
    )
    lc_sc_values_by_row = _resolve_column_values_by_row(
        workbook_snapshot=workbook_snapshot,
        column_index=lc_sc_column_index,
        live_workbook_path=live_workbook_path,
        row_indexes=planned_row_indexes,
        error_code="print_annotation_generation_failed",
        error_message="The live workbook L/C & S/C No. display text could not be read for a selected checklist row.",
    )
    bangladesh_bank_ref_values_by_row = _resolve_column_values_by_row(
        workbook_snapshot=workbook_snapshot,
        column_index=bangladesh_bank_ref_column_index,
        live_workbook_path=live_workbook_path,
        row_indexes=planned_row_indexes,
        error_code="print_annotation_generation_failed",
        error_message="The live workbook Bangladesh Bank Ref. display text could not be read for a selected checklist row.",
    )
    rows = _build_checklist_rows(
        run_report=run_report,
        mail_outcomes=mail_outcomes,
        print_batches=print_batches,
        sl_no_values_by_row=sl_no_values_by_row,
        lc_sc_values_by_row=lc_sc_values_by_row,
        bangladesh_bank_ref_values_by_row=bangladesh_bank_ref_values_by_row,
    )
    payload = {
        "schema_id": PRINT_ANNOTATION_CHECKLIST_SCHEMA_ID,
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run_report.run_id,
        "workflow_id": run_report.workflow_id.value,
        "generated_at_utc": utc_timestamp(),
        "print_group_order": [batch.print_group_id for batch in print_batches],
        "checklist_row_count": len(rows),
        "rows": rows,
    }
    return PrintAnnotationChecklistBuildResult(
        payload=payload,
        html=_build_print_annotation_html(payload),
    )


def persist_print_annotation_checklist(
    *,
    artifact_paths: RunArtifactPaths,
    result: PrintAnnotationChecklistBuildResult,
) -> None:
    atomic_write_text(
        artifact_paths.print_annotation_checklist_json_path,
        pretty_json_dumps(result.payload),
    )
    atomic_write_text(
        artifact_paths.print_annotation_checklist_html_path,
        result.html,
    )


def validate_print_annotation_checklist(
    *,
    artifact_paths: RunArtifactPaths,
    run_report: RunReport,
    print_batches: list,
    mail_outcomes: list[MailOutcomeRecord] | None = None,
) -> None:
    if run_report.workflow_id != WorkflowId.UD_IP_EXP:
        return
    json_path = artifact_paths.print_annotation_checklist_json_path
    html_path = artifact_paths.print_annotation_checklist_html_path
    if not json_path.exists() or not html_path.exists():
        raise PrintAnnotationChecklistError(
            code="print_annotation_checklist_missing_or_invalid",
            message="The mandatory print-annotation checklist artifact is missing for this ud_ip_exp run.",
            details={
                "json_path": str(json_path),
                "html_path": str(html_path),
            },
        )

    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrintAnnotationChecklistError(
            code="print_annotation_checklist_missing_or_invalid",
            message="The persisted print-annotation checklist JSON could not be read.",
            details={"json_path": str(json_path), "error": str(exc)},
        ) from exc

    if not isinstance(payload, dict):
        raise PrintAnnotationChecklistError(
            code="print_annotation_checklist_missing_or_invalid",
            message="The persisted print-annotation checklist artifact must be a JSON object.",
            details={"json_path": str(json_path)},
        )
    if str(payload.get("schema_id", "")).strip() != PRINT_ANNOTATION_CHECKLIST_SCHEMA_ID:
        raise PrintAnnotationChecklistError(
            code="print_annotation_checklist_missing_or_invalid",
            message="The persisted print-annotation checklist artifact did not match the expected schema.",
            details={"json_path": str(json_path)},
        )
    if str(payload.get("run_id", "")).strip() != run_report.run_id:
        raise PrintAnnotationChecklistError(
            code="print_annotation_checklist_missing_or_invalid",
            message="The persisted print-annotation checklist artifact did not match the active run id.",
            details={
                "expected_run_id": run_report.run_id,
                "observed_run_id": payload.get("run_id"),
            },
        )
    if str(payload.get("workflow_id", "")).strip() != run_report.workflow_id.value:
        raise PrintAnnotationChecklistError(
            code="print_annotation_checklist_missing_or_invalid",
            message="The persisted print-annotation checklist artifact did not match the active workflow id.",
            details={
                "expected_workflow_id": run_report.workflow_id.value,
                "observed_workflow_id": payload.get("workflow_id"),
            },
        )

    observed_group_order = [str(value) for value in payload.get("print_group_order", [])]
    expected_group_order = [batch.print_group_id for batch in print_batches]
    if observed_group_order != expected_group_order:
        raise PrintAnnotationChecklistError(
            code="print_annotation_checklist_missing_or_invalid",
            message="The persisted print-annotation checklist order does not match the current print plan.",
            details={
                "expected_print_group_order": expected_group_order,
                "observed_print_group_order": observed_group_order,
            },
        )

    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        raise PrintAnnotationChecklistError(
            code="print_annotation_checklist_missing_or_invalid",
            message="The persisted print-annotation checklist rows must be a JSON array.",
            details={"json_path": str(json_path)},
        )
    observed_hashes = [str(row.get("document_path_hash", "")).strip() for row in rows if isinstance(row, dict)]
    expected_hashes = _expected_checklist_document_hashes(
        print_batches=print_batches,
        mail_outcomes=mail_outcomes or [],
    )
    if observed_hashes != expected_hashes:
        raise PrintAnnotationChecklistError(
            code="print_annotation_checklist_missing_or_invalid",
            message="The persisted print-annotation checklist rows do not match the current print-plan documents.",
            details={
                "expected_document_path_hashes": expected_hashes,
                "observed_document_path_hashes": observed_hashes,
            },
        )


def open_print_annotation_checklist_in_browser(*, artifact_paths: RunArtifactPaths) -> None:
    html_path = artifact_paths.print_annotation_checklist_html_path
    if not html_path.exists():
        raise FileNotFoundError(str(html_path))
    if not webbrowser.open(html_path.resolve().as_uri()):
        raise RuntimeError(f"Browser open request was not acknowledged for {html_path}")


def _build_checklist_rows(
    *,
    run_report: RunReport,
    mail_outcomes: list[MailOutcomeRecord],
    print_batches: list,
    sl_no_values_by_row: dict[int, str],
    lc_sc_values_by_row: dict[int, str],
    bangladesh_bank_ref_values_by_row: dict[int, str],
) -> list[dict[str, Any]]:
    outcomes_by_mail_id = {outcome.mail_id: outcome for outcome in mail_outcomes}
    sequence = 1
    rows: list[dict[str, Any]] = []
    for batch in print_batches:
        outcome = outcomes_by_mail_id.get(batch.mail_id)
        if outcome is None:
            raise PrintAnnotationChecklistError(
                code="print_annotation_generation_failed",
                message="A planned print group did not resolve to its mail outcome record.",
                details={"print_group_id": batch.print_group_id, "mail_id": batch.mail_id},
            )
        saved_documents_by_path = {
            str(document.get("destination_path", "")).strip(): document
            for document in outcome.saved_documents
            if str(document.get("destination_path", "")).strip()
        }
        selection_by_saved_document_id = _selection_by_saved_document_id(outcome)
        for document_path, document_path_hash in zip(batch.document_paths, batch.document_path_hashes):
            saved_document = saved_documents_by_path.get(document_path)
            if not isinstance(saved_document, dict):
                raise PrintAnnotationChecklistError(
                    code="print_annotation_generation_failed",
                    message="A planned print document did not resolve to persisted saved-document evidence.",
                    details={
                        "print_group_id": batch.print_group_id,
                        "mail_id": batch.mail_id,
                        "document_path": document_path,
                    },
                )
            saved_document_id = str(saved_document.get("saved_document_id", "")).strip()
            selection_item = selection_by_saved_document_id.get(saved_document_id)
            if selection_item is None and not _saved_document_requires_checklist_row(
                saved_document=saved_document,
                selection_mapping=selection_by_saved_document_id,
            ):
                continue
            if selection_item is None:
                raise PrintAnnotationChecklistError(
                    code="print_annotation_generation_failed",
                    message="A planned print document did not resolve to UD row-selection evidence for checklist generation.",
                    details={
                        "print_group_id": batch.print_group_id,
                        "mail_id": batch.mail_id,
                        "saved_document_id": saved_document_id,
                        "document_path": document_path,
                    },
                )
            row_indexes = _selected_row_indexes(selection_item.get("selection"))
            if not row_indexes:
                raise PrintAnnotationChecklistError(
                    code="print_annotation_generation_failed",
                    message="A planned print document had no selected workbook rows for checklist generation.",
                    details={
                        "print_group_id": batch.print_group_id,
                        "mail_id": batch.mail_id,
                        "saved_document_id": saved_document_id,
                    },
                )
            sl_no_values: list[str] = []
            for row_index in row_indexes:
                sl_no_value = sl_no_values_by_row.get(row_index, "").strip()
                if not sl_no_value:
                    raise PrintAnnotationChecklistError(
                        code="print_annotation_sl_no_unresolved",
                        message="A selected checklist row did not yield a readable workbook SL.No. value.",
                        details={
                            "mail_id": batch.mail_id,
                            "saved_document_id": saved_document_id,
                            "row_index": row_index,
                        },
                    )
                sl_no_values.append(sl_no_value)
            lc_sc_value = _join_distinct_row_values(
                row_indexes=row_indexes,
                values_by_row=lc_sc_values_by_row,
                field_label="L/C & S/C No.",
                allow_blank=False,
                error_code="print_annotation_generation_failed",
                mail_id=batch.mail_id,
                saved_document_id=saved_document_id,
            )
            bangladesh_bank_ref = _join_distinct_row_values(
                row_indexes=row_indexes,
                values_by_row=bangladesh_bank_ref_values_by_row,
                field_label="Bangladesh Bank Ref.",
                allow_blank=True,
                error_code="print_annotation_generation_failed",
                mail_id=batch.mail_id,
                saved_document_id=saved_document_id,
            )

            rows.append(
                {
                    "print_sequence": sequence,
                    "print_group_id": batch.print_group_id,
                    "mail_id": batch.mail_id,
                    "workflow_id": run_report.workflow_id.value,
                    "ud_or_amendment_no": _selection_document_number(selection_item, saved_document),
                    "lc_sc": lc_sc_value,
                    "bangladesh_bank_ref": bangladesh_bank_ref,
                    "sl_no_values": sl_no_values,
                    "mail_subject": outcome.subject_raw,
                    "document_filename": str(saved_document.get("normalized_filename", "")),
                    "saved_document_id": saved_document_id,
                    "document_path_hash": document_path_hash,
                    "row_indexes": row_indexes,
                }
            )
            sequence += 1
    return rows


def _expected_checklist_document_hashes(
    *,
    print_batches: list,
    mail_outcomes: list[MailOutcomeRecord],
) -> list[str]:
    if not mail_outcomes:
        return [
            document_hash
            for batch in print_batches
            for document_hash in batch.document_path_hashes
        ]
    outcomes_by_mail_id = {outcome.mail_id: outcome for outcome in mail_outcomes}
    expected_hashes: list[str] = []
    for batch in print_batches:
        outcome = outcomes_by_mail_id.get(batch.mail_id)
        if outcome is None:
            expected_hashes.extend(batch.document_path_hashes)
            continue
        saved_documents_by_path = {
            str(document.get("destination_path", "")).strip(): document
            for document in outcome.saved_documents
            if str(document.get("destination_path", "")).strip()
        }
        selection_by_saved_document_id = _selection_by_saved_document_id(outcome)
        for document_path, document_hash in zip(batch.document_paths, batch.document_path_hashes):
            saved_document = saved_documents_by_path.get(document_path)
            if not isinstance(saved_document, dict):
                expected_hashes.append(document_hash)
                continue
            if _saved_document_requires_checklist_row(
                saved_document=saved_document,
                selection_mapping=selection_by_saved_document_id,
            ):
                expected_hashes.append(document_hash)
    return expected_hashes


def _saved_document_requires_checklist_row(
    *,
    saved_document: dict[str, Any],
    selection_mapping: dict[str, dict[str, Any]],
) -> bool:
    saved_document_id = str(saved_document.get("saved_document_id", "")).strip()
    if saved_document_id and saved_document_id in selection_mapping:
        return True
    return str(saved_document.get("document_type", "")).strip() == "ud_document"


def _selection_document_number(selection_item: dict[str, Any], saved_document: dict[str, Any]) -> str:
    document_number = str(selection_item.get("document_number", "")).strip()
    if document_number:
        return document_number
    fallback = str(saved_document.get("extracted_document_number", "")).strip()
    if fallback:
        return fallback
    raise PrintAnnotationChecklistError(
        code="print_annotation_generation_failed",
        message="A planned checklist row did not resolve to a UD/Amendment document number.",
        details={"saved_document_id": saved_document.get("saved_document_id")},
    )


def _selection_by_saved_document_id(outcome: MailOutcomeRecord) -> dict[str, dict[str, Any]]:
    selection = outcome.ud_selection
    fallback_saved_documents = [
        document
        for document in outcome.saved_documents
        if str(document.get("saved_document_id", "")).strip()
    ]
    if isinstance(selection, dict):
        documents = selection.get("documents")
        if isinstance(documents, list):
            mapping: dict[str, dict[str, Any]] = {}
            for item in documents:
                if not isinstance(item, dict):
                    continue
                saved_document_id = str(item.get("source_saved_document_id", "")).strip()
                if not saved_document_id:
                    continue
                mapping[saved_document_id] = item
            return mapping
        fallback_selection = selection
    else:
        fallback_selection = None

    staged_write_mapping = _build_staged_write_selection_mapping(
        outcome=outcome,
        fallback_saved_documents=fallback_saved_documents,
    )
    if staged_write_mapping:
        return staged_write_mapping

    fallback_selection = _build_single_document_staged_write_selection_fallback(
        fallback_saved_documents=fallback_saved_documents,
        staged_write_operations=outcome.staged_write_operations,
    ) or fallback_selection

    if len(fallback_saved_documents) != 1:
        return {}
    if not isinstance(fallback_selection, dict):
        return {}
    saved_document_id = str(fallback_saved_documents[0].get("saved_document_id", "")).strip()
    return {
        saved_document_id: {
            "source_saved_document_id": saved_document_id,
            "document_number": fallback_saved_documents[0].get("extracted_document_number"),
            "selection": fallback_selection,
        }
    }


def _build_staged_write_selection_mapping(
    *,
    outcome: MailOutcomeRecord,
    fallback_saved_documents: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not fallback_saved_documents:
        return {}
    row_indexes_by_document_number = _row_indexes_by_document_number_from_staged_write_operations(
        outcome.staged_write_operations
    )
    mapping: dict[str, dict[str, Any]] = {}
    for document in fallback_saved_documents:
        saved_document_id = str(document.get("saved_document_id", "")).strip()
        if not saved_document_id:
            continue
        document_number = str(document.get("extracted_document_number", "")).strip()
        row_indexes = row_indexes_by_document_number.get(document_number, [])
        if not row_indexes:
            continue
        mapping[saved_document_id] = {
            "source_saved_document_id": saved_document_id,
            "document_number": document_number or document.get("extracted_document_number"),
            "selection": {
                **_build_selection_payload_from_row_indexes(row_indexes),
                "selection_source": "staged_write_operations_fallback",
            },
        }
    return mapping


def _build_single_document_staged_write_selection_fallback(
    *,
    fallback_saved_documents: list[dict[str, Any]],
    staged_write_operations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if len(fallback_saved_documents) != 1:
        return None
    row_indexes = _row_indexes_from_staged_write_operations(staged_write_operations)
    if not row_indexes:
        return None
    return {
        **_build_selection_payload_from_row_indexes(row_indexes),
        "selection_source": "staged_write_operations_fallback",
    }


def _row_indexes_from_staged_write_operations(staged_write_operations: list[dict[str, Any]]) -> list[int]:
    row_indexes = {
        row_index
        for operation in staged_write_operations
        if isinstance(operation, dict)
        for row_index in [operation.get("row_index")]
        if isinstance(row_index, int)
    }
    return sorted(row_indexes)


def _row_indexes_by_document_number_from_staged_write_operations(
    staged_write_operations: list[dict[str, Any]],
) -> dict[str, list[int]]:
    row_indexes_by_document_number: dict[str, set[int]] = {}
    for operation in staged_write_operations:
        if not isinstance(operation, dict):
            continue
        if str(operation.get("column_key", "")).strip() != "ud_ip_shared":
            continue
        document_number = str(operation.get("expected_post_write_value", "")).strip()
        row_index = operation.get("row_index")
        if not document_number or not isinstance(row_index, int):
            continue
        row_indexes_by_document_number.setdefault(document_number, set()).add(row_index)
    return {
        document_number: sorted(row_indexes)
        for document_number, row_indexes in row_indexes_by_document_number.items()
    }


def _build_selection_payload_from_row_indexes(row_indexes: list[int]) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "selected": True,
                "row_indexes": row_indexes,
            }
        ],
        "final_decision": "selected",
    }


def _selected_row_indexes(selection: Any) -> list[int]:
    if not isinstance(selection, dict):
        return []
    rows: list[int] = []
    for candidate in selection.get("candidates", []):
        if not isinstance(candidate, dict) or not candidate.get("selected"):
            continue
        for row_index in candidate.get("row_indexes", []):
            if isinstance(row_index, int):
                rows.append(row_index)
    return rows


def _planned_row_indexes(mail_outcomes: list[MailOutcomeRecord]) -> set[int]:
    row_indexes: set[int] = set()
    for outcome in mail_outcomes:
        selection_by_saved_document_id = _selection_by_saved_document_id(outcome)
        for selection_item in selection_by_saved_document_id.values():
            row_indexes.update(_selected_row_indexes(selection_item.get("selection")))
    return row_indexes


def _resolve_sl_no_column_index(workbook_snapshot: WorkbookSnapshot) -> int:
    return _resolve_required_column_index(
        workbook_snapshot=workbook_snapshot,
        spec=HeaderMappingSpec("sl_no", "SL.No.", ("SL.No",)),
        error_message="Print-annotation checklist generation requires one deterministic SL.No. workbook header.",
    )


def _resolve_required_column_index(
    *,
    workbook_snapshot: WorkbookSnapshot,
    spec: HeaderMappingSpec,
    error_message: str,
) -> int:
    mapping = resolve_header_mapping(workbook_snapshot, (spec,))
    if mapping is None:
        raise PrintAnnotationChecklistError(
            code="workbook_header_mapping_invalid",
            message=error_message,
            details={
                "sheet_name": workbook_snapshot.sheet_name,
                "required_header_text": spec.required_header_text,
            },
        )
    return mapping[spec.column_key]


def _resolve_column_values_by_row(
    *,
    workbook_snapshot: WorkbookSnapshot,
    column_index: int,
    live_workbook_path: Path | None,
    row_indexes: set[int],
    error_code: str,
    error_message: str,
) -> dict[int, str]:
    if live_workbook_path is not None:
        return _resolve_live_column_values_by_row(
            workbook_path=live_workbook_path,
            column_index=column_index,
            row_indexes=row_indexes,
            error_code=error_code,
            error_message=error_message,
        )
    rows_by_index = {row.row_index: row for row in workbook_snapshot.rows}
    resolved: dict[int, str] = {}
    for row_index in row_indexes:
        row = rows_by_index.get(row_index)
        if row is None:
            continue
        resolved[row_index] = str(row.values.get(column_index, "") or "")
    return resolved


def _resolve_live_column_values_by_row(
    *,
    workbook_path: Path,
    column_index: int,
    row_indexes: set[int],
    error_code: str,
    error_message: str,
) -> dict[int, str]:
    if not row_indexes:
        return {}
    try:
        import xlwings  # type: ignore
    except ImportError as exc:
        raise PrintAnnotationChecklistError(
            code="print_annotation_generation_failed",
            message="xlwings is required to resolve displayed workbook SL.No. values from the live workbook.",
            details={"workbook_path": str(workbook_path)},
        ) from exc

    app = xlwings.App(visible=False, add_book=False)
    book = None
    try:
        book = app.books.open(str(workbook_path), update_links=False, read_only=True)
        sheet = book.sheets[0]
        resolved: dict[int, str] = {}
        for row_index in sorted(row_indexes):
            try:
                displayed_value = sheet.range((row_index, column_index)).api.Text
            except Exception as exc:
                raise PrintAnnotationChecklistError(
                    code=error_code,
                    message=error_message,
                    details={
                        "workbook_path": str(workbook_path),
                        "row_index": row_index,
                        "column_index": column_index,
                        "error": str(exc),
                    },
                ) from exc
            resolved[row_index] = "" if displayed_value is None else str(displayed_value)
        return resolved
    finally:
        if book is not None:
            book.close()
        app.quit()


def _join_distinct_row_values(
    *,
    row_indexes: list[int],
    values_by_row: dict[int, str],
    field_label: str,
    allow_blank: bool,
    error_code: str,
    mail_id: str,
    saved_document_id: str,
) -> str:
    values: list[str] = []
    for row_index in row_indexes:
        raw_value = values_by_row.get(row_index, "")
        value = raw_value.strip()
        if value:
            if value not in values:
                values.append(value)
            continue
        if allow_blank:
            continue
        raise PrintAnnotationChecklistError(
            code=error_code,
            message=f"A selected checklist row did not yield a readable workbook {field_label} value.",
            details={
                "mail_id": mail_id,
                "saved_document_id": saved_document_id,
                "row_index": row_index,
                "field_label": field_label,
            },
        )
    return ", ".join(values)


def _build_print_annotation_html(payload: dict[str, Any]) -> str:
    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        rows = []
    table_body = "\n".join(
        "        <tr>"
        f"<td>{escape(str(row.get('print_sequence', '')))}</td>"
        f"<td>{escape(str(row.get('ud_or_amendment_no', '')))}</td>"
        f"<td>{escape(str(row.get('lc_sc', '')))}</td>"
        f"<td>{escape(str(row.get('bangladesh_bank_ref', '')))}</td>"
        f"<td>{escape(', '.join(str(value) for value in row.get('sl_no_values', [])))}</td>"
        f"<td>{escape(str(row.get('mail_subject', '')))}</td>"
        f"<td>{escape(str(row.get('document_filename', '')))}</td>"
        "</tr>"
        for row in rows
        if isinstance(row, dict)
    )
    if not table_body:
        table_body = (
            '        <tr><td colspan="7" class="empty">'
            "No printed UD/Amendment documents required annotation for this run."
            "</td></tr>"
        )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        "  <title>Print Annotation Checklist</title>\n"
        "  <style>\n"
        "    body { font-family: 'Segoe UI', Tahoma, sans-serif; margin: 24px; color: #1f2933; }\n"
        "    h1 { margin-bottom: 6px; }\n"
        "    .meta { color: #52606d; margin-bottom: 18px; }\n"
        "    table { width: 100%; border-collapse: collapse; }\n"
        "    th, td { border: 1px solid #d9e2ec; padding: 10px 12px; text-align: left; vertical-align: top; }\n"
        "    th { background: #f0f4f8; }\n"
        "    .empty { color: #7b8794; font-style: italic; }\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        "  <main>\n"
        "    <h1>Print Annotation Checklist</h1>\n"
        f'    <p class="meta">Run: {escape(str(payload.get("run_id", "")))} | Workflow: {escape(str(payload.get("workflow_id", "")))} | Generated: {escape(str(payload.get("generated_at_utc", "")))}</p>\n'
        "    <table>\n"
        "      <thead>\n"
        "        <tr>\n"
        "          <th>Print Sequence</th>\n"
        "          <th>UD/Amendment No.</th>\n"
        "          <th>LC/SC</th>\n"
        "          <th>Bangladesh Bank Ref.</th>\n"
        "          <th>SL.No.</th>\n"
        "          <th>Mail Subject</th>\n"
        "          <th>Document Filename</th>\n"
        "        </tr>\n"
        "      </thead>\n"
        "      <tbody>\n"
        f"{table_body}\n"
        "      </tbody>\n"
        "    </table>\n"
        "  </main>\n"
        "</body>\n"
        "</html>\n"
    )
