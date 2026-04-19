from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from project.documents.providers import SavedDocumentAnalysisProvider
from project.erp import ERPFamily
from project.erp.normalization import normalize_buyer_name_for_paths, normalize_lc_sc_date, normalize_lc_sc_number
from project.models import EmailMessage, FinalDecision, SavedDocument
from project.storage.document_saving import (
    DocumentSaveIssue,
    DocumentSaveResult,
    build_export_attachment_directory,
)
from project.storage.providers import AttachmentContentProvider
from project.utils.hashing import sha256_file
from project.utils.ids import build_saved_document_id
from project.workbook import WorkbookRow, WorkbookSnapshot, resolve_ud_ip_exp_storage_header_mapping
from project.workflows.ud_ip_exp.document_classification import (
    ClassifiedUDIPEXPDocumentSet,
    classify_saved_ud_ip_exp_documents,
)
from project.workflows.ud_ip_exp.payloads import UDIPEXPDocumentPayload


@dataclass(slots=True, frozen=True)
class UDIPEXPLiveDocumentPreparationResult:
    document_save_result: DocumentSaveResult
    classified_documents: ClassifiedUDIPEXPDocumentSet


def prepare_live_ud_ip_exp_documents(
    *,
    run_id: str,
    mail: EmailMessage,
    workbook_snapshot: WorkbookSnapshot | None,
    document_root: Path,
    provider: AttachmentContentProvider,
    analysis_provider: SavedDocumentAnalysisProvider,
    documents_override: list[UDIPEXPDocumentPayload] | None = None,
) -> UDIPEXPLiveDocumentPreparationResult:
    staging_directory = document_root / ".staging" / "ud_ip_exp" / run_id / mail.mail_id
    staging_directory.mkdir(parents=True, exist_ok=True)
    staged_result = _save_mail_pdfs_to_directory(
        mail=mail,
        destination_directory=staging_directory,
        provider=provider,
    )
    if staged_result.issues or not staged_result.saved_documents:
        _cleanup_directory(staging_directory)
        return UDIPEXPLiveDocumentPreparationResult(
            document_save_result=staged_result,
            classified_documents=ClassifiedUDIPEXPDocumentSet(
                saved_documents=list(staged_result.saved_documents),
                documents=[],
                decision_reasons=[],
                discrepancies=[],
            ),
        )

    staged_classified = classify_saved_ud_ip_exp_documents(
        saved_documents=staged_result.saved_documents,
        analysis_provider=analysis_provider,
    )
    family = _resolve_family_from_documents(
        documents=list(documents_override or staged_classified.documents),
        workbook_snapshot=workbook_snapshot,
    )
    if isinstance(family, DocumentSaveIssue):
        _cleanup_directory(staging_directory)
        return UDIPEXPLiveDocumentPreparationResult(
            document_save_result=DocumentSaveResult(
                saved_documents=[],
                issues=[family],
                decision_reasons=["Attachment saving blocked because the UD/IP/EXP storage path was not resolvable."],
            ),
            classified_documents=ClassifiedUDIPEXPDocumentSet(
                saved_documents=[],
                documents=[],
                decision_reasons=list(staged_classified.decision_reasons),
                discrepancies=list(staged_classified.discrepancies),
            ),
        )

    final_directory = build_export_attachment_directory(document_root, family)
    final_directory.mkdir(parents=True, exist_ok=True)
    final_saved_documents = _move_staged_documents_to_final_directory(
        staged_documents=staged_result.saved_documents,
        final_directory=final_directory,
    )
    _cleanup_directory(staging_directory)
    final_classified = classify_saved_ud_ip_exp_documents(
        saved_documents=final_saved_documents,
        analysis_provider=analysis_provider,
    )
    decision_reasons = list(staged_result.decision_reasons) + list(final_classified.decision_reasons)
    return UDIPEXPLiveDocumentPreparationResult(
        document_save_result=DocumentSaveResult(
            saved_documents=final_classified.saved_documents,
            issues=[],
            decision_reasons=decision_reasons,
            destination_directory=str(final_directory),
        ),
        classified_documents=final_classified,
    )


