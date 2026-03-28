from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from project.models import EmailMessage
from project.utils.ids import build_saved_document_id


@dataclass(slots=True, frozen=True)
class AttachmentSavePlan:
    saved_document_id: str
    mail_id: str
    attachment_id: str
    attachment_index: int
    attachment_name: str
    normalized_filename: str
    destination_path: str
    save_decision: str


def plan_attachment_saves(
    *,
    mail: EmailMessage,
    destination_directory: Path,
    existing_filenames: set[str] | None = None,
) -> list[AttachmentSavePlan]:
    seen_normalized_filenames = set(existing_filenames or set())
    planned: list[AttachmentSavePlan] = []
    for attachment in sorted(mail.attachments, key=lambda item: item.attachment_index):
        destination_path = destination_directory / attachment.normalized_filename
        save_decision = (
            "planned_skip_duplicate_filename"
            if attachment.normalized_filename in seen_normalized_filenames
            else "planned_new"
        )
        planned.append(
            AttachmentSavePlan(
                saved_document_id=build_saved_document_id(
                    mail.mail_id,
                    attachment.normalized_filename,
                    str(destination_path),
                ),
                mail_id=mail.mail_id,
                attachment_id=attachment.attachment_id,
                attachment_index=attachment.attachment_index,
                attachment_name=attachment.attachment_name,
                normalized_filename=attachment.normalized_filename,
                destination_path=str(destination_path),
                save_decision=save_decision,
            )
        )
        seen_normalized_filenames.add(attachment.normalized_filename)
    return planned
