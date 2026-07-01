from __future__ import annotations

import json
import os
import re
import shutil
import webbrowser
from html import escape
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from project.erp import EmptyImportPIRegisterProvider, ImportPIRegisterProvider
from project.erp.import_pi import format_import_pi_decimal, parse_import_pi_decimal
from project.models import EmailMessage, MailMoveOperation, WorkflowId, WriteOperation
from project.outlook import MailMoveProvider
from project.storage import AttachmentContentProvider
from project.storage.artifacts import atomic_write_text
from project.utils.hashing import canonical_json_hash, sha256_file
from project.utils.ids import build_mail_move_operation_id, build_write_operation_id
from project.utils.json import pretty_json_dumps, to_jsonable
from project.utils.time import utc_timestamp, validate_timezone
from project.workbook import (
    WorkbookHeader,
    WorkbookMutationSessionProvider,
    WorkbookRow,
    WorkbookSnapshot,
    XLWingsWorkbookMutationProvider,
    XLWingsWorkbookSnapshotProvider,
)
from project.workflows.import_btb_lc.extraction import (
    IMPORT_BTB_LC_EXTRACTION_SCHEMA_VERSION,
    IMPORT_BTB_LC_REPORT_SCHEMA_VERSION,
    extract_import_btb_lc_pdf,
)
from project.workflows.import_btb_lc.extraction import (
    _canonicalize_related_export_lc as _canonicalize_extracted_related_lc,
)


IMPORT_BTB_LC_WORKFLOW_SCHEMA_ID = "import_btb_lc_workflow"
IMPORT_BTB_LC_WORKFLOW_SCHEMA_VERSION = "1.1.0"
IMPORT_BTB_LC_DATE_NUMBER_FORMAT = "dd/mm/yyyy"


@dataclass(slots=True, frozen=True)
class ImportBTBLCHeaderMapping:
    sl_no: int
    lc_sc_no: int
    up_no: int
    export_amount: int
    btb_lc_no: int
    btb_lc_issue_date: int
    import_amount: int
    quantity_kgs: int

    def as_dict(self) -> dict[str, int]:
        return {
            "sl_no": self.sl_no,
            "lc_sc_no": self.lc_sc_no,
            "up_no": self.up_no,
            "export_amount": self.export_amount,
            "btb_lc_no": self.btb_lc_no,
            "btb_lc_issue_date": self.btb_lc_issue_date,
            "import_amount": self.import_amount,
            "quantity_kgs": self.quantity_kgs,
        }


@dataclass(slots=True, frozen=True)
class ImportBTBLCDocument:
    document_id: str
    source_path: str
    filename: str
    file_sha256: str
    snapshot_index: int
    attachment_index: int | None
    extraction_artifact: dict[str, object]
    btb_lc_number: str | None
    btb_lc_date: str | None
    btb_lc_value: Decimal | None
    btb_lc_value_text: str | None
    currency: str | None
    seller_pi_numbers: tuple[str, ...]
    related_export_lc_number: str | None


@dataclass(slots=True, frozen=True)
class ImportBTBLCAllocationResult:
    workflow_report: dict[str, object]
    staged_write_plan: list[WriteOperation]


@dataclass(slots=True, frozen=True)
class DirectoryAttachmentContentProvider:
    attachment_root: Path

    def save_attachment(
        self,
        *,
        mail: EmailMessage,
        attachment_index: int,
        destination_path: Path,
    ) -> None:
        attachment = next(
            (
                item
                for item in mail.attachments
                if item.attachment_index == attachment_index
            ),
            None,
        )
        if attachment is None:
            raise ValueError(f"Mail {mail.mail_id} has no attachment index {attachment_index}.")
        candidates = [
            self.attachment_root / mail.mail_id / attachment.normalized_filename,
            self.attachment_root / mail.entry_id / attachment.normalized_filename,
            self.attachment_root / attachment.normalized_filename,
        ]
        source_path = next((path for path in candidates if path.is_file()), None)
        if source_path is None:
            raise ValueError(
                "Attachment source file was not found in the attachment directory: "
                + ", ".join(str(path) for path in candidates)
            )
        shutil.copy2(source_path, destination_path)


def run_import_btb_lc_file_picker(
    *,
    input_path: Path | Iterable[Path],
    output_directory: Path,
    workbook_snapshot: WorkbookSnapshot,
    run_id: str,
    import_document_root: Path | None = None,
    state_timezone: str = "Asia/Dhaka",
    apply_live_writes: bool = False,
    workbook_path: Path | None = None,
    mutation_session_provider: WorkbookMutationSessionProvider | None = None,
    pi_register_provider: ImportPIRegisterProvider | None = None,
) -> dict[str, object]:
    """Run the file-picker import path over local PDFs or extraction JSON artifacts."""

    output_directory.mkdir(parents=True, exist_ok=True)
    extraction_directory = output_directory / "extraction"
    extraction_directory.mkdir(parents=True, exist_ok=True)

    documents: list[ImportBTBLCDocument] = []
    resolved_inputs = _resolve_import_inputs(
        input_path,
        import_document_root=import_document_root,
    )
    for index, source in enumerate(resolved_inputs):
        artifact = _load_or_extract_artifact(source, extraction_directory=extraction_directory)
        documents.append(
            _document_from_artifact(
                artifact=artifact,
                snapshot_index=index,
                attachment_index=None,
            )
        )

    allocation = allocate_import_btb_lc_documents(
        documents=documents,
        workbook_snapshot=workbook_snapshot,
        run_id=run_id,
        pi_register_provider=pi_register_provider,
    )
    report = dict(allocation.workflow_report)
    report["state_timezone"] = state_timezone
    write_execution = {
        "requested": bool(apply_live_writes),
        "status": "not_requested",
        "target_probes": [],
        "commit_marker": None,
        "discrepancies": [],
    }
    if apply_live_writes:
        if workbook_path is None:
            raise ValueError("--apply-live-writes requires --workbook")
        write_execution = execute_import_btb_lc_writes(
            run_id=run_id,
            workbook_snapshot=workbook_snapshot,
            staged_write_plan=allocation.staged_write_plan,
            workbook_path=workbook_path,
            mutation_session_provider=mutation_session_provider,
        )
    report["write_execution"] = write_execution
    report["overall_decision"] = _overall_decision_from_document_outcomes(
        report["document_outcomes"],
        write_execution=write_execution,
    )
    report["completed_at_utc"] = utc_timestamp()
    report["staged_write_plan_hash"] = canonical_json_hash(
        to_jsonable(allocation.staged_write_plan)
    )

    output_path = output_directory / f"{run_id}.import-btb-lc.workflow.json"
    atomic_write_text(output_path, pretty_json_dumps(report))
    html_path = output_directory / f"{run_id}.import-btb-lc.workflow.html"
    atomic_write_text(html_path, render_import_btb_lc_html_report(report))
    summary = {
        "schema_id": IMPORT_BTB_LC_WORKFLOW_SCHEMA_ID,
        "schema_version": IMPORT_BTB_LC_WORKFLOW_SCHEMA_VERSION,
        "report_schema_version": IMPORT_BTB_LC_REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "input_path": str(resolved_inputs[0]) if len(resolved_inputs) == 1 else None,
        "input_paths": [str(path) for path in resolved_inputs],
        "output_path": str(output_path.resolve()),
        "html_output_path": str(html_path.resolve()),
        "overall_decision": report["overall_decision"],
        "document_count": len(documents),
        "decision_counts": _decision_counts(report["document_outcomes"]),
        "write_disposition_counts": _write_disposition_counts(report["document_outcomes"]),
        "selected_rows": _selected_row_summary(report["document_outcomes"]),
        "staged_write_operation_count": len(allocation.staged_write_plan),
        "write_execution_status": write_execution["status"],
    }
    return summary


