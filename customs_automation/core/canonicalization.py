from __future__ import annotations

import re
import unicodedata

CONTROL_OR_ZERO_WIDTH_PATTERN = re.compile(r"[\u200B-\u200D\uFEFF\x00-\x1F\x7F]")
WHITESPACE_PATTERN = re.compile(r"\s+")
MULTI_DASH_PATTERN = re.compile(r"-+")
TRAILING_PUNCT_PATTERN = re.compile(r"[.,;:]+$")

DASH_TRANSLATION = str.maketrans({"–": "-", "—": "-", "‑": "-"})


class CanonicalizationError(ValueError):
    """Raised when an identifier cannot be canonicalized per deterministic profile rules."""


def _apply_shared_primitives(
    value: str,
    *,
    collapse_whitespace: bool,
    uppercase: bool,
    normalize_slashes: bool,
) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.strip()
    normalized = normalized.translate(DASH_TRANSLATION)
    normalized = CONTROL_OR_ZERO_WIDTH_PATTERN.sub("", normalized)

    if normalize_slashes:
        normalized = normalized.replace("\\", "/")

    if uppercase:
        normalized = normalized.upper()

    if collapse_whitespace:
        normalized = WHITESPACE_PATTERN.sub(" ", normalized)

    normalized = normalized.strip()
    if not normalized:
        raise CanonicalizationError("Canonicalization produced an empty result")
    return normalized


def canonicalize_file_number(raw_value: str) -> str:
    base = _apply_shared_primitives(
        raw_value,
        collapse_whitespace=False,
        uppercase=True,
        normalize_slashes=True,
    )
    base = base.replace("-", "/")
    parts = [segment for segment in base.split("/") if segment]
    if len(parts) != 3:
        raise CanonicalizationError("File number must contain exactly three segments")

    prefix, year, sequence = parts
    if prefix != "P":
        raise CanonicalizationError("File number prefix must be P")
    if not re.fullmatch(r"\d{2}", year):
        raise CanonicalizationError("File number year must be two digits")
    if not re.fullmatch(r"\d{1,4}", sequence):
        raise CanonicalizationError("File number sequence must be 1-4 digits")

    return f"P/{year}/{int(sequence):04d}"


def canonicalize_lc_sc_number(raw_value: str) -> str:
    base = _apply_shared_primitives(
        raw_value,
        collapse_whitespace=True,
        uppercase=True,
        normalize_slashes=False,
    )
    match = re.fullmatch(r"(LC|SC)\s*[-_ /]*\s*(.+)", base)
    if match is None:
        raise CanonicalizationError("LC/SC number must start with LC or SC")

    prefix, body = match.groups()
    body = re.sub(r"[^A-Z0-9-]+", "-", body)
    body = MULTI_DASH_PATTERN.sub("-", body).strip("-")
    if not body:
        raise CanonicalizationError("LC/SC body cannot be empty")

    return f"{prefix}-{body}"


def canonicalize_pi_number(raw_value: str) -> str:
    base = _apply_shared_primitives(
        raw_value,
        collapse_whitespace=False,
        uppercase=True,
        normalize_slashes=False,
    )
    normalized = re.sub(r"[\s_/]+", "-", base)
    normalized = MULTI_DASH_PATTERN.sub("-", normalized).strip("-")

    match = re.fullmatch(r"PDL-(\d{2})-(\d{1,4})(?:-R(\d+))?", normalized)
    if match is None:
        raise CanonicalizationError("PI number must match PDL-YY-NNNN[-Rdigits]")

    year, serial, revision = match.groups()
    output = f"PDL-{year}-{int(serial):04d}"
    if revision is not None:
        output += f"-R{int(revision)}"
    return output


def canonicalize_ud_ip_exp_number(raw_value: str) -> str:
    base = _apply_shared_primitives(
        raw_value,
        collapse_whitespace=True,
        uppercase=True,
        normalize_slashes=False,
    )
    match = re.fullmatch(r"(UD|IP|EXP)\s*[-_ ]*\s*(.+)", base)
    if match is None:
        raise CanonicalizationError("Document number must start with UD, IP, or EXP")

    prefix, body = match.groups()
    body = TRAILING_PUNCT_PATTERN.sub("", body)
    body = re.sub(r"[\s_/]+", "-", body)
    body = MULTI_DASH_PATTERN.sub("-", body).strip("-")
    if not body:
        raise CanonicalizationError("Document number body cannot be empty")

    return f"{prefix}-{body}"


def canonicalize_buyer_name(raw_value: str) -> str:
    base = _apply_shared_primitives(
        raw_value,
        collapse_whitespace=True,
        uppercase=True,
        normalize_slashes=False,
    )
    buyer = base.split("\\", maxsplit=1)[0].strip()
    buyer = buyer.rstrip(".")
    buyer = re.sub(r"[-_,;:]+", " ", buyer)
    buyer = WHITESPACE_PATTERN.sub(" ", buyer).strip()
    if not buyer:
        raise CanonicalizationError("Buyer name cannot be empty")
    return buyer