def _save_mail_pdfs_to_directory(
    *,
    mail: EmailMessage,
    destination_directory: Path,
    provider: AttachmentContentProvider,
) -> DocumentSaveResult:
    saved_documents: list[SavedDocument] = []
    decision_reasons: list[str] = []
    seen_normalized_filenames: set[str] = set()
    pdf_attachments = [
        attachment
        for attachment in sorted(mail.attachments, key=lambda item: item.attachment_index)
        if attachment.normalized_filename.lower().endswith(".pdf")
    ]

    for attachment in pdf_attachments:
        destination_path = destination_directory / attachment.normalized_filename
        duplicate_in_mail = attachment.normalized_filename in seen_normalized_filenames
        existed_before = destination_path.exists()
        try:
            if duplicate_in_mail or existed_before:
                if not destination_path.exists():
                    raise ValueError(f"Expected duplicate file was not present: {destination_path}")
                file_sha256 = sha256_file(destination_path)
                save_decision = "skipped_duplicate_filename"
                decision_reasons.append(
                    f"Skipped duplicate attachment filename {attachment.normalized_filename}."
                )
            else:
                _save_attachment_atomically(
                    provider=provider,
                    mail=mail,
                    attachment_index=attachment.attachment_index,
                    destination_path=destination_path,
                )
                file_sha256 = sha256_file(destination_path)
                save_decision = "saved_new"
                decision_reasons.append(f"Saved new attachment {attachment.normalized_filename}.")
        except Exception as exc:
            return DocumentSaveResult(
                saved_documents=saved_documents,
                issues=[
                    DocumentSaveIssue(
                        code="document_save_runtime_error",
                        severity=FinalDecision.HARD_BLOCK,
                        message="Attachment saving failed before validation completed.",
                        details={
                            "mail_id": mail.mail_id,
                            "attachment_index": attachment.attachment_index,
                            "attachment_name": attachment.attachment_name,
                            "destination_path": str(destination_path),
                            "error": str(exc),
                        },
                    )
                ],
                decision_reasons=decision_reasons + ["Attachment saving failed; validation is hard-blocked."],
                destination_directory=str(destination_directory),
            )
        saved_documents.append(
            SavedDocument(
                saved_document_id=build_saved_document_id(
                    mail.mail_id,
                    attachment.normalized_filename,
                    str(destination_path),
                ),
                mail_id=mail.mail_id,
                attachment_name=attachment.attachment_name,
                normalized_filename=attachment.normalized_filename,
                destination_path=str(destination_path),
                file_sha256=file_sha256,
                save_decision=save_decision,
                attachment_index=attachment.attachment_index,
            )
        )
        seen_normalized_filenames.add(attachment.normalized_filename)

    if not decision_reasons:
        decision_reasons.append("No PDF attachments were available for saving.")

    return DocumentSaveResult(
        saved_documents=saved_documents,
        issues=[],
        decision_reasons=decision_reasons,
        destination_directory=str(destination_directory),
    )