def run_import_btb_lc_current_full(
    *,
    mail_snapshot: list[EmailMessage],
    attachment_provider: AttachmentContentProvider,
    output_directory: Path,
    workbook_snapshot: WorkbookSnapshot,
    import_document_root: Path,
    run_id: str,
    state_timezone: str = "Asia/Dhaka",
    source_folder_entry_id: str | None = None,
    destination_folder_entry_id: str | None = None,
    apply_live_writes: bool = False,
    workbook_path: Path | None = None,
    mutation_session_provider: WorkbookMutationSessionProvider | None = None,
    move_mails: bool = False,
    mail_move_provider: MailMoveProvider | None = None,
    page_provider=None,
    pi_register_provider: ImportPIRegisterProvider | None = None,
) -> dict[str, object]:
    output_directory.mkdir(parents=True, exist_ok=True)
    source_documents_root = output_directory / "source_documents"
    extraction_directory = output_directory / "extraction"
    source_documents_root.mkdir(parents=True, exist_ok=True)
    extraction_directory.mkdir(parents=True, exist_ok=True)

    keywords = load_import_relevance_keywords()
    relevance_by_mail_id = {
        mail.mail_id: evaluate_import_mail_relevance(mail, keywords=keywords)
        for mail in mail_snapshot
    }
    documents: list[ImportBTBLCDocument] = []
    document_mail_id: dict[str, str] = {}
    acquisition_records: list[dict[str, object]] = []
    acquisition_discrepancies: list[dict[str, object]] = []

    for mail in mail_snapshot:
        relevance = relevance_by_mail_id[mail.mail_id]
        if not relevance["eligible"]:
            continue
        pdf_attachments = [
            attachment
            for attachment in sorted(mail.attachments, key=lambda item: item.attachment_index)
            if attachment.normalized_filename.lower().endswith(".pdf")
        ]
        if not pdf_attachments:
            acquisition_discrepancies.append(
                _mail_discrepancy(
                    mail_id=mail.mail_id,
                    code="import_required_document_missing",
                    message="Relevant import mail contained no PDF attachments.",
                    details={"subject_raw": mail.subject_raw},
                )
            )
            continue
        for attachment in pdf_attachments:
            saved_path = (
                source_documents_root
                / mail.mail_id
                / f"{attachment.attachment_index:03d}"
                / attachment.normalized_filename
            )
            saved_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                attachment_provider.save_attachment(
                    mail=mail,
                    attachment_index=attachment.attachment_index,
                    destination_path=saved_path,
                )
                artifact = extract_import_btb_lc_pdf(
                    pdf_path=saved_path,
                    page_provider=page_provider,
                )
                extraction_path = extraction_directory / f"{mail.mail_id}.{attachment.attachment_index:03d}.{saved_path.name}.import-btb-lc.json"
                atomic_write_text(extraction_path, pretty_json_dumps(artifact))
                document = _document_from_artifact(
                    artifact=artifact,
                    snapshot_index=mail.snapshot_index,
                    attachment_index=attachment.attachment_index,
                )
                documents.append(document)
                document_mail_id[document.document_id] = mail.mail_id
                promotion = _promote_import_document_if_valid(
                    source_path=saved_path,
                    artifact=artifact,
                    import_document_root=import_document_root,
                )
                acquisition_records.append(
                    {
                        "mail_id": mail.mail_id,
                        "attachment_index": attachment.attachment_index,
                        "attachment_name": attachment.attachment_name,
                        "source_evidence_path": str(saved_path.resolve()),
                        "extraction_artifact_path": str(extraction_path.resolve()),
                        "promotion": promotion,
                    }
                )
                if promotion.get("status") == "hard_block":
                    acquisition_discrepancies.append(
                        _mail_discrepancy(
                            mail_id=mail.mail_id,
                            code=str(promotion["code"]),
                            message=str(promotion["message"]),
                            details=dict(promotion),
                        )
                    )
            except Exception as exc:
                acquisition_discrepancies.append(
                    _mail_discrepancy(
                        mail_id=mail.mail_id,
                        code="import_required_document_missing",
                        message="Import BTB LC PDF could not be acquired or extracted deterministically.",
                        details={
                            "attachment_index": attachment.attachment_index,
                            "attachment_name": attachment.attachment_name,
                            "error": str(exc),
                        },
                    )
                )

    allocation = allocate_import_btb_lc_documents(
        documents=documents,
        workbook_snapshot=workbook_snapshot,
        run_id=run_id,
        pi_register_provider=pi_register_provider,
    )
    report = dict(allocation.workflow_report)
    report["launcher_path"] = "current_full"
    report["state_timezone"] = state_timezone
    report["import_keyword_revision"] = keywords["revision"]
    report["mail_relevance"] = list(relevance_by_mail_id.values())
    report["source_document_acquisition"] = acquisition_records
    report["acquisition_discrepancies"] = acquisition_discrepancies
    report["document_mail_index"] = dict(document_mail_id)

    write_execution = {
        "requested": bool(apply_live_writes),
        "status": "not_requested",
        "target_probes": [],
        "commit_marker": None,
        "discrepancies": [],
    }
    if apply_live_writes:
        if workbook_path is None:
            raise ValueError("--apply-live-writes requires --workbook")
        write_execution = execute_import_btb_lc_writes(
            run_id=run_id,
            workbook_snapshot=workbook_snapshot,
            staged_write_plan=allocation.staged_write_plan,
            workbook_path=workbook_path,
            mutation_session_provider=mutation_session_provider,
        )
    report["write_execution"] = write_execution
    mail_outcomes = _build_current_full_mail_outcomes(
        mail_snapshot=mail_snapshot,
        relevance_by_mail_id=relevance_by_mail_id,
        document_outcomes=report["document_outcomes"],
        document_mail_id=document_mail_id,
        acquisition_discrepancies=acquisition_discrepancies,
        run_id=run_id,
    )
    mail_move = _execute_import_current_mail_moves(
        run_id=run_id,
        mail_outcomes=mail_outcomes,
        source_folder_entry_id=source_folder_entry_id,
        destination_folder_entry_id=destination_folder_entry_id,
        move_mails=move_mails,
        mail_move_provider=mail_move_provider,
        write_execution=write_execution,
        staged_write_plan=allocation.staged_write_plan,
    )
    report["mail_outcomes"] = mail_outcomes
    report["mail_move"] = mail_move
    report["overall_decision"] = _current_full_overall_decision(
        mail_outcomes=mail_outcomes,
        write_execution=write_execution,
        mail_move=mail_move,
    )
    report["completed_at_utc"] = utc_timestamp()
    report["staged_write_plan_hash"] = canonical_json_hash(to_jsonable(allocation.staged_write_plan))

    output_path = output_directory / f"{run_id}.import-btb-lc.current-full.json"
    atomic_write_text(output_path, pretty_json_dumps(report))
    html_path = output_directory / f"{run_id}.import-btb-lc.current-full.html"
    atomic_write_text(html_path, render_import_btb_lc_html_report(report))
    return {
        "schema_id": IMPORT_BTB_LC_WORKFLOW_SCHEMA_ID,
        "schema_version": IMPORT_BTB_LC_WORKFLOW_SCHEMA_VERSION,
        "report_schema_version": IMPORT_BTB_LC_REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "launcher_path": "current_full",
        "output_path": str(output_path.resolve()),
        "html_output_path": str(html_path.resolve()),
        "overall_decision": report["overall_decision"],
        "mail_count": len(mail_snapshot),
        "relevant_mail_count": sum(1 for item in relevance_by_mail_id.values() if item["eligible"]),
        "document_count": len(documents),
        "decision_counts": _mail_decision_counts(mail_outcomes),
        "write_disposition_counts": _mail_write_disposition_counts(mail_outcomes),
        "selected_rows": _selected_row_summary(report["document_outcomes"]),
        "staged_write_operation_count": len(allocation.staged_write_plan),
        "write_execution_status": write_execution["status"],
        "mail_move_status": mail_move["status"],
        "mail_move_operation_count": len(mail_move["operations"]),
    }


def allocate_import_btb_lc_documents(
    *,
    documents: list[ImportBTBLCDocument],
    workbook_snapshot: WorkbookSnapshot,
    run_id: str,
    pi_register_provider: ImportPIRegisterProvider | None = None,
) -> ImportBTBLCAllocationResult:
    mapping = resolve_import_btb_lc_header_mapping(workbook_snapshot)
    active_pi_register_provider = pi_register_provider or EmptyImportPIRegisterProvider()
    report: dict[str, object] = {
        "schema_id": IMPORT_BTB_LC_WORKFLOW_SCHEMA_ID,
        "schema_version": IMPORT_BTB_LC_WORKFLOW_SCHEMA_VERSION,
        "report_schema_version": IMPORT_BTB_LC_REPORT_SCHEMA_VERSION,
        "workflow_id": WorkflowId.IMPORT_BTB_LC.value,
        "run_id": run_id,
        "started_at_utc": utc_timestamp(),
        "completed_at_utc": None,
        "extraction_schema_version": IMPORT_BTB_LC_EXTRACTION_SCHEMA_VERSION,
        "workbook": {
            "sheet_name": workbook_snapshot.sheet_name,
            "header_mapping": mapping.as_dict() if mapping is not None else None,
        },
        "allocation_order": [],
        "document_outcomes": [],
        "staged_write_plan": [],
        "summary": {},
        "overall_decision": "pass",
    }
    if mapping is None:
        outcomes = [
            _blocked_document_outcome(
                document=document,
                code="workbook_header_mapping_invalid",
                message="Required import workbook headers could not be resolved deterministically.",
                details={"sheet_name": workbook_snapshot.sheet_name},
            )
            for document in documents
        ]
        report["document_outcomes"] = outcomes
        report["summary"] = _summarize_outcomes(outcomes)
        report["overall_decision"] = "hard_block" if outcomes else "pass"
        return ImportBTBLCAllocationResult(workflow_report=report, staged_write_plan=[])

    outcomes_by_document_id: dict[str, dict[str, object]] = {}
    staged_write_plan: list[WriteOperation] = []
    reserved_rows: set[int] = set()
    accepted_signatures_by_btb: dict[str, tuple[object, ...]] = {}
    ordered_documents = sorted(documents, key=_allocation_sort_key)
    report["allocation_order"] = [document.document_id for document in ordered_documents]

    for document in ordered_documents:
        outcome, operations = _allocate_one_document(
            document=document,
            workbook_snapshot=workbook_snapshot,
            mapping=mapping,
            run_id=run_id,
            reserved_rows=reserved_rows,
            accepted_signatures_by_btb=accepted_signatures_by_btb,
            pi_register_provider=active_pi_register_provider,
        )
        outcomes_by_document_id[document.document_id] = outcome
        staged_write_plan.extend(operations)

    outcomes = [
        outcomes_by_document_id[document.document_id]
        for document in sorted(documents, key=lambda item: (item.snapshot_index, item.filename.casefold()))
    ]
    report["document_outcomes"] = outcomes
    report["staged_write_plan"] = to_jsonable(staged_write_plan)
    report["summary"] = _summarize_outcomes(outcomes)
    report["overall_decision"] = _overall_decision_from_document_outcomes(outcomes)
    return ImportBTBLCAllocationResult(
        workflow_report=report,
        staged_write_plan=staged_write_plan,
    )


def resolve_import_btb_lc_header_mapping(
    snapshot: WorkbookSnapshot,
) -> ImportBTBLCHeaderMapping | None:
    sl_no = _resolve_header(snapshot.headers, "SL.No.", ("SL.No", "SL No.", "SL No"))
    lc_sc_no = _resolve_header(snapshot.headers, "L/C & S/C No.", ("L/C No.", "LC/SC No.", "LC No."))
    up_no = _resolve_header(snapshot.headers, "UP No.", ("UP",))
    btb_lc_no = _resolve_header(
        snapshot.headers,
        "BTB L/C No.",
        (
            "BTB L/C No",
            "BTB LC No.",
            "BTB LC No",
            "Back To Back L/C No.",
            "Back-to-Back L/C No.",
        ),
    )
    btb_lc_issue_date = _resolve_header(
        snapshot.headers,
        "BTB LC Issue Date",
        (
            "BTB L/C Issue Date",
            "BTB LC Issue Dt.",
            "BTB L/C Issue Dt.",
            "BTB LC Date",
            "BTB L/C Date",
        ),
    )
    export_amount_headers = _resolve_header_candidates(snapshot.headers, "Amount", required_column_index=6)
    import_amount_headers = _resolve_header_candidates(snapshot.headers, "Amount", required_column_index=22)
    quantity_kgs = _resolve_header(
        snapshot.headers,
        "Quantity (Kgs)",
        ("Quantity Kgs", "Quantity (Kg)", "Quantity Kg", "Qty.Kg", "Qty Kg", "Qty.Kgs", "Qty Kgs"),
    )
    if (
        sl_no is None
        or lc_sc_no is None
        or up_no is None
        or btb_lc_no is None
        or btb_lc_issue_date is None
        or len(export_amount_headers) != 1
        or len(import_amount_headers) != 1
        or quantity_kgs is None
    ):
        return None
    return ImportBTBLCHeaderMapping(
        sl_no=sl_no,
        lc_sc_no=lc_sc_no,
        up_no=up_no,
        export_amount=export_amount_headers[0],
        btb_lc_no=btb_lc_no,
        btb_lc_issue_date=btb_lc_issue_date,
        import_amount=import_amount_headers[0],
        quantity_kgs=quantity_kgs,
    )


