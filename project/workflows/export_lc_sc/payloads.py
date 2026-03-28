from __future__ import annotations

from dataclasses import dataclass

from project.erp import ERPFamily, ERPRegisterRow, ERPRowProvider
from project.models import EmailMessage
from project.workflows.export_lc_sc.parsing import ParsedExportSubject, extract_file_numbers, parse_export_subject


@dataclass(slots=True, frozen=True)
class ExportFileNumberMatch:
    file_number: str
    canonical_row: ERPRegisterRow | None
    matched_rows: list[ERPRegisterRow]


@dataclass(slots=True, frozen=True)
class ExportMailPayload:
    parsed_subject: ParsedExportSubject | None
    file_numbers: list[str]
    erp_matches: list[ExportFileNumberMatch]
    verified_family: ERPFamily | None


def build_export_mail_payload(
    mail: EmailMessage,
    *,
    erp_row_provider: ERPRowProvider | None = None,
) -> ExportMailPayload:
    file_numbers = extract_file_numbers(mail.body_text)
    erp_index = (
        erp_row_provider.lookup_rows(file_numbers=file_numbers)
        if erp_row_provider is not None and file_numbers
        else {file_number: [] for file_number in file_numbers}
    )
    erp_matches = [
        ExportFileNumberMatch(
            file_number=file_number,
            canonical_row=rows[0] if rows else None,
            matched_rows=list(rows),
        )
        for file_number, rows in ((file_number, erp_index.get(file_number, [])) for file_number in file_numbers)
    ]
    return ExportMailPayload(
        parsed_subject=parse_export_subject(mail.subject_raw),
        file_numbers=file_numbers,
        erp_matches=erp_matches,
        verified_family=_derive_verified_family(erp_matches),
    )


def _derive_verified_family(matches: list[ExportFileNumberMatch]) -> ERPFamily | None:
    canonical_rows = [match.canonical_row for match in matches if match.canonical_row is not None]
    if not canonical_rows:
        return None
    family = canonical_rows[0].family
    if all(row.family == family for row in canonical_rows[1:]):
        return family
    return None
