from __future__ import annotations

import re


WHITESPACE_PATTERN = re.compile(r"\s+")
NON_PRINTABLE_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
DASH_PATTERN = re.compile(r"[\u2010-\u2015]")
REPEATED_DASH_PATTERN = re.compile(r"-+")
PUNCTUATION_SPACE_PATTERN = re.compile(r"[.,;:]+")


def normalize_buyer_name(raw_value: str) -> str | None:
    normalized = _shared_string_normalize(raw_value)
    if "\\" in normalized:
        normalized = normalized.split("\\", 1)[0].strip()
    normalized = normalized.rstrip(".")
    normalized = PUNCTUATION_SPACE_PATTERN.sub(" ", normalized)
    normalized = WHITESPACE_PATTERN.sub(" ", normalized).strip()
    return normalized or None


def normalize_lc_sc_number(raw_value: str) -> str | None:
    normalized = _shared_string_normalize(raw_value)
    match = re.match(r"^(LC|SC)\s*[- ]*\s*(.+)$", normalized)
    if match is None:
        return None
    prefix = match.group(1)
    body = match.group(2).strip(" -")
    body = REPEATED_DASH_PATTERN.sub("-", body.replace(" ", "-"))
    body = body.strip("-")
    if not body:
        return None
    return f"{prefix}-{body}"


def normalize_lc_sc_date(raw_value: str) -> str | None:
    normalized = WHITESPACE_PATTERN.sub(" ", raw_value.strip())
    return normalized or None


def _shared_string_normalize(raw_value: str) -> str:
    normalized = raw_value.strip().upper()
    normalized = DASH_PATTERN.sub("-", normalized)
    normalized = NON_PRINTABLE_PATTERN.sub("", normalized)
    normalized = WHITESPACE_PATTERN.sub(" ", normalized)
    return normalized