def execute_import_btb_lc_writes(
    *,
    run_id: str,
    workbook_snapshot: WorkbookSnapshot,
    staged_write_plan: list[WriteOperation],
    workbook_path: Path,
    mutation_session_provider: WorkbookMutationSessionProvider | None = None,
) -> dict[str, object]:
    if not staged_write_plan:
        return {
            "requested": True,
            "status": "no_writes",
            "target_probes": [],
            "commit_marker": None,
            "discrepancies": [],
        }
    mapping = resolve_import_btb_lc_header_mapping(workbook_snapshot)
    if mapping is None:
        return {
            "requested": True,
            "status": "hard_blocked_no_write",
            "target_probes": [],
            "commit_marker": None,
            "discrepancies": [
                {
                    "code": "workbook_header_mapping_invalid",
                    "severity": "hard_block",
                    "message": "Required import workbook headers could not be resolved before writing.",
                    "details": {"sheet_name": workbook_snapshot.sheet_name},
                }
            ],
        }
    provider = mutation_session_provider or XLWingsWorkbookMutationProvider(workbook_path)
    open_result = provider.open_write_session(operator_context=None)
    if open_result.discrepancy_code is not None or open_result.session is None:
        return {
            "requested": True,
            "status": "hard_blocked_no_write",
            "preflight": to_jsonable(open_result.preflight),
            "target_probes": [],
            "commit_marker": None,
            "discrepancies": [
                {
                    "code": open_result.discrepancy_code or "excel_adapter_unavailable",
                    "severity": "hard_block",
                    "message": open_result.discrepancy_message
                    or "Workbook write session could not be opened safely.",
                    "details": open_result.discrepancy_details,
                }
            ],
        }

    session = open_result.session
    probes: list[dict[str, object]] = []
    try:
        live_snapshot = session.capture_snapshot()
        prevalidation = prevalidate_import_btb_lc_write_plan(
            run_id=run_id,
            workbook_snapshot=live_snapshot,
            staged_write_plan=staged_write_plan,
        )
        probes.extend(prevalidation["target_probes"])
        if prevalidation["discrepancies"]:
            return {
                "requested": True,
                "status": "hard_blocked_no_write",
                "preflight": to_jsonable(open_result.preflight),
                "target_probes": probes,
                "commit_marker": None,
                "discrepancies": prevalidation["discrepancies"],
            }

        column_by_key = _import_column_index_by_key(mapping)
        for operation in staged_write_plan:
            session.write_cell(
                sheet_name=operation.sheet_name,
                row_index=operation.row_index,
                column_index=column_by_key[operation.column_key],
                value=_coerce_import_write_value(
                    operation.expected_post_write_value,
                    number_format=operation.number_format,
                ),
                number_format=operation.number_format,
            )
        post_probes = _collect_import_post_write_probes(
            session=session,
            mapping=mapping,
            staged_write_plan=staged_write_plan,
        )
        probes.extend(post_probes)
        mismatches = [probe for probe in post_probes if probe["classification"] != "matches_post_write"]
        if mismatches:
            return {
                "requested": True,
                "status": "uncertain_not_committed",
                "preflight": to_jsonable(open_result.preflight),
                "target_probes": probes,
                "commit_marker": None,
                "discrepancies": [
                    {
                        "code": "workbook_post_write_probe_mismatch",
                        "severity": "hard_block",
                        "message": "One or more import workbook targets did not match after write application.",
                        "details": {"mismatches": mismatches},
                    }
                ],
            }
        try:
            session.save()
        except Exception as exc:
            return {
                "requested": True,
                "status": "uncertain_not_committed",
                "preflight": to_jsonable(open_result.preflight),
                "target_probes": probes,
                "commit_marker": None,
                "discrepancies": [
                    {
                        "code": "workbook_save_conflict",
                        "severity": "hard_block",
                        "message": "Workbook save failed after import writes were applied.",
                        "details": {"error": str(exc)},
                    }
                ],
            }
        return {
            "requested": True,
            "status": "committed",
            "preflight": to_jsonable(open_result.preflight),
            "target_probes": probes,
            "commit_marker": {
                "run_id": run_id,
                "workflow_id": WorkflowId.IMPORT_BTB_LC.value,
                "committed_at_utc": utc_timestamp(),
                "operation_count": len(staged_write_plan),
                "staged_write_plan_hash": canonical_json_hash(to_jsonable(staged_write_plan)),
            },
            "discrepancies": [],
        }
    finally:
        try:
            session.close()
        except Exception:
            pass


def prevalidate_import_btb_lc_write_plan(
    *,
    run_id: str,
    workbook_snapshot: WorkbookSnapshot,
    staged_write_plan: list[WriteOperation],
) -> dict[str, object]:
    mapping = resolve_import_btb_lc_header_mapping(workbook_snapshot)
    if mapping is None:
        return {
            "target_probes": [],
            "summary": {"status": "hard_blocked", "total_targets": len(staged_write_plan)},
            "discrepancies": [
                {
                    "code": "workbook_header_mapping_invalid",
                    "severity": "hard_block",
                    "message": "Required import workbook headers could not be resolved deterministically during target prevalidation.",
                    "details": {"sheet_name": workbook_snapshot.sheet_name},
                }
            ],
        }
    column_by_key = _import_column_index_by_key(mapping)
    rows_by_index = {row.row_index: row for row in workbook_snapshot.rows}
    probes: list[dict[str, object]] = []
    discrepancies: list[dict[str, object]] = []
    for operation in staged_write_plan:
        column_index = column_by_key.get(operation.column_key)
        row = rows_by_index.get(operation.row_index)
        observed = row.values.get(column_index, "") if row is not None and column_index is not None else None
        failure_reason = None
        if operation.sheet_name != workbook_snapshot.sheet_name:
            failure_reason = "sheet_name_mismatch"
        elif column_index is None:
            failure_reason = "column_key_unmapped"
        elif _normalize_cell_value(observed) not in {"", None}:
            failure_reason = "target_cell_already_populated"
        classification = (
            "matches_pre_write"
            if failure_reason is None
            and _normalize_cell_value(observed) in {"", None}
            and operation.expected_pre_write_value is None
            else "mismatch_unknown"
        )
        probe = {
            "write_operation_id": operation.write_operation_id,
            "run_id": run_id,
            "mail_id": operation.mail_id,
            "probe_stage": "prevalidation",
            "sheet_name": operation.sheet_name,
            "row_index": operation.row_index,
            "column_key": operation.column_key,
            "column_index": column_index,
            "expected_pre_write_value": operation.expected_pre_write_value,
            "expected_post_write_value": operation.expected_post_write_value,
            "observed_value": observed,
            "classification": classification,
            "failure_reason": failure_reason,
        }
        probes.append(probe)
        if classification != "matches_pre_write":
            discrepancies.append(
                {
                    "code": "import_target_cell_already_populated"
                    if failure_reason == "target_cell_already_populated"
                    else "workbook_target_prevalidation_failed",
                    "severity": "hard_block",
                    "message": "Staged import workbook target failed prevalidation.",
                    "details": probe,
                }
            )
    return {
        "target_probes": probes,
        "summary": {
            "status": "passed" if not discrepancies else "hard_blocked",
            "total_targets": len(probes),
            "matches_pre_write": sum(1 for probe in probes if probe["classification"] == "matches_pre_write"),
            "mismatch_unknown": sum(1 for probe in probes if probe["classification"] == "mismatch_unknown"),
        },
        "discrepancies": discrepancies,
    }


def render_import_btb_lc_html_report(report: dict[str, object]) -> str:
    outcomes = report.get("document_outcomes", [])
    rows = []
    if isinstance(outcomes, list):
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            fields = outcome.get("extracted_fields") if isinstance(outcome.get("extracted_fields"), dict) else {}
            bank_detection = (
                outcome.get("bank_detection")
                if isinstance(outcome.get("bank_detection"), dict)
                else {}
            )
            pi_validation = (
                outcome.get("pi_register_validation")
                if isinstance(outcome.get("pi_register_validation"), dict)
                else {}
            )
            extraction_field_evidence = (
                outcome.get("extraction_field_evidence")
                if isinstance(outcome.get("extraction_field_evidence"), dict)
                else {}
            )
            pi_numbers = fields.get("seller_pi_numbers", []) if isinstance(fields, dict) else []
            pi_numbers_display = ", ".join(
                str(value) for value in pi_numbers
            ) if isinstance(pi_numbers, list) else str(pi_numbers or "")
            calculated_quantity = (
                pi_validation.get("quantity_kgs")
                or outcome.get("selected_quantity_kgs")
                or ""
            )
            rows.append(
                "<tr>"
                f"<td>{escape(str(outcome.get('decision', '')))}</td>"
                f"<td>{escape(str(outcome.get('filename', '')))}</td>"
                f"<td>{escape(str(fields.get('btb_lc_number', '') if isinstance(fields, dict) else ''))}</td>"
                f"<td>{escape(_format_import_report_date(fields.get('btb_lc_date', '') if isinstance(fields, dict) else ''))}</td>"
                f"<td>{escape(str(fields.get('btb_lc_value', '') if isinstance(fields, dict) else ''))}</td>"
                f"<td>{escape(str(calculated_quantity))}</td>"
                f"<td>{escape(_format_related_export_lc_display(fields.get('related_export_lc_number', '') if isinstance(fields, dict) else ''))}</td>"
                f"<td>{escape(str(outcome.get('selected_sl_no', '') or ''))}</td>"
                f"<td>{escape(pi_numbers_display)}</td>"
                f"<td>{escape(str(outcome.get('write_disposition', '')))}</td>"
                f"<td>{escape(_format_import_report_messages(outcome.get('decision_reasons')))}</td>"
                f"<td>{escape(str(bank_detection.get('bank_name') or bank_detection.get('bank_id') or ''))}</td>"
                f"<td>{escape(_format_import_report_issues(outcome.get('warnings')))}</td>"
                f"<td>{escape(str(pi_validation.get('total_amount') or ''))}</td>"
                f"<td>{escape(str(fields.get('currency', '') if isinstance(fields, dict) else ''))}</td>"
                f"<td>{escape(_format_import_raw_field_evidence(extraction_field_evidence))}</td>"
                f"<td>{escape(_format_import_candidate_evidence(outcome.get('candidate_rows')))}</td>"
                f"<td>{escape(_format_import_report_issues(outcome.get('hard_block_discrepancies')))}</td>"
                "</tr>"
            )
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    state_timezone = str(report.get("state_timezone", "Asia/Dhaka") or "Asia/Dhaka")
    generated_at_display = _format_import_report_timestamp(
        report.get("completed_at_utc") or report.get("started_at_utc"),
        state_timezone=state_timezone,
    )
    snapshot_rows = _import_report_snapshot_rows(
        report=report,
        generated_at_display=generated_at_display,
        state_timezone=state_timezone,
    )
    summary_rows = [
        ("Pass", summary.get("pass", 0)),
        ("Warning", summary.get("warning", 0)),
        ("Hard Block", summary.get("hard_block", 0)),
        ("Staged", summary.get("staged", 0)),
        ("Duplicate Only", summary.get("duplicate_only", 0)),
    ]
    rows_html = "\n".join(rows) if rows else '<tr><td colspan="18" class="empty">No import BTB LC documents were processed.</td></tr>'
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\">\n"
        "  <title>Workflow Dashboard: import_btb_lc</title>\n"
        "  <style>\n"
        "    :root { color-scheme: light; }\n"
        "    html, body { height: 100%; }\n"
        "    body { font-family: 'Segoe UI', Tahoma, sans-serif; margin: 0; color: #1f2933; background: #f6f8fb; }\n"
        "    main { width: 100%; max-width: none; margin: 0; padding: 24px; box-sizing: border-box; }\n"
        "    h1, h2 { color: #102a43; }\n"
        "    h1 { margin-bottom: 4px; }\n"
        "    .meta { color: #52606d; margin-bottom: 24px; }\n"
        "    .section { background: #ffffff; border: 1px solid #d9e2ec; border-radius: 10px; padding: 18px 20px; margin-bottom: 18px; }\n"
        "    .documents-section { padding-bottom: 12px; }\n"
        "    .sticky-section-title { position: sticky; top: 0; z-index: 5; background: #ffffff; padding-bottom: 12px; margin-bottom: 0; }\n"
        "    .table-wrap { width: 100%; max-width: 100%; overflow-x: auto; padding-bottom: 8px; }\n"
        "    table { width: 100%; border-collapse: collapse; margin-top: 10px; }\n"
        "    .wide-table { width: max-content; min-width: 100%; table-layout: auto; margin-top: 0; }\n"
        "    th, td { border-bottom: 1px solid #e5e7eb; border-right: 1px solid #d9e2ec; padding: 10px 12px; text-align: left; vertical-align: top; white-space: normal; overflow-wrap: anywhere; word-break: break-word; background: #ffffff; }\n"
        "    th:last-child, td:last-child { border-right: none; }\n"
        "    th { background: #f0f4f8; font-weight: 600; }\n"
        "    .wide-table thead th { position: sticky; top: 0; z-index: 4; box-shadow: inset 0 -1px 0 #d9e2ec; }\n"
        "    td { line-height: 1.4; }\n"
        "    code { font-family: Consolas, 'Courier New', monospace; background: #f0f4f8; padding: 1px 4px; border-radius: 4px; }\n"
        "    .empty { color: #7b8794; font-style: italic; }\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        "  <main>\n"
        "    <h1>Workflow Dashboard: import_btb_lc</h1>\n"
        f"    <p class=\"meta\">Generated at: {escape(generated_at_display)} ({escape(state_timezone)})</p>\n"
        f"{_render_import_report_key_value_section('Snapshot', snapshot_rows)}\n"
        f"{_render_import_report_key_value_section('Summary', summary_rows)}\n"
        "    <section class=\"section documents-section\">\n"
        "      <h2 class=\"sticky-section-title\">Documents</h2>\n"
        "      <div class=\"table-wrap\">\n"
        "      <table class=\"wide-table\">\n"
        "        <thead><tr><th>Decision</th><th>Filename</th><th>BTB LC</th><th>BTB LC Issue Date</th><th>BTB Value</th><th>Calculated Quantity (Kgs)</th><th>Related Export LC</th><th>SL.No.</th><th>Seller PI Number(s)</th><th>Disposition</th><th>Decision Reasons</th><th>Bank</th><th>Warnings</th><th>ERP PI Amount</th><th>Currency</th><th>Raw Extracted Values</th><th>Candidate Evidence</th><th>Discrepancies</th></tr></thead>\n"
        "        <tbody>\n"
        f"{rows_html}\n"
        "        </tbody>\n"
        "      </table>\n"
        "      </div>\n"
        "    </section>\n"
        "  </main>\n"
        "</body>\n"
        "</html>\n"
    )


