from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from project.erp import ERPFamily
from project.models import EmailMessage, FinalDecision, SavedDocument
from project.storage.planning import plan_attachment_saves
from project.storage.providers import AttachmentContentProvider
from project.utils.hashing import sha256_file
from project.utils.ids import build_saved_document_id


@dataclass(slots=True, frozen=True)
class DocumentSaveIssue:
    code: str
    severity: FinalDecision
    message: str
    details: dict[str, object]


@dataclass(slots=True, frozen=True)
class DocumentSaveResult:
    saved_documents: list[SavedDocument]
    issues: list[DocumentSaveIssue]
    decision_reasons: list[str]
    destination_directory: str | None = None


def save_export_mail_documents(
    *,
    mail: EmailMessage,
    verified_family: ERPFamily | None,
    document_root: Path,
    provider: AttachmentContentProvider,
) -> DocumentSaveResult:
    if verified_family is None:
        return DocumentSaveResult(
            saved_documents=[],
            issues=[
                DocumentSaveIssue(
                    code="document_storage_path_unresolved",
                    severity=FinalDecision.HARD_BLOCK,
                    message="Export attachment storage path could not be resolved without a verified ERP family.",
                    details={"mail_id": mail.mail_id},
                )
            ],
            decision_reasons=["Attachment saving blocked because the export family was not verified."],
        )

    try:
        destination_directory = build_export_attachment_directory(document_root, verified_family)
    except ValueError as exc:
        return DocumentSaveResult(
            saved_documents=[],
            issues=[
                DocumentSaveIssue(
                    code="document_storage_path_unresolved",
                    severity=FinalDecision.HARD_BLOCK,
                    message="Export attachment storage path could not be resolved deterministically.",
                    details={"mail_id": mail.mail_id, "error": str(exc)},
                )
            ],
            decision_reasons=["Attachment saving blocked because the export storage path was invalid."],
        )

    destination_directory.mkdir(parents=True, exist_ok=True)
    existing_filenames = {
        path.name
        for path in destination_directory.iterdir()
        if path.is_file()
    }
    save_plans = [
        plan
        for plan in plan_attachment_saves(
            mail=mail,
            destination_directory=destination_directory,
            existing_filenames=existing_filenames,
        )
        if plan.normalized_filename.lower().endswith(".pdf")
    ]

    saved_documents: list[SavedDocument] = []
    decision_reasons: list[str] = []
    for plan in save_plans:
        destination_path = Path(plan.destination_path)
        try:
            if plan.save_decision == "planned_skip_duplicate_filename":
                if not destination_path.exists():
                    raise ValueError(f"Expected duplicate file was not present: {destination_path}")
                file_sha256 = sha256_file(destination_path)
                save_decision = "skipped_duplicate_filename"
                decision_reasons.append(
                    f"Skipped duplicate attachment filename {plan.normalized_filename}."
                )
            else:
                _save_attachment_atomically(
                    provider=provider,
                    mail=mail,
                    attachment_index=plan.attachment_index,
                    destination_path=destination_path,
                )
                file_sha256 = sha256_file(destination_path)
                save_decision = "saved_new"
                decision_reasons.append(
                    f"Saved new attachment {plan.normalized_filename}."
                )
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
                            "attachment_index": plan.attachment_index,
                            "attachment_name": plan.attachment_name,
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
                    plan.normalized_filename,
                    str(destination_path),
                ),
                mail_id=mail.mail_id,
                attachment_name=plan.attachment_name,
                normalized_filename=plan.normalized_filename,
                destination_path=str(destination_path),
                file_sha256=file_sha256,
                save_decision=save_decision,
            )
        )

    if not decision_reasons:
        decision_reasons.append("No PDF attachments were available for saving.")

    return DocumentSaveResult(
        saved_documents=saved_documents,
        issues=[],
        decision_reasons=decision_reasons,
        destination_directory=str(destination_directory),
    )


def build_export_attachment_directory(document_root: Path, family: ERPFamily) -> Path:
    try:
        year = datetime.fromisoformat(family.lc_sc_date).year
    except ValueError as exc:
        raise ValueError(f"Invalid LC/SC date for storage path: {family.lc_sc_date}") from exc
    return (
        document_root
        / _sanitize_path_segment(str(year))
        / _sanitize_path_segment(family.buyer_name)
        / _sanitize_path_segment(family.lc_sc_number)
        / "All Attachments"
    )


def _sanitize_path_segment(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Path segment cannot be empty")
    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if character in invalid else character for character in normalized).rstrip(" .")
    if not cleaned:
        raise ValueError("Path segment became empty after sanitization")
    return cleaned


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
