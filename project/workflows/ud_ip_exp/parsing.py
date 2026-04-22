from __future__ import annotations

import re
import unicodedata

from project.workflows.ud_ip_exp.payloads import UDIPEXPDocumentKind


_PREFIX_RE = re.compile(r"^(UD|IP|EXP)(?:[\s./\\_:;,\-]+|$)(.*)$")
_SEPARATOR_RE = re.compile(r"[\s/\\_\-]+")
_UNICODE_DASHES = {
    "\u2010",
    "\u2011",
    "\u2012",
    "\u2013",
    "\u2014",
    "\u2015",
    "\u2212",
}
_ZERO_WIDTH = {
    "\u200b",
    "\u200c",
    "\u200d",
    "\ufeff",
}


def normalize_ud_ip_exp_document_number(raw_value: str) -> str | None:
    normalized = _apply_shared_identifier_primitives(raw_value)
    bgmea_match = re.fullmatch(r"BGMEA/DHK/(UD|AM)/.+", normalized)
    if bgmea_match is not None:
        return normalized

    match = _PREFIX_RE.match(normalized)
    if match is None:
        return None

    prefix = match.group(1)
    body = match.group(2).strip().strip(".,;:")
    if not body:
        return None

    body_tokens = [token for token in _SEPARATOR_RE.split(body) if token]
    if not body_tokens:
        return None
    return f"{prefix}-{_canonical_body(body_tokens)}"


def document_kind_from_number(canonical_document_number: str) -> UDIPEXPDocumentKind | None:
    normalized = canonical_document_number.strip().upper()
    if "/UD/" in normalized or "/AM/" in normalized:
        return UDIPEXPDocumentKind.UD
    prefix = canonical_document_number.split("-", 1)[0].strip().upper()
    try:
        return UDIPEXPDocumentKind(prefix)
    except ValueError:
        return None


def _apply_shared_identifier_primitives(raw_value: str) -> str:
    cleaned = "".join(_clean_identifier_char(character) for character in str(raw_value))
    return re.sub(r"\s+", " ", cleaned).strip().upper()


def _clean_identifier_char(character: str) -> str:
    if character in _ZERO_WIDTH or unicodedata.category(character)[0] == "C":
        return ""
    if character in _UNICODE_DASHES:
        return "-"
    return character


def _canonical_body(tokens: list[str]) -> str:
    if len(tokens) >= 2 and tokens[0] in {"LC", "SC"}:
        remainder = " ".join(tokens[2:])
        if remainder:
            return f"{tokens[0]}-{tokens[1]}-{remainder}"
        return f"{tokens[0]}-{tokens[1]}"
    return "-".join(tokens)