def open_import_btb_lc_report_in_browser(*, html_path: Path) -> None:
    if not html_path.exists():
        raise FileNotFoundError(str(html_path))
    if not webbrowser.open(html_path.resolve().as_uri()):
        raise RuntimeError(f"Browser open request was not acknowledged for {html_path}")


def load_import_relevance_keywords() -> dict[str, object]:
    try:
        from project.rules.workflows.import_btb_lc import keywords as keyword_module
    except ImportError as exc:
        raise ValueError("Import BTB LC keyword module could not be loaded.") from exc

    include_keywords = _validate_keyword_sequence(
        getattr(keyword_module, "IMPORT_SUBJECT_KEYWORDS", None),
        name="IMPORT_SUBJECT_KEYWORDS",
        required_non_empty=True,
    )
    exclude_keywords = _validate_keyword_sequence(
        getattr(keyword_module, "IMPORT_SUBJECT_EXCLUDE_KEYWORDS", ()),
        name="IMPORT_SUBJECT_EXCLUDE_KEYWORDS",
        required_non_empty=False,
    )
    revision = getattr(keyword_module, "IMPORT_KEYWORD_REVISION", None)
    if not isinstance(revision, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.[1-9]\d*", revision.strip()):
        raise ValueError("IMPORT_KEYWORD_REVISION must match YYYY-MM-DD.N")
    return {
        "revision": revision.strip(),
        "include_keywords": include_keywords,
        "exclude_keywords": exclude_keywords,
    }


def evaluate_import_mail_relevance(
    mail: EmailMessage,
    *,
    keywords: dict[str, object],
) -> dict[str, object]:
    normalized_subject = _normalize_subject(mail.subject_raw)
    include_keywords = list(keywords.get("include_keywords", []))
    exclude_keywords = list(keywords.get("exclude_keywords", []))
    include_hits = [
        keyword
        for keyword in include_keywords
        if str(keyword).casefold() in normalized_subject
    ]
    exclude_hits = [
        keyword
        for keyword in exclude_keywords
        if str(keyword).casefold() in normalized_subject
    ]
    eligible = bool(include_hits) and not exclude_hits
    return {
        "mail_id": mail.mail_id,
        "snapshot_index": mail.snapshot_index,
        "subject_raw": mail.subject_raw,
        "normalized_subject": normalized_subject,
        "include_keyword_hits": include_hits,
        "exclude_keyword_hits": exclude_hits,
        "eligible": eligible,
        "import_keyword_revision": keywords["revision"],
    }


def _validate_keyword_sequence(
    value: object,
    *,
    name: str,
    required_non_empty: bool,
) -> list[str]:
    if not isinstance(value, (tuple, list)):
        raise ValueError(f"{name} must be a sequence of non-empty strings")
    normalized = []
    seen = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name} must contain only non-empty strings")
        keyword = " ".join(item.strip().casefold().split())
        if keyword in seen:
            raise ValueError(f"{name} contains duplicate keyword after normalization: {item}")
        seen.add(keyword)
        normalized.append(keyword)
    if required_non_empty and not normalized:
        raise ValueError(f"{name} must contain at least one keyword")
    return normalized


