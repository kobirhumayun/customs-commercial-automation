from __future__ import annotations

import re
from dataclasses import dataclass


FILE_NUMBER_CANDIDATE_PATTERN = re.compile(r"(?i)\bP(?:[\\/ -]?\d{2}){1}(?:[\\/ -]?\d{1,4})\b")
SUBJECT_PREFIX_PATTERN = re.compile(r"^(LC|SC)\s*-\s*(.+)$", re.IGNORECASE)


@dataclass(slots=True, frozen=True)
class ParsedExportSubject:
    prefix: str
    lc_sc_number: str
    lc_sc_number_end_sequence: str
    buyer_name: str
    suffix_tokens: list[str]


def normalize_file_number(raw_value: str) -> str | None:
    normalized = raw_value.strip().upper()
    normalized = normalized.replace("\\", "/").replace("-", "/")
    normalized = "".join(character for character in normalized if ord(character) >= 32)
    segments = [segment for segment in normalized.split("/") if segment]
    if len(segments) != 3:
        return None
    prefix, year, sequence = segments
    if prefix != "P" or not year.isdigit() or len(year) != 2:
        return None
    if not sequence.isdigit() or not 1 <= len(sequence) <= 4:
        return None
    return f"P/{year}/{int(sequence):04d}"


def extract_file_numbers(body_text: str) -> list[str]:
    extracted: list[str] = []
    seen: set[str] = set()
    for match in FILE_NUMBER_CANDIDATE_PATTERN.finditer(body_text):
        canonical = normalize_file_number(match.group(0))
        if canonical is None or canonical in seen:
            continue
        seen.add(canonical)
        extracted.append(canonical)
    return extracted


def parse_export_subject(subject_raw: str) -> ParsedExportSubject | None:
    cleaned_subject = subject_raw.strip()
    match = SUBJECT_PREFIX_PATTERN.match(cleaned_subject)
    if match is None:
        return None

    prefix = match.group(1).upper()
    remainder = match.group(2).strip()
    main_part, suffix_tokens = _split_suffix_tokens(remainder)
    tokens = [token.strip() for token in main_part.split("-") if token.strip()]
    if len(tokens) < 2:
        return None

    buyer_start_index = next((index for index, token in enumerate(tokens) if " " in token), None)
    if buyer_start_index is None or buyer_start_index == 0:
        return None

    number_body = "-".join(tokens[:buyer_start_index]).strip()
    buyer_name = "-".join(tokens[buyer_start_index:]).strip()
    if not number_body or not buyer_name:
        return None

    return ParsedExportSubject(
        prefix=prefix,
        lc_sc_number=f"{prefix}-{number_body}",
        lc_sc_number_end_sequence=tokens[buyer_start_index - 1].strip(),
        buyer_name=buyer_name,
        suffix_tokens=suffix_tokens,
    )


def _split_suffix_tokens(remainder: str) -> tuple[str, list[str]]:
    if "_" not in remainder:
        return remainder, []
    main_part, *suffix_parts = remainder.split("_")
    suffix_tokens = [part.strip().upper() for part in suffix_parts if part.strip()]
    return main_part.strip(), suffix_tokens