def _resolve_family_from_documents(
    *,
    documents,
    workbook_snapshot: WorkbookSnapshot | None,
) -> ERPFamily | DocumentSaveIssue:
    normalized_lc_sc_numbers = {
        normalize_lc_sc_number(document.lc_sc_number.value)
        for document in documents
        if document.lc_sc_number.value.strip()
    }
    normalized_lc_sc_numbers.discard(None)
    if not normalized_lc_sc_numbers:
        return DocumentSaveIssue(
            code="document_storage_path_unresolved",
            severity=FinalDecision.HARD_BLOCK,
            message="UD/IP/EXP attachment storage path could not be resolved without an extracted LC/SC number.",
            details={"lc_sc_numbers": []},
        )
    if len(normalized_lc_sc_numbers) != 1:
        return DocumentSaveIssue(
            code="document_storage_path_unresolved",
            severity=FinalDecision.HARD_BLOCK,
            message="UD/IP/EXP attachment storage path requires one resolved LC/SC family per mail.",
            details={"lc_sc_numbers": sorted(str(value) for value in normalized_lc_sc_numbers)},
        )
    if workbook_snapshot is None:
        return DocumentSaveIssue(
            code="document_storage_path_unresolved",
            severity=FinalDecision.HARD_BLOCK,
            message="UD/IP/EXP attachment storage path requires a workbook snapshot for family resolution.",
            details={"lc_sc_numbers": sorted(str(value) for value in normalized_lc_sc_numbers)},
        )

    mapping = resolve_ud_ip_exp_storage_header_mapping(workbook_snapshot)
    if mapping is None:
        return DocumentSaveIssue(
            code="document_storage_path_unresolved",
            severity=FinalDecision.HARD_BLOCK,
            message="UD/IP/EXP attachment storage path could not resolve workbook family headers deterministically.",
            details={"sheet_name": workbook_snapshot.sheet_name},
        )

    expected_lc_sc_number = next(iter(normalized_lc_sc_numbers))
    matching_rows = [
        row
        for row in sorted(workbook_snapshot.rows, key=lambda item: item.row_index)
        if normalize_lc_sc_number(row.values.get(mapping["lc_sc_no"], "")) == expected_lc_sc_number
    ]
    if not matching_rows:
        return DocumentSaveIssue(
            code="document_storage_path_unresolved",
            severity=FinalDecision.HARD_BLOCK,
            message="UD/IP/EXP attachment storage path could not find workbook rows for the extracted LC/SC family.",
            details={"lc_sc_number": expected_lc_sc_number},
        )

    buyer_name = _canonical_workbook_buyer_name(matching_rows[0], mapping)
    lc_issue_date = _canonical_workbook_issue_date(matching_rows[0], mapping)
    if buyer_name is None or lc_issue_date is None:
        return DocumentSaveIssue(
            code="document_storage_path_unresolved",
            severity=FinalDecision.HARD_BLOCK,
            message="UD/IP/EXP attachment storage path requires workbook buyer and LC issue date values.",
            details={"lc_sc_number": expected_lc_sc_number},
        )

    for row in matching_rows[1:]:
        if _canonical_workbook_buyer_name(row, mapping) != buyer_name or _canonical_workbook_issue_date(row, mapping) != lc_issue_date:
            return DocumentSaveIssue(
                code="document_storage_path_unresolved",
                severity=FinalDecision.HARD_BLOCK,
                message="UD/IP/EXP attachment storage path found inconsistent workbook family metadata.",
                details={
                    "lc_sc_number": expected_lc_sc_number,
                    "row_indexes": [match.row_index for match in matching_rows],
                },
            )

    return ERPFamily(
        lc_sc_number=matching_rows[0].values.get(mapping["lc_sc_no"], expected_lc_sc_number),
        buyer_name=buyer_name,
        lc_sc_date=lc_issue_date,
        folder_buyer_name=buyer_name,
    )


def _canonical_workbook_buyer_name(row: WorkbookRow, mapping: dict[str, int]) -> str | None:
    return normalize_buyer_name_for_paths(row.values.get(mapping["buyer_name"], ""))


def _canonical_workbook_issue_date(row: WorkbookRow, mapping: dict[str, int]) -> str | None:
    return normalize_lc_sc_date(row.values.get(mapping["lc_issue_date"], ""))


def _move_staged_documents_to_final_directory(
    *,
    staged_documents: list[SavedDocument],
    final_directory: Path,
) -> list[SavedDocument]:
    moved_documents: list[SavedDocument] = []
    seen_filenames: set[str] = set()
    for staged_document in sorted(
        staged_documents,
        key=lambda item: item.attachment_index if item.attachment_index is not None else 10**6,
    ):
        staging_path = Path(staged_document.destination_path)
        final_path = final_directory / staged_document.normalized_filename
        duplicate_in_mail = staged_document.normalized_filename in seen_filenames
        if duplicate_in_mail or final_path.exists():
            file_sha256 = sha256_file(final_path)
            save_decision = "skipped_duplicate_filename"
            staging_path.unlink(missing_ok=True)
        else:
            os.replace(staging_path, final_path)
            file_sha256 = sha256_file(final_path)
            save_decision = "saved_new"
        moved_documents.append(
            SavedDocument(
                saved_document_id=build_saved_document_id(
                    staged_document.mail_id,
                    staged_document.normalized_filename,
                    str(final_path),
                ),
                mail_id=staged_document.mail_id,
                attachment_name=staged_document.attachment_name,
                normalized_filename=staged_document.normalized_filename,
                destination_path=str(final_path),
                file_sha256=file_sha256,
                save_decision=save_decision,
                attachment_index=staged_document.attachment_index,
            )
        )
        seen_filenames.add(staged_document.normalized_filename)
    return moved_documents


def _save_attachment_atomically(
    *,
    provider: AttachmentContentProvider,
    mail: EmailMessage,
    attachment_index: int,
    destination_path: Path,
) -> None:
    temp_path = destination_path.parent / f"{destination_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        provider.save_attachment(mail=mail, attachment_index=attachment_index, destination_path=temp_path)
        os.replace(temp_path, destination_path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _cleanup_directory(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            child.rmdir()
    path.rmdir()