def _normalize_subject(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _promote_import_document_if_valid(
    *,
    source_path: Path,
    artifact: dict[str, object],
    import_document_root: Path,
) -> dict[str, object]:
    if artifact.get("overall_extraction_decision") == "hard_block":
        return {
            "status": "not_promoted",
            "reason": "extraction_hard_block",
        }
    fields = artifact.get("fields")
    if not isinstance(fields, dict):
        return {"status": "not_promoted", "reason": "missing_fields"}
    date_value = _field_canonical(fields, "btb_lc_date")
    if date_value is None:
        return {"status": "not_promoted", "reason": "missing_btb_lc_date"}
    try:
        year = date.fromisoformat(date_value).year
    except ValueError:
        return {"status": "not_promoted", "reason": "invalid_btb_lc_date"}

    destination_directory = import_document_root / str(year)
    destination_directory.mkdir(parents=True, exist_ok=True)
    destination_path = destination_directory / source_path.name
    source_hash = sha256_file(source_path)
    if destination_path.exists():
        destination_hash = sha256_file(destination_path)
        if destination_hash == source_hash:
            return {
                "status": "reused_existing",
                "destination_path": str(destination_path.resolve()),
                "file_sha256": source_hash,
            }
        return {
            "status": "hard_block",
            "code": "import_storage_filename_content_conflict",
            "message": "Import document destination filename already exists with different file content.",
            "destination_path": str(destination_path.resolve()),
            "source_file_sha256": source_hash,
            "destination_file_sha256": destination_hash,
        }

    temp_path = destination_directory / f"{destination_path.name}.{os.getpid()}.tmp"
    shutil.copy2(source_path, temp_path)
    os.replace(temp_path, destination_path)
    return {
        "status": "promoted",
        "destination_path": str(destination_path.resolve()),
        "file_sha256": source_hash,
    }


def _allocate_one_document(
    *,
    document: ImportBTBLCDocument,
    workbook_snapshot: WorkbookSnapshot,
    mapping: ImportBTBLCHeaderMapping,
    run_id: str,
    reserved_rows: set[int],
    accepted_signatures_by_btb: dict[str, tuple[object, ...]],
    pi_register_provider: ImportPIRegisterProvider,
) -> tuple[dict[str, object], list[WriteOperation]]:
    base = _base_document_outcome(document)
    extraction_decision = str(document.extraction_artifact.get("overall_extraction_decision", "hard_block"))
    if extraction_decision == "hard_block":
        base["decision"] = "hard_block"
        base["hard_block_discrepancies"] = list(
            document.extraction_artifact.get("hard_block_discrepancies", [])
        )
        base["decision_reasons"] = ["Extraction produced hard-block discrepancies."]
        return base, []

    required_missing = [
        field_name
        for field_name, value in (
            ("btb_lc_number", document.btb_lc_number),
            ("btb_lc_date", document.btb_lc_date),
            ("btb_lc_value", document.btb_lc_value),
            ("currency", document.currency),
            ("seller_pi_numbers", document.seller_pi_numbers),
            ("related_export_lc_number", document.related_export_lc_number),
        )
        if value is None or value == ()
    ]
    if required_missing:
        base["decision"] = "hard_block"
        base["hard_block_discrepancies"].append(
            {
                "code": "import_required_field_missing",
                "severity": "hard_block",
                "message": "A required import BTB LC field was missing after extraction.",
                "details": {"missing_fields": required_missing},
            }
        )
        return base, []

    signature = _same_run_signature(document)
    if document.btb_lc_number in accepted_signatures_by_btb:
        if accepted_signatures_by_btb[document.btb_lc_number] == signature:
            base["decision"] = "warning"
            base["write_disposition"] = "duplicate_only_noop"
            base["warnings"].append(
                {
                    "code": "import_duplicate_document_same_run",
                    "severity": "warning",
                    "message": "Duplicate import BTB LC evidence in the same run was ignored.",
                    "details": {"btb_lc_number": document.btb_lc_number},
                }
            )
            base["decision_reasons"] = ["Duplicate import BTB LC already accepted earlier in this run."]
            return base, []
        base["decision"] = "hard_block"
        base["hard_block_discrepancies"].append(
            {
                "code": "import_duplicate_document_conflict",
                "severity": "hard_block",
                "message": "The same BTB LC number appeared with conflicting import evidence in this run.",
                "details": {"btb_lc_number": document.btb_lc_number},
            }
        )
        return base, []

    pi_validation = _validate_document_pi_register(
        document=document,
        pi_register_provider=pi_register_provider,
    )
    base["pi_register_validation"] = pi_validation
    if pi_validation["status"] != "pass":
        base["decision"] = "hard_block"
        base["hard_block_discrepancies"].append(pi_validation["discrepancy"])
        base["decision_reasons"] = [str(pi_validation["reason"])]
        return base, []

    workbook_duplicate = _classify_workbook_duplicate(
        document=document,
        workbook_snapshot=workbook_snapshot,
        mapping=mapping,
    )
    if workbook_duplicate is not None:
        base["candidate_rows"] = workbook_duplicate["candidate_rows"]
        if workbook_duplicate["status"] == "duplicate":
            base["decision"] = "warning"
            base["write_disposition"] = "duplicate_only_noop"
            base["warnings"].append(workbook_duplicate["discrepancy"])
            base["decision_reasons"] = [
                "BTB LC was already recorded in the workbook with matching export LC and import amount."
            ]
            accepted_signatures_by_btb[document.btb_lc_number or ""] = signature
            return base, []
        base["decision"] = "hard_block"
        base["hard_block_discrepancies"].append(workbook_duplicate["discrepancy"])
        base["decision_reasons"] = ["Workbook duplicate evidence could not be verified safely."]
        return base, []

    candidate_result = _select_candidate_row(
        document=document,
        workbook_snapshot=workbook_snapshot,
        mapping=mapping,
        reserved_rows=reserved_rows,
    )
    base["candidate_rows"] = candidate_result["candidate_rows"]
    if candidate_result["status"] != "selected":
        base["decision"] = "hard_block"
        base["hard_block_discrepancies"].append(candidate_result["discrepancy"])
        base["decision_reasons"] = [candidate_result["reason"]]
        return base, []

    row_index = int(candidate_result["selected_row_index"])
    operations = _stage_import_write_operations(
        run_id=run_id,
        document=document,
        workbook_snapshot=workbook_snapshot,
        mapping=mapping,
        row_index=row_index,
        quantity_kgs=str(pi_validation["quantity_kgs"]),
    )
    reserved_rows.add(row_index)
    accepted_signatures_by_btb[document.btb_lc_number or ""] = signature
    base["decision"] = "warning" if base["warnings"] else "pass"
    base["write_disposition"] = "new_writes_staged"
    base["selected_row_index"] = row_index
    base["selected_sl_no"] = _sl_no_for_row(
        workbook_snapshot=workbook_snapshot,
        mapping=mapping,
        row_index=row_index,
    )
    base["selected_quantity_kgs"] = str(pi_validation["quantity_kgs"])
    base["staged_write_operations"] = to_jsonable(operations)
    base["decision_reasons"] = [
        f"Selected workbook row {row_index} for import BTB LC write staging."
    ]
    return base, operations


def _stage_import_write_operations(
    *,
    run_id: str,
    document: ImportBTBLCDocument,
    workbook_snapshot: WorkbookSnapshot,
    mapping: ImportBTBLCHeaderMapping,
    row_index: int,
    quantity_kgs: str,
) -> list[WriteOperation]:
    assert document.btb_lc_number is not None
    assert document.btb_lc_date is not None
    assert document.btb_lc_value_text is not None
    values = (
        ("btb_lc_no", document.btb_lc_number, None),
        ("btb_lc_issue_date", document.btb_lc_date, IMPORT_BTB_LC_DATE_NUMBER_FORMAT),
        ("import_amount", document.btb_lc_value_text, None),
        ("quantity_kgs", quantity_kgs, None),
    )
    operations: list[WriteOperation] = []
    for operation_index, (column_key, value, number_format) in enumerate(values):
        operations.append(
            WriteOperation(
                write_operation_id=build_write_operation_id(
                    run_id=run_id,
                    mail_id=document.document_id,
                    operation_index_within_mail=operation_index,
                    sheet_name=workbook_snapshot.sheet_name,
                    row_index=row_index,
                    column_key=column_key,
                ),
                run_id=run_id,
                mail_id=document.document_id,
                operation_index_within_mail=operation_index,
                sheet_name=workbook_snapshot.sheet_name,
                row_index=row_index,
                column_key=column_key,
                expected_pre_write_value=None,
                expected_post_write_value=value,
                row_eligibility_checks=[
                    "import_related_export_lc_match",
                    "import_up_no_blank",
                    "import_target_cell_blank",
                    "import_value_40_to_80_percent",
                    "import_pi_register_amount_exact_match",
                ],
                number_format=number_format,
            )
        )
    return operations


def _validate_document_pi_register(
    *,
    document: ImportBTBLCDocument,
    pi_register_provider: ImportPIRegisterProvider,
) -> dict[str, object]:
    pi_numbers = list(document.seller_pi_numbers)
    try:
        rows_by_pi = pi_register_provider.lookup_pi_numbers(pi_numbers=pi_numbers)
    except Exception as exc:
        return {
            "status": "hard_block",
            "reason": "Import PI register could not be loaded or parsed.",
            "quantity_kgs": None,
            "total_amount": None,
            "pi_rows": [],
            "discrepancy": {
                "code": "import_pi_register_unavailable",
                "severity": "hard_block",
                "message": "Import PI register data could not be loaded or parsed.",
                "details": {
                    "seller_pi_numbers": pi_numbers,
                    "error": str(exc),
                },
            },
        }

    missing = [pi_number for pi_number in pi_numbers if not rows_by_pi.get(pi_number)]
    pi_evidence = []
    total_amount = Decimal("0")
    total_quantity = Decimal("0")
    for pi_number in pi_numbers:
        matched_rows = rows_by_pi.get(pi_number, [])
        pi_amount = Decimal("0")
        pi_quantity = Decimal("0")
        row_evidence = []
        for row in matched_rows:
            amount = parse_import_pi_decimal(row.total_amount)
            quantity = parse_import_pi_decimal(row.quantity_kg)
            if amount is None or quantity is None:
                return {
                    "status": "hard_block",
                    "reason": "Import PI register row contained an invalid amount or quantity.",
                    "quantity_kgs": None,
                    "total_amount": None,
                    "pi_rows": pi_evidence,
                    "discrepancy": {
                        "code": "import_pi_register_amount_invalid",
                        "severity": "hard_block",
                        "message": "Import PI register row amount or quantity was not a valid decimal.",
                        "details": {
                            "pi_number": pi_number,
                            "source_row_index": row.source_row_index,
                            "total_amount": row.total_amount,
                            "quantity_kg": row.quantity_kg,
                        },
                    },
                }
            pi_amount += amount
            pi_quantity += quantity
            row_evidence.append(
                {
                    "source_row_index": row.source_row_index,
                    "quantity_kg": row.quantity_kg,
                    "total_amount": row.total_amount,
                    "raw_values": row.raw_values,
                }
            )
        total_amount += pi_amount
        total_quantity += pi_quantity
        pi_evidence.append(
            {
                "pi_number": pi_number,
                "row_count": len(matched_rows),
                "source_row_indexes": [row.source_row_index for row in matched_rows],
                "quantity_kg": format_import_pi_decimal(pi_quantity),
                "total_amount": format_import_pi_decimal(pi_amount),
                "rows": row_evidence,
            }
        )

    if missing:
        return {
            "status": "hard_block",
            "reason": "One or more extracted seller PIs were missing from the import PI register.",
            "quantity_kgs": None,
            "total_amount": format_import_pi_decimal(total_amount),
            "pi_rows": pi_evidence,
            "discrepancy": {
                "code": "import_pi_register_row_missing",
                "severity": "hard_block",
                "message": "One or more extracted seller PI numbers were not found in the import PI register.",
                "details": {
                    "missing_pi_numbers": missing,
                    "seller_pi_numbers": pi_numbers,
                    "pi_rows": pi_evidence,
                },
            },
        }

    if document.btb_lc_value is None or total_amount != document.btb_lc_value:
        return {
            "status": "hard_block",
            "reason": "Aggregated import PI register value did not exactly match the extracted BTB LC value.",
            "quantity_kgs": format_import_pi_decimal(total_quantity),
            "total_amount": format_import_pi_decimal(total_amount),
            "pi_rows": pi_evidence,
            "discrepancy": {
                "code": "import_pi_register_amount_mismatch",
                "severity": "hard_block",
                "message": "Aggregated import PI value did not exactly match the extracted BTB LC value.",
                "details": {
                    "btb_lc_number": document.btb_lc_number,
                    "btb_lc_value": document.btb_lc_value_text,
                    "seller_pi_numbers": pi_numbers,
                    "aggregated_pi_total_amount": format_import_pi_decimal(total_amount),
                    "aggregated_pi_quantity_kg": format_import_pi_decimal(total_quantity),
                    "pi_rows": pi_evidence,
                },
            },
        }

    return {
        "status": "pass",
        "reason": "Aggregated import PI register value exactly matched the extracted BTB LC value.",
        "quantity_kgs": format_import_pi_decimal(total_quantity),
        "total_amount": format_import_pi_decimal(total_amount),
        "pi_rows": pi_evidence,
        "discrepancy": None,
    }


def _select_candidate_row(
    *,
    document: ImportBTBLCDocument,
    workbook_snapshot: WorkbookSnapshot,
    mapping: ImportBTBLCHeaderMapping,
    reserved_rows: set[int],
) -> dict[str, object]:
    candidate_rows = []
    for row in sorted(workbook_snapshot.rows, key=lambda item: item.row_index):
        related_match = (
            _canonicalize_workbook_lc(row.values.get(mapping.lc_sc_no, ""))
            == document.related_export_lc_number
        )
        export_amount = _parse_workbook_decimal(row.values.get(mapping.export_amount, ""))
        btb_value = _parse_workbook_decimal(row.values.get(mapping.import_amount, ""))
        btb_no = row.values.get(mapping.btb_lc_no, "").strip()
        issue_date_raw = row.values.get(mapping.btb_lc_issue_date, "").strip()
        import_amount_raw = row.values.get(mapping.import_amount, "").strip()
        quantity_raw = row.values.get(mapping.quantity_kgs, "").strip()
        populated_import_targets = [bool(btb_no), bool(issue_date_raw), bool(import_amount_raw), bool(quantity_raw)]
        partial = related_match and any(populated_import_targets) and not all(populated_import_targets)
        up_blank = not row.values.get(mapping.up_no, "").strip()
        issue_date_blank = not issue_date_raw
        quantity_blank = not quantity_raw
        targets_blank = not btb_no and issue_date_blank and not import_amount_raw and quantity_blank
        value_ratio = None
        value_eligible = False
        if export_amount is not None and document.btb_lc_value is not None and export_amount > 0:
            value_ratio = document.btb_lc_value / export_amount
            value_eligible = Decimal("0.40") <= value_ratio <= Decimal("0.80")
        evidence = {
            "row_index": row.row_index,
            "sl_no": _stringify_sl_no_text(row.values.get(mapping.sl_no, "")),
            "related_export_lc_raw": row.values.get(mapping.lc_sc_no, ""),
            "related_export_lc_canonical": _canonicalize_workbook_lc(row.values.get(mapping.lc_sc_no, "")),
            "related_export_lc_matches": related_match,
            "up_no_blank": up_blank,
            "btb_lc_no_blank": not btb_no,
            "btb_lc_issue_date_blank": issue_date_blank,
            "import_amount_blank": not import_amount_raw,
            "quantity_kgs_blank": quantity_blank,
            "partial_import_target_state": partial,
            "reserved_in_run": row.row_index in reserved_rows,
            "export_amount_raw": row.values.get(mapping.export_amount, ""),
            "export_amount_canonical": str(export_amount) if export_amount is not None else None,
            "value_ratio": str(value_ratio) if value_ratio is not None else None,
            "value_eligible_40_to_80": value_eligible,
        }
        if related_match:
            candidate_rows.append(evidence)

    eligible = [
        candidate
        for candidate in candidate_rows
        if candidate["related_export_lc_matches"]
        and candidate["up_no_blank"]
        and candidate["btb_lc_no_blank"]
        and candidate["btb_lc_issue_date_blank"]
        and candidate["import_amount_blank"]
        and candidate["quantity_kgs_blank"]
        and not candidate["reserved_in_run"]
        and candidate["value_eligible_40_to_80"]
    ]
    if not eligible:
        return {
            "status": "hard_block",
            "candidate_rows": candidate_rows,
            "reason": "No qualified workbook row was available for this import BTB LC.",
            "discrepancy": {
                "code": "import_no_qualified_workbook_row",
                "severity": "hard_block",
                "message": "No workbook row matched related export LC, blank target, UP blank, reservation, and value rules.",
                "details": {
                    "btb_lc_number": document.btb_lc_number,
                    "btb_lc_value": document.btb_lc_value_text,
                    "related_export_lc_number": document.related_export_lc_number,
                    "candidate_rows": candidate_rows,
                },
            },
        }
    selected = sorted(
        eligible,
        key=lambda candidate: (
            -Decimal(str(candidate["value_ratio"])),
            int(candidate["row_index"]),
        ),
    )[0]
    return {
        "status": "selected",
        "selected_row_index": selected["row_index"],
        "candidate_rows": candidate_rows,
    }


def _classify_workbook_duplicate(
    *,
    document: ImportBTBLCDocument,
    workbook_snapshot: WorkbookSnapshot,
    mapping: ImportBTBLCHeaderMapping,
) -> dict[str, object] | None:
    matches = []
    for row in sorted(workbook_snapshot.rows, key=lambda item: item.row_index):
        raw_btb = row.values.get(mapping.btb_lc_no, "").strip()
        if raw_btb != document.btb_lc_number:
            continue
        import_amount = _parse_workbook_decimal(row.values.get(mapping.import_amount, ""))
        quantity_kgs = _parse_workbook_decimal(row.values.get(mapping.quantity_kgs, ""))
        issue_date = _normalize_date_value(row.values.get(mapping.btb_lc_issue_date, ""))
        related = _canonicalize_workbook_lc(row.values.get(mapping.lc_sc_no, ""))
        matches.append(
            {
                "row_index": row.row_index,
                "btb_lc_no": raw_btb,
                "related_export_lc_raw": row.values.get(mapping.lc_sc_no, ""),
                "related_export_lc_canonical": related,
                "import_amount_raw": row.values.get(mapping.import_amount, ""),
                "import_amount_canonical": str(import_amount) if import_amount is not None else None,
                "quantity_kgs_raw": row.values.get(mapping.quantity_kgs, ""),
                "quantity_kgs_canonical": str(quantity_kgs) if quantity_kgs is not None else None,
                "btb_lc_issue_date_raw": row.values.get(mapping.btb_lc_issue_date, ""),
                "btb_lc_issue_date_canonical": issue_date,
                "matches_related_export_lc": related == document.related_export_lc_number,
                "matches_import_amount": import_amount == document.btb_lc_value,
                "matches_btb_lc_issue_date": (
                    issue_date is None or issue_date == document.btb_lc_date
                ),
            }
        )
    if not matches:
        return None
    exact = [
        match
        for match in matches
        if match["matches_related_export_lc"]
        and match["matches_import_amount"]
        and match["matches_btb_lc_issue_date"]
    ]
    if len(matches) == 1 and len(exact) == 1:
        return {
            "status": "duplicate",
            "candidate_rows": matches,
            "discrepancy": {
                "code": "import_duplicate_document_in_workbook",
                "severity": "warning",
                "message": "Import BTB LC is already recorded in the workbook with matching evidence.",
                "details": {"workbook_duplicate_row": exact[0]},
            },
        }
    return {
        "status": "hard_block",
        "candidate_rows": matches,
        "discrepancy": {
            "code": "import_workbook_duplicate_unverifiable",
            "severity": "hard_block",
            "message": "Workbook contains BTB LC duplicate evidence that cannot be verified as one exact duplicate.",
            "details": {"workbook_duplicate_rows": matches},
        },
    }


def _base_document_outcome(document: ImportBTBLCDocument) -> dict[str, object]:
    return {
        "document_id": document.document_id,
        "source_path": document.source_path,
        "filename": document.filename,
        "file_sha256": document.file_sha256,
        "snapshot_index": document.snapshot_index,
        "attachment_index": document.attachment_index,
        "extracted_fields": {
            "btb_lc_number": document.btb_lc_number,
            "btb_lc_date": document.btb_lc_date,
            "btb_lc_value": document.btb_lc_value_text,
            "currency": document.currency,
            "seller_pi_numbers": list(document.seller_pi_numbers),
            "related_export_lc_number": document.related_export_lc_number,
        },
        "extraction_field_evidence": document.extraction_artifact.get("fields", {}),
        "bank_detection": document.extraction_artifact.get("bank_detection"),
        "filename_comparison": document.extraction_artifact.get("filename_comparison"),
        "decision": "pass",
        "write_disposition": "not_staged",
        "selected_row_index": None,
        "selected_sl_no": None,
        "selected_quantity_kgs": None,
        "pi_register_validation": None,
        "candidate_rows": [],
        "staged_write_operations": [],
        "warnings": list(document.extraction_artifact.get("warnings", [])),
        "hard_block_discrepancies": [],
        "decision_reasons": [],
    }


def _blocked_document_outcome(
    *,
    document: ImportBTBLCDocument,
    code: str,
    message: str,
    details: dict[str, object],
) -> dict[str, object]:
    outcome = _base_document_outcome(document)
    outcome["decision"] = "hard_block"
    outcome["hard_block_discrepancies"] = [
        {
            "code": code,
            "severity": "hard_block",
            "message": message,
            "details": details,
        }
    ]
    outcome["decision_reasons"] = [message]
    return outcome


def _document_from_artifact(
    *,
    artifact: dict[str, object],
    snapshot_index: int,
    attachment_index: int | None,
) -> ImportBTBLCDocument:
    source = artifact.get("source")
    if not isinstance(source, dict):
        raise ValueError("Import extraction artifact is missing source metadata")
    fields = artifact.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("Import extraction artifact is missing field payloads")
    btb_value_text = _field_canonical(fields, "btb_lc_value")
    return ImportBTBLCDocument(
        document_id=f"import-doc-{snapshot_index:04d}-{canonical_json_hash(source)[:12]}",
        source_path=str(source.get("path") or ""),
        filename=str(source.get("filename") or Path(str(source.get("path") or "")).name),
        file_sha256=str(source.get("file_sha256") or ""),
        snapshot_index=snapshot_index,
        attachment_index=attachment_index,
        extraction_artifact=artifact,
        btb_lc_number=_field_canonical(fields, "btb_lc_number"),
        btb_lc_date=_field_canonical(fields, "btb_lc_date"),
        btb_lc_value=_parse_canonical_decimal(btb_value_text),
        btb_lc_value_text=btb_value_text,
        currency=_field_canonical(fields, "currency"),
        seller_pi_numbers=tuple(_field_canonical_list(fields, "seller_pi_numbers")),
        related_export_lc_number=_field_canonical(fields, "related_export_lc_number"),
    )


def _load_or_extract_artifact(source: Path, *, extraction_directory: Path) -> dict[str, object]:
    if source.suffix.casefold() == ".json":
        with source.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError(f"Import extraction artifact must be a JSON object: {source}")
        return payload
    artifact = extract_import_btb_lc_pdf(pdf_path=source)
    output_path = extraction_directory / f"{source.name}.import-btb-lc.json"
    atomic_write_text(output_path, pretty_json_dumps(artifact))
    return artifact


def _resolve_import_inputs(
    input_path: Path | Iterable[Path],
    *,
    import_document_root: Path | None = None,
) -> list[Path]:
    inputs = _coerce_import_input_paths(input_path)
    root = import_document_root.resolve() if import_document_root is not None else None
    paths_by_key: dict[str, Path] = {}
    for raw_input in inputs:
        resolved = raw_input.resolve()
        if resolved.is_file():
            if resolved.suffix.casefold() not in {".pdf", ".json"}:
                raise ValueError(f"Input file must be a PDF or import extraction JSON: {raw_input}")
            _validate_file_picker_source_root(resolved, root=root)
            paths_by_key.setdefault(os.path.normcase(str(resolved)), resolved)
            continue
        if not resolved.is_dir():
            raise ValueError(f"Input path does not exist: {raw_input}")
        for path in resolved.iterdir():
            child = path.resolve()
            if child.is_file() and child.suffix.casefold() in {".pdf", ".json"}:
                _validate_file_picker_source_root(child, root=root)
                paths_by_key.setdefault(os.path.normcase(str(child)), child)
    paths = sorted(paths_by_key.values(), key=lambda path: os.path.normcase(str(path)))
    if not paths:
        raise ValueError("Input path contains no PDF or JSON files.")
    return paths


def _coerce_import_input_paths(input_path: Path | Iterable[Path]) -> list[Path]:
    if isinstance(input_path, (str, os.PathLike)):
        return [Path(input_path)]
    paths = [Path(path) for path in input_path]
    if not paths:
        raise ValueError("At least one import BTB LC input path is required.")
    return paths


def _validate_file_picker_source_root(path: Path, *, root: Path | None) -> None:
    if root is None or path.suffix.casefold() == ".json":
        return
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "Selected import BTB LC PDFs must resolve beneath import_document_root. "
            f"Source: {path}; import_document_root: {root}"
        ) from exc


