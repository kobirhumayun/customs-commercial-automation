from __future__ import annotations

from pathlib import Path
from typing import Any

from project.documents import extract_saved_document_raw_report
from project.models import EmailMessage, SavedDocument, WorkflowId
from project.storage import AttachmentContentProvider, write_json
from project.utils.hashing import sha256_file
from project.utils.ids import build_saved_document_id
from project.utils.json import to_jsonable
from project.utils.time import utc_now, utc_timestamp


def build_live_smoke_test_id(workflow_id: WorkflowId) -> str:
    timestamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    return f"smoke-{timestamp}-{workflow_id.value}"


def build_live_smoke_test_bundle_root(*, report_root: Path, workflow_id: WorkflowId, smoke_test_id: str) -> Path:
    return report_root / "live_smoke_tests" / workflow_id.value / smoke_test_id


def save_smoke_test_pdf_audits(
    *,
    snapshot: list[EmailMessage],
    bundle_root: Path,
    provider: AttachmentContentProvider,
    audit_mode: str,
    max_pdf_attachments: int,
) -> dict[str, Any]:
    if max_pdf_attachments <= 0:
        raise ValueError("Smoke-test PDF audit limit must be greater than zero.")

    saved_pdf_dir = bundle_root / "saved_pdfs"
    audit_dir = bundle_root / "document_audits"
    saved_pdf_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    saved_documents: list[SavedDocument] = []
    issues: list[dict[str, Any]] = []
    audited_document_count = 0
    candidate_count = 0

    for mail in snapshot:
        for attachment in mail.attachments:
            if candidate_count >= max_pdf_attachments:
                break
            if not attachment.normalized_filename.lower().endswith(".pdf"):
                continue
            candidate_count += 1
            mail_dir = saved_pdf_dir / f"{mail.snapshot_index:04d}_{mail.mail_id}"
            mail_dir.mkdir(parents=True, exist_ok=True)
            destination_path = mail_dir / f"{attachment.attachment_index:03d}_{attachment.normalized_filename}"
            try:
                provider.save_attachment(
                    mail=mail,
                    attachment_index=attachment.attachment_index,
                    destination_path=destination_path,
                )
                saved_document = SavedDocument(
                    saved_document_id=build_saved_document_id(
                        mail.mail_id,
                        attachment.normalized_filename,
                        str(destination_path),
                    ),
                    mail_id=mail.mail_id,
                    attachment_name=attachment.attachment_name,
                    normalized_filename=attachment.normalized_filename,
                    destination_path=str(destination_path),
                    file_sha256=sha256_file(destination_path),
                    save_decision="saved_new",
                    attachment_index=attachment.attachment_index,
                )
                saved_documents.append(saved_document)
                audit_report = extract_saved_document_raw_report(
                    saved_document=saved_document,
                    mode=audit_mode,
                )
                write_json(
                    audit_dir / f"{saved_document.saved_document_id}.{audit_mode}.json",
                    audit_report,
                )
                audited_document_count += 1
            except Exception as exc:
                issues.append(
                    {
                        "mail_id": mail.mail_id,
                        "entry_id": mail.entry_id,
                        "attachment_index": attachment.attachment_index,
                        "attachment_name": attachment.attachment_name,
                        "normalized_filename": attachment.normalized_filename,
                        "error": str(exc),
                    }
                )
        if candidate_count >= max_pdf_attachments:
            break

    return {
        "status": "ready" if not issues else "issue",
        "audit_mode": audit_mode,
        "max_pdf_attachments": max_pdf_attachments,
        "candidate_pdf_count": candidate_count,
        "saved_pdf_count": len(saved_documents),
        "audited_pdf_count": audited_document_count,
        "issue_count": len(issues),
        "saved_pdf_directory": str(saved_pdf_dir),
        "document_audit_directory": str(audit_dir),
        "saved_documents": to_jsonable(saved_documents),
        "issues": issues,
    }


def build_live_smoke_test_report(
    *,
    workflow_id: WorkflowId,
    smoke_test_id: str,
    bundle_root: Path,
    readiness_report: dict[str, Any],
    attachment_audit_section: dict[str, Any] | None,
) -> dict[str, Any]:
    attachment_section = attachment_audit_section or {"status": "not_requested"}
    issue_count = int(readiness_report.get("issue_section_count", 0))
    if attachment_section.get("status") == "issue":
        issue_count += 1

    return {
        "generated_at_utc": utc_timestamp(),
        "workflow_id": workflow_id.value,
        "smoke_test_id": smoke_test_id,
        "bundle_root": str(bundle_root),
        "overall_status": "ready" if issue_count == 0 else "attention_required",
        "readiness_report": readiness_report,
        "attachment_audit": attachment_section,
        "summary_counts": {
            "readiness_issue_count": int(readiness_report.get("issue_section_count", 0)),
            "attachment_issue_count": int(attachment_section.get("issue_count", 0))
            if isinstance(attachment_section, dict)
            else 0,
            "saved_pdf_count": int(attachment_section.get("saved_pdf_count", 0))
            if isinstance(attachment_section, dict)
            else 0,
            "audited_pdf_count": int(attachment_section.get("audited_pdf_count", 0))
            if isinstance(attachment_section, dict)
            else 0,
        },
    }
