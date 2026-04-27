from __future__ import annotations

import re
from datetime import date, datetime


WHITESPACE_PATTERN = re.compile(r"\s+")
NON_PRINTABLE_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
DASH_PATTERN = re.compile(r"[\u2010-\u2015]")
REPEATED_DASH_PATTERN = re.compile(r"-+")
PATH_PUNCTUATION_SPACE_PATTERN = re.compile(r"[;:]+")


def normalize_buyer_name(raw_value: str) -> str | None:
    normalized = _shared_string_normalize(raw_value)
    normalized = re.sub(r"\s*\\\s*", r"\\", normalized)
    normalized = WHITESPACE_PATTERN.sub(" ", normalized).strip()
    return normalized or None


def normalize_buyer_name_for_paths(raw_value: str) -> str | None:
    normalized = _shared_string_normalize(raw_value)
    if "\\" in normalized:
        normalized = normalized.split("\\", 1)[0].strip()
    normalized = normalized.rstrip(".")
    normalized = PATH_PUNCTUATION_SPACE_PATTERN.sub(" ", normalized)
    normalized = WHITESPACE_PATTERN.sub(" ", normalized).strip()
    return normalized or None


def normalize_lc_sc_number(raw_value: str) -> str | None:
    normalized = _shared_string_normalize(raw_value)
    match = re.match(r"^(LC|SC)\s*[- ]*\s*(.+)$", normalized)
    if match is not None:
        prefix = match.group(1)
        body = match.group(2).strip(" -")
        body = REPEATED_DASH_PATTERN.sub("-", body.replace(" ", "-"))
        body = body.strip("-")
        if not body:
            return None
        return f"{prefix}-{body}"
    return normalized or None


def normalize_lc_sc_date(raw_value: object) -> str | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, datetime):
        return raw_value.date().isoformat()
    if isinstance(raw_value, date):
        return raw_value.isoformat()
    normalized = WHITESPACE_PATTERN.sub(" ", str(raw_value).strip())
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d-%b-%y", "%d-%b-%Y", "%d/%m/%y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(normalized, fmt).date().isoformat()
        except ValueError:
            continue
    return normalized


def _shared_string_normalize(raw_value: str) -> str:
    normalized = raw_value.strip().upper()
    normalized = DASH_PATTERN.sub("-", normalized)
    normalized = NON_PRINTABLE_PATTERN.sub("", normalized)
    normalized = WHITESPACE_PATTERN.sub(" ", normalized)
    return normalized