def load_import_workbook_snapshot(
    *,
    workbook_json: Path | None,
    workbook_path: Path | None,
) -> WorkbookSnapshot:
    if workbook_json is not None and workbook_path is not None:
        raise ValueError("Choose either --workbook-json or --workbook, not both for snapshot input")
    if workbook_json is not None:
        from project.workbook import JsonManifestWorkbookSnapshotProvider

        return JsonManifestWorkbookSnapshotProvider(workbook_json).load_snapshot()
    if workbook_path is not None:
        return XLWingsWorkbookSnapshotProvider(workbook_path).load_snapshot()
    raise ValueError("Import BTB LC workflow requires --workbook-json or --workbook")


def _field_canonical(fields: dict[str, object], field_name: str) -> str | None:
    field = fields.get(field_name)
    if not isinstance(field, dict):
        return None
    canonical = field.get("canonical")
    return canonical if isinstance(canonical, str) and canonical.strip() else None


def _field_canonical_list(fields: dict[str, object], field_name: str) -> list[str]:
    field = fields.get(field_name)
    if not isinstance(field, dict):
        return []
    canonical = field.get("canonical")
    if not isinstance(canonical, list):
        return []
    return [item for item in canonical if isinstance(item, str) and item.strip()]


def _allocation_sort_key(document: ImportBTBLCDocument) -> tuple[object, ...]:
    value_sort = -document.btb_lc_value if document.btb_lc_value is not None else Decimal("0")
    return (
        document.related_export_lc_number or "",
        value_sort,
        document.btb_lc_number or "",
        document.snapshot_index,
        document.attachment_index if document.attachment_index is not None else 999999,
        document.source_path.casefold(),
    )


def _same_run_signature(document: ImportBTBLCDocument) -> tuple[object, ...]:
    return (
        document.btb_lc_number,
        document.btb_lc_date,
        document.btb_lc_value_text,
        document.currency,
        document.seller_pi_numbers,
        document.related_export_lc_number,
    )


