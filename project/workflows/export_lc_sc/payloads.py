from __future__ import annotations

from dataclasses import dataclass

from project.models import EmailMessage
from project.workflows.export_lc_sc.parsing import ParsedExportSubject, extract_file_numbers, parse_export_subject


@dataclass(slots=True, frozen=True)
class ExportMailPayload:
    parsed_subject: ParsedExportSubject | None
    file_numbers: list[str]


def build_export_mail_payload(mail: EmailMessage) -> ExportMailPayload:
    return ExportMailPayload(
        parsed_subject=parse_export_subject(mail.subject_raw),
        file_numbers=extract_file_numbers(mail.body_text),
    )
