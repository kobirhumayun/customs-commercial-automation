from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, replace
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
from project.workflows.ud_ip_exp.structured_extraction import (
    StructuredUDExtractionContext,
    StructuredUDSavedDocumentAnalysisProvider,
)

_FILENAME_LC_SC_SUFFIX_PATTERN = re.compile(
    r"(?:^|[^A-Z0-9])UD[-_\s]*(?:LC|SC)[-_\s]*(?P<suffix>[A-Z0-9]+)",
    re.IGNORECASE,
)


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
    verified_family: ERPFamily | None = None,
    export_payload=None,
    require_verified_family: bool = False,
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

    analysis_provider = _structured_analysis_provider(
        analysis_provider=analysis_provider,
        export_payload=export_payload,
    )
    staged_classified = classify_saved_ud_ip_exp_documents(
        saved_documents=staged_result.saved_documents,
        analysis_provider=analysis_provider,
    )
    resolution_documents = list(documents_override or staged_classified.documents)
    document_evidence = _build_document_resolution_evidence(
        saved_documents=staged_classified.saved_documents,
        documents=resolution_documents,
    )
    if verified_family is not None:
        family = _validate_documents_against_verified_family(
            verified_family=verified_family,
            document_evidence=document_evidence,
        )
    elif require_verified_family:
        family = DocumentSaveIssue(
            code="document_storage_path_unresolved",
            severity=FinalDecision.HARD_BLOCK,
            message=(
                "UD/IP/EXP attachment storage path requires email body file numbers "
                "that resolve to one ERP LC/SC family."
            ),
            details={"document_evidence": document_evidence},
        )
    else:
        family = _resolve_family_from_documents(
            documents=resolution_documents,
            workbook_snapshot=workbook_snapshot,
            document_evidence=document_evidence,
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
    final_classified = _rebase_classified_documents(
        staged_classified=staged_classified,
        final_saved_documents=final_saved_documents,
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


def _validate_documents_against_verified_family(
    *,
    verified_family: ERPFamily,
    document_evidence: list[dict[str, object]],
) -> ERPFamily | DocumentSaveIssue:
    expected_lc_sc_number = _comparison_lc_sc_number(verified_family.lc_sc_number)
    conflicting_evidence = [
        evidence
        for evidence in document_evidence
        if _evidence_lc_sc_contradicts_family(
            evidence=evidence,
            expected_lc_sc_number=expected_lc_sc_number,
        )
    ]
    if conflicting_evidence:
        return DocumentSaveIssue(
            code="document_storage_path_unresolved",
            severity=FinalDecision.HARD_BLOCK,
            message="UD/IP/EXP PDF evidence contradicts the ERP-derived LC/SC family for the mail.",
            details={
                "expected_lc_sc_number": verified_family.lc_sc_number,
                "conflicting_document_evidence": conflicting_evidence,
                "document_evidence": document_evidence,
            },
        )
    filename_suffix_mismatches = _filename_suffix_mismatches_verified_family(
        expected_lc_sc_number=verified_family.lc_sc_number,
        document_evidence=document_evidence,
    )
    if filename_suffix_mismatches:
        return DocumentSaveIssue(
            code="ud_filename_lc_suffix_mismatch",
            severity=FinalDecision.HARD_BLOCK,
            message=(
                "UD/IP/EXP attachment filename LC/SC suffix contradicts the ERP-derived "
                "LC/SC family for the mail."
            ),
            details={
                "expected_lc_sc_number": verified_family.lc_sc_number,
                "mismatched_filename_suffixes": filename_suffix_mismatches,
                "document_evidence": document_evidence,
            },
        )
    return verified_family


def _structured_analysis_provider(
    *,
    analysis_provider: SavedDocumentAnalysisProvider,
    export_payload,
) -> SavedDocumentAnalysisProvider:
    if export_payload is None:
        return analysis_provider
    canonical_row = next(
        (match.canonical_row for match in export_payload.erp_matches if match.canonical_row is not None),
        None,
    )
    if canonical_row is None:
        return analysis_provider
    return StructuredUDSavedDocumentAnalysisProvider(
        base_provider=analysis_provider,
        context=StructuredUDExtractionContext(
            erp_lc_sc_number=canonical_row.lc_sc_number,
            erp_ship_remarks=canonical_row.ship_remarks,
        ),
    )


def _evidence_lc_sc_contradicts_family(
    *,
    evidence: dict[str, object],
    expected_lc_sc_number: str,
) -> bool:
    raw_lc_sc_number = str(evidence.get("lc_sc_number") or "").strip()
    if not raw_lc_sc_number:
        return False
    return _comparison_lc_sc_number(raw_lc_sc_number) != expected_lc_sc_number


def _comparison_lc_sc_number(value: str) -> str:
    return normalize_lc_sc_number(value) or value.strip().upper()


def _filename_suffix_mismatches_verified_family(
    *,
    expected_lc_sc_number: str,
    document_evidence: list[dict[str, object]],
) -> list[dict[str, str]]:
    expected_suffix_target = _alnum_upper(expected_lc_sc_number)
    if not expected_suffix_target:
        return []
    mismatches: list[dict[str, str]] = []
    for evidence in document_evidence:
        normalized_filename = str(evidence.get("normalized_filename") or "").strip()
        filename_suffix = _extract_filename_lc_sc_suffix(normalized_filename)
        if filename_suffix is None:
            continue
        suffix_target = _alnum_upper(filename_suffix)
        if suffix_target and not expected_suffix_target.endswith(suffix_target):
            mismatches.append(
                {
                    "attachment_name": str(evidence.get("attachment_name") or ""),
                    "normalized_filename": normalized_filename,
                    "filename_suffix": filename_suffix,
                    "expected_lc_sc_number": expected_lc_sc_number,
                }
            )
    return mismatches


def _extract_filename_lc_sc_suffix(normalized_filename: str) -> str | None:
    match = _FILENAME_LC_SC_SUFFIX_PATTERN.search(normalized_filename)
    if match is None:
        return None
    suffix = match.group("suffix").strip()
    return suffix or None


def _alnum_upper(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


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
    document_evidence: list[dict[str, object]],
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
            details={"lc_sc_numbers": [], "document_evidence": document_evidence},
        )
    if len(normalized_lc_sc_numbers) != 1:
        return DocumentSaveIssue(
            code="document_storage_path_unresolved",
            severity=FinalDecision.HARD_BLOCK,
            message="UD/IP/EXP attachment storage path requires one resolved LC/SC family per mail.",
            details={
                "lc_sc_numbers": sorted(str(value) for value in normalized_lc_sc_numbers),
                "document_evidence": document_evidence,
            },
        )
    if workbook_snapshot is None:
        return DocumentSaveIssue(
            code="document_storage_path_unresolved",
            severity=FinalDecision.HARD_BLOCK,
            message="UD/IP/EXP attachment storage path requires a workbook snapshot for family resolution.",
            details={
                "lc_sc_numbers": sorted(str(value) for value in normalized_lc_sc_numbers),
                "document_evidence": document_evidence,
            },
        )

    mapping = resolve_ud_ip_exp_storage_header_mapping(workbook_snapshot)
    if mapping is None:
        return DocumentSaveIssue(
            code="document_storage_path_unresolved",
            severity=FinalDecision.HARD_BLOCK,
            message="UD/IP/EXP attachment storage path could not resolve workbook family headers deterministically.",
            details={"sheet_name": workbook_snapshot.sheet_name, "document_evidence": document_evidence},
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
            details={"lc_sc_number": expected_lc_sc_number, "document_evidence": document_evidence},
        )

    buyer_name = _canonical_workbook_buyer_name(matching_rows[0], mapping)
    lc_issue_date = _canonical_workbook_issue_date(matching_rows[0], mapping)
    if buyer_name is None or lc_issue_date is None:
        return DocumentSaveIssue(
            code="document_storage_path_unresolved",
            severity=FinalDecision.HARD_BLOCK,
            message="UD/IP/EXP attachment storage path requires workbook buyer and LC issue date values.",
            details={"lc_sc_number": expected_lc_sc_number, "document_evidence": document_evidence},
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
                    "document_evidence": document_evidence,
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


def _build_document_resolution_evidence(
    *,
    saved_documents: list[SavedDocument],
    documents: list[UDIPEXPDocumentPayload],
) -> list[dict[str, object]]:
    documents_by_saved_document_id = {
        document.source_saved_document_id: document
        for document in documents
        if document.source_saved_document_id
    }
    evidence: list[dict[str, object]] = []
    for saved_document in sorted(
        saved_documents,
        key=lambda item: (
            item.attachment_index if item.attachment_index is not None else 10**6,
            item.normalized_filename,
        ),
    ):
        document = documents_by_saved_document_id.get(saved_document.saved_document_id)
        quantity_value = _document_evidence_quantity(saved_document=saved_document, document=document)
        evidence.append(
            {
                "attachment_name": saved_document.attachment_name,
                "normalized_filename": saved_document.normalized_filename,
                "saved_document_id": saved_document.saved_document_id,
                "document_kind": document.document_kind.value if document is not None else None,
                "document_number": (
                    document.document_number.value
                    if document is not None
                    else (saved_document.extracted_document_number or "")
                ),
                "lc_sc_number": (
                    document.lc_sc_number.value
                    if document is not None
                    else (normalize_lc_sc_number(saved_document.extracted_lc_sc_number or "") or "")
                ),
                "document_date": (
                    document.document_date.value
                    if document is not None and document.document_date is not None
                    else (saved_document.extracted_document_date or "")
                ),
                "quantity": quantity_value,
            }
        )
    return evidence


def _document_evidence_quantity(
    *,
    saved_document: SavedDocument,
    document: UDIPEXPDocumentPayload | None,
) -> str:
    if document is not None and document.quantity is not None:
        return f"{_format_decimal_string(document.quantity.amount)} {document.quantity.unit}"
    if saved_document.extracted_quantity and saved_document.extracted_quantity_unit:
        return f"{saved_document.extracted_quantity} {saved_document.extracted_quantity_unit}"
    return ""


def _format_decimal_string(value) -> str:
    normalized = format(value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


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
            file_sha256 = staged_document.file_sha256
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


def _rebase_classified_documents(
    *,
    staged_classified: ClassifiedUDIPEXPDocumentSet,
    final_saved_documents: list[SavedDocument],
) -> ClassifiedUDIPEXPDocumentSet:
    final_documents_by_key = {
        _saved_document_rebase_key(document): document for document in final_saved_documents
    }
    saved_document_id_map: dict[str, str] = {}
    rebased_saved_documents: list[SavedDocument] = []
    for staged_document in staged_classified.saved_documents:
        final_document = final_documents_by_key[_saved_document_rebase_key(staged_document)]
        saved_document_id_map[staged_document.saved_document_id] = final_document.saved_document_id
        rebased_saved_documents.append(
            replace(
                staged_document,
                saved_document_id=final_document.saved_document_id,
                destination_path=final_document.destination_path,
                file_sha256=final_document.file_sha256,
                save_decision=final_document.save_decision,
            )
        )

    rebased_documents = [
        replace(
            document,
            source_saved_document_id=saved_document_id_map.get(
                document.source_saved_document_id or "",
                document.source_saved_document_id,
            ),
        )
        for document in staged_classified.documents
    ]

    return ClassifiedUDIPEXPDocumentSet(
        saved_documents=rebased_saved_documents,
        documents=rebased_documents,
        decision_reasons=list(staged_classified.decision_reasons),
        discrepancies=list(staged_classified.discrepancies),
    )


def _saved_document_rebase_key(saved_document: SavedDocument) -> tuple[int | None, str, str]:
    return (
        saved_document.attachment_index,
        saved_document.normalized_filename,
        saved_document.attachment_name,
    )


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