def _canonicalize_workbook_lc(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    direct = _canonicalize_extracted_related_lc(text, None)
    if direct is not None:
        return direct
    for hint in ("LC", "SC"):
        candidate = _canonicalize_extracted_related_lc(text, hint)
        if candidate is not None:
            return candidate
    return None


def _parse_canonical_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _parse_workbook_decimal(value: object) -> Decimal | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidates = [text, text.replace(",", "")]
    if "," in text and "." not in text:
        candidates.append(text.replace(",", "."))
    for candidate in candidates:
        try:
            return Decimal(candidate)
        except InvalidOperation:
            continue
    return None


def _resolve_header(
    headers: list[WorkbookHeader],
    required_text: str,
    aliases: tuple[str, ...] = (),
) -> int | None:
    matches = _resolve_header_candidates(headers, required_text, aliases=aliases)
    return matches[0] if len(matches) == 1 else None


def _resolve_header_candidates(
    headers: list[WorkbookHeader],
    required_text: str,
    aliases: tuple[str, ...] = (),
    required_column_index: int | None = None,
) -> list[int]:
    allowed = {_normalize_header(required_text), *(_normalize_header(alias) for alias in aliases)}
    matches = [
        header.column_index
        for header in headers
        if _normalize_header(header.text) in allowed
        and (required_column_index is None or header.column_index == required_column_index)
    ]
    return matches


def _normalize_header(value: str) -> str:
    return " ".join(value.strip().casefold().replace(".", "").split())


def _sl_no_for_row(
    *,
    workbook_snapshot: WorkbookSnapshot,
    mapping: ImportBTBLCHeaderMapping,
    row_index: int,
) -> str:
    row = next((item for item in workbook_snapshot.rows if item.row_index == row_index), None)
    if row is None:
        return ""
    return _stringify_sl_no_text(row.values.get(mapping.sl_no, ""))


def _stringify_sl_no_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return ""
        if "." not in text_value and "e" not in text_value.lower():
            return text_value
        try:
            decimal_value = Decimal(text_value.replace(",", ""))
        except (InvalidOperation, ValueError):
            return text_value
        if decimal_value == decimal_value.to_integral_value():
            return str(int(decimal_value))
        return text_value
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return str(int(value))
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        decimal_value = Decimal(str(value))
        if decimal_value == decimal_value.to_integral_value():
            return str(int(decimal_value))
        return str(value)
    text_value = str(value).strip()
    if not text_value:
        return ""
    try:
        decimal_value = Decimal(text_value.replace(",", ""))
    except (InvalidOperation, ValueError):
        return text_value
    if decimal_value == decimal_value.to_integral_value():
        return str(int(decimal_value))
    return text_value


def _format_import_report_date(value: object) -> str:
    normalized = _normalize_date_value(value)
    if normalized is None:
        return str(value or "")
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return str(value or "")


def _import_report_snapshot_rows(
    *,
    report: dict[str, object],
    generated_at_display: str,
    state_timezone: str,
) -> list[tuple[str, object]]:
    return [
        ("Run ID", report.get("run_id", "")),
        ("Workflow ID", report.get("workflow_id", "")),
        ("Schema", f"{report.get('schema_id', '')} {report.get('schema_version', '')}".strip()),
        ("Report Schema", report.get("report_schema_version", "")),
        ("Launcher Path", report.get("launcher_path", "")),
        ("Generated At", f"{generated_at_display} ({state_timezone})"),
        ("Overall Decision", report.get("overall_decision", "")),
    ]


def _format_import_report_timestamp(value: object, *, state_timezone: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    timezone = validate_timezone(state_timezone)
    return parsed.astimezone(timezone).strftime("%d/%m/%Y %I:%M:%S %p")


def _render_import_report_key_value_section(title: str, rows: list[tuple[str, object]]) -> str:
    items = "\n".join(
        f"        <tr><th>{escape(str(label))}</th><td>{escape(str(value))}</td></tr>"
        for label, value in rows
    )
    return "\n".join(
        [
            '    <section class="section">',
            f"      <h2>{escape(title)}</h2>",
            "      <table>",
            "        <tbody>",
            items,
            "        </tbody>",
            "      </table>",
            "    </section>",
        ]
    )


def _format_related_export_lc_display(value: object) -> str:
    text = str(value or "").strip()
    if text.upper().startswith("LC-"):
        return text[3:]
    return text


def _format_import_candidate_evidence(value: object) -> str:
    if not isinstance(value, list):
        return ""
    rendered = []
    for candidate in value:
        if not isinstance(candidate, dict):
            continue
        ratio = candidate.get("value_ratio")
        ratio_display = ""
        if ratio not in {None, ""}:
            try:
                ratio_display = f"{Decimal(str(ratio)) * Decimal('100'):.4f}%"
            except InvalidOperation:
                ratio_display = str(ratio)
        target_states = ", ".join(
            f"{label}={'yes' if bool(candidate.get(key)) else 'no'}"
            for key, label in (
                ("up_no_blank", "UP blank"),
                ("btb_lc_no_blank", "BTB blank"),
                ("btb_lc_issue_date_blank", "date blank"),
                ("import_amount_blank", "amount blank"),
                ("quantity_kgs_blank", "quantity blank"),
                ("reserved_in_run", "reserved"),
                ("value_eligible_40_to_80", "value eligible"),
            )
        )
        evidence_parts = [
            f"SL.No. {candidate.get('sl_no') or ''}",
            f"row {candidate.get('row_index') or ''}",
            f"export LC {candidate.get('related_export_lc_raw') or ''}",
            "export amount "
            + str(
                candidate.get("export_amount_canonical")
                or candidate.get("export_amount_raw")
                or ""
            ),
            f"ratio {ratio_display}",
            target_states,
        ]
        rendered.append("; ".join(evidence_parts))
    return " | ".join(rendered)


def _format_import_raw_field_evidence(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    labels = (
        ("btb_lc_number", "BTB LC"),
        ("btb_lc_date", "issue date"),
        ("btb_lc_value", "value"),
        ("currency", "currency"),
        ("seller_pi_numbers", "seller PI"),
        ("related_export_lc_number", "related export LC"),
    )
    rendered = []
    for field_name, label in labels:
        field = value.get(field_name)
        if not isinstance(field, dict):
            continue
        raw = field.get("raw")
        if isinstance(raw, list):
            raw_text = ", ".join(str(item) for item in raw)
        else:
            raw_text = str(raw or "")
        if raw_text:
            rendered.append(f"{label}={raw_text}")
    return "; ".join(rendered)


def _format_import_report_messages(value: object) -> str:
    if not isinstance(value, list):
        return str(value or "")
    return " | ".join(str(item) for item in value if str(item or "").strip())


def _format_import_report_issues(value: object) -> str:
    if not isinstance(value, list):
        return str(value or "")
    rendered = []
    for item in value:
        if not isinstance(item, dict):
            if str(item or "").strip():
                rendered.append(str(item))
            continue
        code = str(item.get("code") or "").strip()
        message = str(item.get("message") or "").strip()
        details = item.get("details")
        details_text = ""
        if details:
            details_text = json.dumps(
                details,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        rendered.append(
            ": ".join(part for part in (code, message) if part)
            + (f" ({details_text})" if details_text else "")
        )
    return " | ".join(rendered)


def _import_column_index_by_key(mapping: ImportBTBLCHeaderMapping) -> dict[str, int]:
    return {
        "btb_lc_no": mapping.btb_lc_no,
        "btb_lc_issue_date": mapping.btb_lc_issue_date,
        "import_amount": mapping.import_amount,
        "quantity_kgs": mapping.quantity_kgs,
    }


def _normalize_cell_value(value: object) -> str | None:
    if value is None:
        return None
    return str(value).strip()


def _collect_import_post_write_probes(
    *,
    session,
    mapping: ImportBTBLCHeaderMapping,
    staged_write_plan: list[WriteOperation],
) -> list[dict[str, object]]:
    column_by_key = _import_column_index_by_key(mapping)
    probes: list[dict[str, object]] = []
    for operation in staged_write_plan:
        column_index = column_by_key[operation.column_key]
        observed = session.read_cell(
            sheet_name=operation.sheet_name,
            row_index=operation.row_index,
            column_index=column_index,
        )
        expected = operation.expected_post_write_value
        classification = (
            "matches_post_write"
            if _post_write_value_matches(observed, expected)
            else "mismatch_unknown"
        )
        probes.append(
            {
                "write_operation_id": operation.write_operation_id,
                "run_id": operation.run_id,
                "mail_id": operation.mail_id,
                "probe_stage": "post_write",
                "sheet_name": operation.sheet_name,
                "row_index": operation.row_index,
                "column_key": operation.column_key,
                "column_index": column_index,
                "expected_post_write_value": expected,
                "observed_value": observed,
                "classification": classification,
            }
        )
    return probes


def _post_write_value_matches(observed: object, expected: object) -> bool:
    observed_date = _normalize_date_value(observed)
    expected_date = _normalize_date_value(expected)
    if observed_date is not None and expected_date is not None:
        return observed_date == expected_date
    observed_decimal = _parse_workbook_decimal(observed)
    expected_decimal = _parse_workbook_decimal(expected)
    if observed_decimal is not None and expected_decimal is not None:
        return observed_decimal == expected_decimal
    return str(observed or "").strip() == str(expected or "").strip()


def _coerce_import_write_value(value: object, *, number_format: str | None) -> object:
    if value is None or number_format is None:
        return value
    normalized_format = number_format.lower()
    if "d" not in normalized_format or "y" not in normalized_format:
        return value
    normalized_date = _normalize_date_value(value)
    if normalized_date is None:
        return value
    return date.fromisoformat(normalized_date)


def _normalize_date_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed_datetime = datetime.fromisoformat(text)
        return parsed_datetime.date().isoformat()
    except ValueError:
        pass
    for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(text, date_format).date().isoformat()
        except ValueError:
            continue
    return None


def _summarize_outcomes(outcomes: list[dict[str, object]]) -> dict[str, int]:
    return {
        "pass": sum(1 for outcome in outcomes if outcome.get("decision") == "pass"),
        "warning": sum(1 for outcome in outcomes if outcome.get("decision") == "warning"),
        "hard_block": sum(1 for outcome in outcomes if outcome.get("decision") == "hard_block"),
        "staged": sum(1 for outcome in outcomes if outcome.get("write_disposition") == "new_writes_staged"),
        "duplicate_only": sum(1 for outcome in outcomes if outcome.get("write_disposition") == "duplicate_only_noop"),
    }


def _build_current_full_mail_outcomes(
    *,
    mail_snapshot: list[EmailMessage],
    relevance_by_mail_id: dict[str, dict[str, object]],
    document_outcomes: object,
    document_mail_id: dict[str, str],
    acquisition_discrepancies: list[dict[str, object]],
    run_id: str,
) -> list[dict[str, object]]:
    outcome_list = document_outcomes if isinstance(document_outcomes, list) else []
    documents_by_mail: dict[str, list[dict[str, object]]] = {}
    for outcome in outcome_list:
        if not isinstance(outcome, dict):
            continue
        mail_id = document_mail_id.get(str(outcome.get("document_id", "")))
        if mail_id is None:
            continue
        documents_by_mail.setdefault(mail_id, []).append(outcome)
    acquisition_by_mail: dict[str, list[dict[str, object]]] = {}
    for discrepancy in acquisition_discrepancies:
        mail_id = str(discrepancy.get("mail_id", ""))
        acquisition_by_mail.setdefault(mail_id, []).append(discrepancy)

    mail_outcomes: list[dict[str, object]] = []
    for mail in sorted(mail_snapshot, key=lambda item: item.snapshot_index):
        relevance = relevance_by_mail_id[mail.mail_id]
        documents = documents_by_mail.get(mail.mail_id, [])
        mail_acquisition_discrepancies = acquisition_by_mail.get(mail.mail_id, [])
        if not relevance["eligible"]:
            decision = "pass"
            processing_disposition = "not_applicable"
            write_disposition = "not_applicable"
            reasons = ["Mail subject did not match import BTB LC relevance keywords."]
        elif mail_acquisition_discrepancies or any(document.get("decision") == "hard_block" for document in documents):
            decision = "hard_block"
            processing_disposition = "blocked"
            write_disposition = (
                _mail_write_disposition(documents) if documents else "not_staged"
            )
            reasons = [
                "One or more import BTB LC documents in the mail hard-blocked; "
                "independently writable documents retained their staged writes."
            ]
        elif not documents:
            decision = "hard_block"
            processing_disposition = "blocked"
            write_disposition = "not_staged"
            mail_acquisition_discrepancies = [
                _mail_discrepancy(
                    mail_id=mail.mail_id,
                    code="import_required_document_missing",
                    message="No deterministic import BTB LC PDF was extracted from the relevant mail.",
                    details={"subject_raw": mail.subject_raw},
                )
            ]
            reasons = ["Relevant mail did not produce a deterministic import BTB LC document."]
        else:
            decisions = {str(document.get("decision")) for document in documents}
            decision = "warning" if "warning" in decisions else "pass"
            processing_disposition = "validated"
            write_disposition = _mail_write_disposition(documents)
            reasons = ["Import BTB LC mail processed without hard-block discrepancies."]
        mail_outcomes.append(
            {
                "run_id": run_id,
                "mail_id": mail.mail_id,
                "workflow_id": WorkflowId.IMPORT_BTB_LC.value,
                "snapshot_index": mail.snapshot_index,
                "source_entry_id": mail.entry_id,
                "subject_raw": mail.subject_raw,
                "sender_address": mail.sender_address,
                "import_relevance": relevance,
                "import_keyword_revision": relevance["import_keyword_revision"],
                "processing_disposition": processing_disposition,
                "final_decision": decision,
                "write_disposition": write_disposition,
                "eligible_for_mail_move": bool(relevance["eligible"] and decision != "hard_block"),
                "document_ids": [document.get("document_id") for document in documents],
                "btb_lc_numbers_extracted": _mail_field_projection(documents, "btb_lc_number"),
                "pi_numbers_extracted": _mail_pi_projection(documents),
                "related_export_lc_numbers_extracted": _mail_field_projection(documents, "related_export_lc_number"),
                "discrepancies": list(mail_acquisition_discrepancies)
                + [
                    discrepancy
                    for document in documents
                    for discrepancy in document.get("hard_block_discrepancies", [])
                    if isinstance(discrepancy, dict)
                ],
                "warnings": [
                    warning
                    for document in documents
                    for warning in document.get("warnings", [])
                    if isinstance(warning, dict)
                ],
                "decision_reasons": reasons,
            }
        )
    return mail_outcomes


def _mail_write_disposition(documents: list[dict[str, object]]) -> str:
    dispositions = {str(document.get("write_disposition", "")) for document in documents}
    if dispositions == {"duplicate_only_noop"}:
        return "duplicate_only_noop"
    if "new_writes_staged" in dispositions and "duplicate_only_noop" in dispositions:
        return "mixed_duplicate_and_new_writes"
    if "new_writes_staged" in dispositions:
        return "new_writes_staged"
    return "no_write_noop"


def _mail_field_projection(documents: list[dict[str, object]], field_name: str) -> list[str]:
    values = []
    for document in documents:
        fields = document.get("extracted_fields")
        if not isinstance(fields, dict):
            continue
        value = fields.get(field_name)
        if isinstance(value, str) and value:
            values.append(value)
    return values


def _mail_pi_projection(documents: list[dict[str, object]]) -> list[str]:
    values = []
    for document in documents:
        fields = document.get("extracted_fields")
        if not isinstance(fields, dict):
            continue
        pi_numbers = fields.get("seller_pi_numbers")
        if isinstance(pi_numbers, list):
            values.extend(str(value) for value in pi_numbers if str(value).strip())
    return values


def _mail_discrepancy(
    *,
    mail_id: str,
    code: str,
    message: str,
    details: dict[str, object],
) -> dict[str, object]:
    return {
        "mail_id": mail_id,
        "code": code,
        "severity": "hard_block",
        "message": message,
        "details": details,
    }


def _execute_import_current_mail_moves(
    *,
    run_id: str,
    mail_outcomes: list[dict[str, object]],
    source_folder_entry_id: str | None,
    destination_folder_entry_id: str | None,
    move_mails: bool,
    mail_move_provider: MailMoveProvider | None,
    write_execution: dict[str, object],
    staged_write_plan: list[WriteOperation],
) -> dict[str, object]:
    operations = []
    if not move_mails:
        return {"requested": False, "status": "not_requested", "operations": operations, "discrepancies": []}
    if not source_folder_entry_id or not destination_folder_entry_id:
        return {
            "requested": True,
            "status": "hard_blocked",
            "operations": operations,
            "discrepancies": [
                {
                    "code": "mail_move_gate_unsatisfied",
                    "severity": "hard_block",
                    "message": "Import mail move requires configured source and destination folders.",
                    "details": {},
                }
            ],
        }
    write_status = str(write_execution.get("status", ""))
    if staged_write_plan and write_status != "committed":
        return {
            "requested": True,
            "status": "hard_blocked",
            "operations": operations,
            "discrepancies": [
                {
                    "code": "mail_move_gate_unsatisfied",
                    "severity": "hard_block",
                    "message": "Import mail move is blocked until staged workbook writes commit.",
                    "details": {"write_execution_status": write_status},
                }
            ],
        }
    if mail_move_provider is None:
        return {
            "requested": True,
            "status": "hard_blocked",
            "operations": operations,
            "discrepancies": [
                {
                    "code": "mail_move_runtime_error",
                    "severity": "hard_block",
                    "message": "No import mail move provider was supplied.",
                    "details": {},
                }
            ],
        }
    for outcome in mail_outcomes:
        if not outcome.get("eligible_for_mail_move"):
            continue
        operation = MailMoveOperation(
            mail_move_operation_id=build_mail_move_operation_id(
                run_id,
                str(outcome["source_entry_id"]),
                destination_folder_entry_id,
            ),
            run_id=run_id,
            mail_id=str(outcome["mail_id"]),
            entry_id=str(outcome["source_entry_id"]),
            source_folder=source_folder_entry_id,
            destination_folder=destination_folder_entry_id,
            moved_at_utc=None,
            move_status="pending",
        )
        try:
            receipt = mail_move_provider.move_mail(operation)
        except Exception as exc:
            return {
                "requested": True,
                "status": "uncertain_incomplete",
                "operations": operations,
                "discrepancies": [
                    {
                        "code": "mail_move_runtime_error",
                        "severity": "hard_block",
                        "message": "A runtime error interrupted import mail movement.",
                        "details": {"mail_id": outcome["mail_id"], "error": str(exc)},
                    }
                ],
            }
        operations.append(
            {
                **to_jsonable(operation),
                "move_status": "moved",
                "move_execution_receipt": to_jsonable(receipt),
                "moved_at_utc": utc_timestamp(),
            }
        )
    return {
        "requested": True,
        "status": "completed",
        "operations": operations,
        "discrepancies": [],
    }


def _current_full_overall_decision(
    *,
    mail_outcomes: list[dict[str, object]],
    write_execution: dict[str, object],
    mail_move: dict[str, object],
) -> str:
    if any(outcome.get("final_decision") == "hard_block" for outcome in mail_outcomes):
        return "hard_block"
    if write_execution.get("status") in {"hard_blocked_no_write", "uncertain_not_committed"}:
        return "hard_block"
    if mail_move.get("status") in {"hard_blocked", "uncertain_incomplete"}:
        return "hard_block"
    if any(outcome.get("final_decision") == "warning" for outcome in mail_outcomes):
        return "warning"
    return "pass"


def _mail_decision_counts(mail_outcomes: list[dict[str, object]]) -> dict[str, int]:
    return {
        decision: sum(1 for outcome in mail_outcomes if outcome.get("final_decision") == decision)
        for decision in ("pass", "warning", "hard_block")
    }


def _mail_write_disposition_counts(mail_outcomes: list[dict[str, object]]) -> dict[str, int]:
    dispositions = (
        "new_writes_staged",
        "mixed_duplicate_and_new_writes",
        "duplicate_only_noop",
        "no_write_noop",
        "not_staged",
        "not_applicable",
    )
    return {
        disposition: sum(1 for outcome in mail_outcomes if outcome.get("write_disposition") == disposition)
        for disposition in dispositions
    }


def _decision_counts(outcomes: object) -> dict[str, int]:
    outcome_list = outcomes if isinstance(outcomes, list) else []
    return {
        decision: sum(
            1
            for outcome in outcome_list
            if isinstance(outcome, dict) and outcome.get("decision") == decision
        )
        for decision in ("pass", "warning", "hard_block")
    }


def _write_disposition_counts(outcomes: object) -> dict[str, int]:
    outcome_list = outcomes if isinstance(outcomes, list) else []
    dispositions = ("new_writes_staged", "duplicate_only_noop", "not_staged")
    return {
        disposition: sum(
            1
            for outcome in outcome_list
            if isinstance(outcome, dict) and outcome.get("write_disposition") == disposition
        )
        for disposition in dispositions
    }


def _selected_row_summary(outcomes: object) -> list[dict[str, object]]:
    outcome_list = outcomes if isinstance(outcomes, list) else []
    rows: list[dict[str, object]] = []
    for outcome in outcome_list:
        if not isinstance(outcome, dict) or outcome.get("selected_row_index") is None:
            continue
        fields = outcome.get("extracted_fields")
        if not isinstance(fields, dict):
            fields = {}
        rows.append(
            {
                "document_id": outcome.get("document_id"),
                "filename": outcome.get("filename"),
                "btb_lc_number": fields.get("btb_lc_number"),
                "btb_lc_issue_date": fields.get("btb_lc_date"),
                "btb_lc_value": fields.get("btb_lc_value"),
                "related_export_lc_number": fields.get("related_export_lc_number"),
                "selected_sl_no": outcome.get("selected_sl_no"),
                "selected_row_index": outcome.get("selected_row_index"),
                "quantity_kgs": outcome.get("selected_quantity_kgs"),
            }
        )
    return rows


def _overall_decision_from_document_outcomes(
    outcomes: Iterable[dict[str, object]],
    *,
    write_execution: dict[str, object] | None = None,
) -> str:
    outcome_list = list(outcomes)
    if any(outcome.get("decision") == "hard_block" for outcome in outcome_list):
        return "hard_block"
    if write_execution and write_execution.get("status") in {
        "hard_blocked_no_write",
        "uncertain_not_committed",
    }:
        return "hard_block"
    if any(outcome.get("decision") == "warning" for outcome in outcome_list):
        return "warning"
    return "pass"
