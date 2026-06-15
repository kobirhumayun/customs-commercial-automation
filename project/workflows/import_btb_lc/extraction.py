from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import Callable, Protocol

from project.storage.artifacts import atomic_write_text
from project.utils.hashing import sha256_file
from project.utils.json import pretty_json_dumps


IMPORT_BTB_LC_EXTRACTION_SCHEMA_ID = "import_btb_lc_extraction"
IMPORT_BTB_LC_EXTRACTION_SCHEMA_VERSION = "1.1.0"
IMPORT_BTB_LC_REPORT_SCHEMA_VERSION = "1.1.0"
IMPORT_BTB_LC_PAGE_LIMIT = 3

_OCR_THRESHOLDS = {
    "btb_lc_number": 0.98,
    "btb_lc_date": 0.96,
    "btb_lc_value": 0.96,
    "currency": 0.96,
    "seller_pi_numbers": 0.95,
    "related_export_lc_number": 0.98,
}

_BANK_PATTERNS = (
    ("the_city_bank_plc", "The City Bank PLC", re.compile(r"^0742\d{9}$")),
    ("mutual_trust_bank_limited", "Mutual Trust Bank Limited", re.compile(r"^0002228\d{9}$")),
    ("al_arafah_islami_bank_plc", "Al-Arafah Islami Bank PLC", re.compile(r"^1080\d{9}$")),
    ("brac_bank_plc", "Brac Bank PLC", re.compile(r"^3085\d{9}$")),
    ("standard_chartered_bank", "Standard Chartered Bank", re.compile(r"^41101\d{7}-L$")),
)

_BANK_MARKERS = {
    "the_city_bank_plc": (r"\bCIBLBDDH\w*\b", r"\bCITY BANK PLC\b"),
    "mutual_trust_bank_limited": (r"\bMTBLBDDH\w*\b", r"\bMUTUAL TRUST BANK (?:LIMITED|PLC)\b"),
    "al_arafah_islami_bank_plc": (r"\bALARBDDH\w*\b", r"\bAL-?ARAFAH ISLAMI BANK PLC\b"),
    "brac_bank_plc": (r"\bBRAKBDDH\w*\b", r"\bBRAC BANK PLC\b"),
    "standard_chartered_bank": (r"\bSCBLBDD\w*\b", r"\bSTANDARD CHARTERED BANK\b"),
}

_FIELD_CODES = {
    "btb_lc_number": "import_btb_lc_number_invalid",
    "btb_lc_date": "import_btb_lc_date_invalid",
    "btb_lc_value": "import_btb_lc_amount_invalid",
    "currency": "import_currency_missing_or_mismatch",
    "seller_pi_numbers": "import_pi_number_invalid",
    "related_export_lc_number": "import_related_export_lc_invalid",
}

_DASH_TRANSLATION = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
    }
)


@dataclass(slots=True, frozen=True)
class ExtractedPage:
    page_number: int
    text: str
    extraction_method: str
    confidence: float
    embedded_text_reliable: bool
    token_confidences: tuple[tuple[str, float], ...] = ()


@dataclass(slots=True, frozen=True)
class FieldCandidate:
    raw: str
    page_number: int
    matched_text: str
    extraction_method: str
    confidence: float
    hint: str | None = None


class ImportBTBLCPageProvider(Protocol):
    def embedded_pages(self, *, pdf_path: Path, page_limit: int) -> list[ExtractedPage]:
        """Extract bounded embedded-text pages."""

    def ocr_pages(
        self,
        *,
        pdf_path: Path,
        page_numbers: list[int],
    ) -> list[ExtractedPage]:
        """OCR only the requested bounded pages."""


@dataclass(slots=True)
class PDFImportBTBLCPageProvider:
    render_dpi: int = 300

    def embedded_pages(self, *, pdf_path: Path, page_limit: int) -> list[ExtractedPage]:
        fitz = _load_pymupdf()
        document = fitz.open(str(pdf_path))
        try:
            pages = []
            for page_index in range(min(page_limit, document.page_count)):
                text = str(document[page_index].get_text("text") or "")
                pages.append(
                    ExtractedPage(
                        page_number=page_index + 1,
                        text=text,
                        extraction_method="embedded_text",
                        confidence=1.0,
                        embedded_text_reliable=_embedded_text_is_reliable(text),
                    )
                )
            return pages
        finally:
            document.close()

    def ocr_pages(
        self,
        *,
        pdf_path: Path,
        page_numbers: list[int],
    ) -> list[ExtractedPage]:
        if not page_numbers:
            return []
        fitz = _load_pymupdf()
        pytesseract = _load_pytesseract()
        image_module = _load_pillow_image()
        document = fitz.open(str(pdf_path))
        try:
            pages = []
            for page_number in sorted(set(page_numbers)):
                if page_number < 1 or page_number > document.page_count:
                    continue
                page = document[page_number - 1]
                matrix = fitz.Matrix(self.render_dpi / 72.0, self.render_dpi / 72.0)
                image_bytes = page.get_pixmap(matrix=matrix, alpha=False).tobytes("png")
                image = image_module.open(BytesIO(image_bytes))
                data = pytesseract.image_to_data(
                    image,
                    output_type=pytesseract.Output.DICT,
                    lang="eng",
                )
                text, confidence, token_confidences = _ocr_text_and_confidence(data)
                pages.append(
                    ExtractedPage(
                        page_number=page_number,
                        text=text,
                        extraction_method="ocr",
                        confidence=confidence,
                        embedded_text_reliable=False,
                        token_confidences=token_confidences,
                    )
                )
            return pages
        finally:
            document.close()


def extract_import_btb_lc_path(
    *,
    input_path: Path,
    output_directory: Path,
    page_provider: ImportBTBLCPageProvider | None = None,
) -> list[dict[str, object]]:
    pdf_paths = _resolve_pdf_inputs(input_path)
    output_directory.mkdir(parents=True, exist_ok=True)
    results = []
    for pdf_path in pdf_paths:
        artifact = extract_import_btb_lc_pdf(
            pdf_path=pdf_path,
            page_provider=page_provider,
        )
        output_path = output_directory / f"{pdf_path.name}.import-btb-lc.json"
        atomic_write_text(output_path, pretty_json_dumps(artifact))
        results.append(
            {
                "source_path": str(pdf_path.resolve()),
                "output_path": str(output_path.resolve()),
                "decision": artifact["overall_extraction_decision"],
            }
        )
    return results


def extract_import_btb_lc_pdf(
    *,
    pdf_path: Path,
    page_provider: ImportBTBLCPageProvider | None = None,
) -> dict[str, object]:
    source_path = pdf_path.resolve()
    if not source_path.is_file() or source_path.suffix.casefold() != ".pdf":
        raise ValueError(f"Input must be a regular PDF file: {pdf_path}")

    provider = page_provider or PDFImportBTBLCPageProvider()
    embedded_pages = provider.embedded_pages(
        pdf_path=source_path,
        page_limit=IMPORT_BTB_LC_PAGE_LIMIT,
    )
    if not embedded_pages:
        raise ValueError(f"PDF contains no readable pages: {source_path}")

    unreliable_numbers = [
        page.page_number for page in embedded_pages if not page.embedded_text_reliable
    ]
    initial_ocr_pages = provider.ocr_pages(
        pdf_path=source_path,
        page_numbers=unreliable_numbers,
    )
    selected_pages = [
        _ocr_page_for_number(initial_ocr_pages, page.page_number) or page
        for page in embedded_pages
    ]

    candidate_groups = _collect_all_candidates(selected_pages)
    missing_fields = [
        field_name for field_name, candidates in candidate_groups.items() if not candidates
    ]
    reliable_page_numbers = [
        page.page_number
        for page in embedded_pages
        if page.embedded_text_reliable and page.page_number not in unreliable_numbers
    ]
    supplemental_ocr_pages = []
    if missing_fields and reliable_page_numbers:
        supplemental_ocr_pages = provider.ocr_pages(
            pdf_path=source_path,
            page_numbers=reliable_page_numbers,
        )
        supplemental_candidates = _collect_all_candidates(supplemental_ocr_pages)
        for field_name in missing_fields:
            if supplemental_candidates[field_name]:
                candidate_groups[field_name] = supplemental_candidates[field_name]

    btb_field = _resolve_field(
        field_name="btb_lc_number",
        candidates=candidate_groups["btb_lc_number"],
        canonicalizer=_canonicalize_btb_number,
    )
    bank_detection = _detect_bank(
        canonical_btb_number=btb_field["canonical"],
        pages=selected_pages + supplemental_ocr_pages,
    )
    bank_id = bank_detection["bank_id"]

    fields = {
        "btb_lc_number": btb_field,
        "btb_lc_date": _resolve_field(
            field_name="btb_lc_date",
            candidates=candidate_groups["btb_lc_date"],
            canonicalizer=lambda raw, _hint: _canonicalize_date(raw),
        ),
        "btb_lc_value": _resolve_field(
            field_name="btb_lc_value",
            candidates=candidate_groups["btb_lc_value"],
            canonicalizer=lambda raw, _hint: _canonicalize_amount(raw, bank_id=bank_id),
        ),
        "currency": _resolve_field(
            field_name="currency",
            candidates=candidate_groups["currency"],
            canonicalizer=lambda raw, _hint: _canonicalize_currency(raw),
        ),
        "seller_pi_numbers": _resolve_multi_field(
            field_name="seller_pi_numbers",
            candidates=candidate_groups["seller_pi_numbers"],
            canonicalizer=_canonicalize_pi,
        ),
        "related_export_lc_number": _resolve_field(
            field_name="related_export_lc_number",
            candidates=candidate_groups["related_export_lc_number"],
            canonicalizer=_canonicalize_related_export_lc,
        ),
    }

    hard_blocks = []
    for field_name, field_payload in fields.items():
        validation = field_payload["validation"]
        if validation["status"] == "hard_block":
            hard_blocks.append(
                {
                    "code": validation["code"],
                    "severity": "hard_block",
                    "message": validation["message"],
                    "field": field_name,
                    "details": {
                        "raw": field_payload["raw"],
                        "canonical": field_payload["canonical"],
                        "matches": field_payload["matches"],
                    },
                }
            )

    if bank_detection["status"] != "detected":
        hard_blocks.append(
            {
                "code": "import_btb_lc_number_invalid",
                "severity": "hard_block",
                "message": "The issuing bank could not be detected deterministically.",
                "field": "detected_bank",
                "details": {"evidence": bank_detection["evidence"]},
            }
        )

    filename_comparison = _compare_filename_to_btb_number(
        filename=source_path.name,
        canonical_btb_number=fields["btb_lc_number"]["canonical"],
    )
    warnings = []
    if not hard_blocks and filename_comparison["matches"] is False:
        warnings.append(
            {
                "code": "import_filename_number_mismatch",
                "severity": "warning",
                "message": "The PDF filename stem does not match the extracted BTB LC number.",
                "field": "filename_comparison",
                "details": filename_comparison,
            }
        )

    decision = "hard_block" if hard_blocks else ("warning" if warnings else "pass")
    extraction_methods = sorted(
        {
            page.extraction_method
            for page in selected_pages + supplemental_ocr_pages
            if page.text.strip()
        }
    )
    return {
        "schema_id": IMPORT_BTB_LC_EXTRACTION_SCHEMA_ID,
        "schema_version": IMPORT_BTB_LC_EXTRACTION_SCHEMA_VERSION,
        "report_schema_version": IMPORT_BTB_LC_REPORT_SCHEMA_VERSION,
        "workflow_id": "import_btb_lc",
        "source": {
            "path": str(source_path),
            "filename": source_path.name,
            "file_sha256": sha256_file(source_path),
            "page_limit": IMPORT_BTB_LC_PAGE_LIMIT,
            "pages_inspected": len(embedded_pages),
            "extraction_methods_used": extraction_methods,
        },
        "bank_detection": bank_detection,
        "fields": fields,
        "filename_comparison": filename_comparison,
        "warnings": warnings,
        "hard_block_discrepancies": hard_blocks,
        "overall_extraction_decision": decision,
    }


def _collect_all_candidates(
    pages: list[ExtractedPage],
) -> dict[str, list[FieldCandidate]]:
    amount_pairs = _collect_amount_currency_candidates(pages)
    return {
        "btb_lc_number": _collect_btb_number_candidates(pages),
        "btb_lc_date": _collect_date_candidates(pages),
        "btb_lc_value": [
            amount_candidate
            for _currency_candidate, amount_candidate in amount_pairs
        ],
        "currency": [
            currency_candidate
            for currency_candidate, _amount_candidate in amount_pairs
        ],
        "seller_pi_numbers": _collect_pi_candidates(pages),
        "related_export_lc_number": _collect_related_export_lc_candidates(pages),
    }


def _collect_btb_number_candidates(pages: list[ExtractedPage]) -> list[FieldCandidate]:
    broad_patterns = (
        re.compile(r"(?<!\d)(0742[0-9 ]{9,16})(?!\d)"),
        re.compile(r"(?<!\d)(0002228[0-9 ]{9,18})(?!\d)"),
        re.compile(r"(?<!\d)(1080[0-9 ]{9,16})(?!\d)"),
        re.compile(r"(?<!\d)(3085[0-9 ]{9,16})(?!\d)"),
        re.compile(r"(?<![A-Z0-9])(41101[0-9 ]{7,14}\s*-\s*[A-Z]?)(?![A-Z0-9])", re.IGNORECASE),
    )
    anchored_candidates = []
    fallback_candidates = []
    for page in pages:
        normalized_text = _safe_text(page.text)
        for pattern in broad_patterns:
            for match in pattern.finditer(normalized_text):
                fallback_candidates.append(_candidate(page, match.group(1), match.group(0)))
        lines = normalized_text.splitlines()
        for index, line in enumerate(lines):
            if "DOCUMENTARY CREDIT NUMBER" not in line.upper():
                continue
            for following_line in lines[index + 1 : index + 21]:
                token = following_line.strip()
                if re.fullmatch(r"[0-9][A-Z0-9 -]{8,24}", token, re.IGNORECASE):
                    anchored_candidates.append(_candidate(page, token, token))
                    break
    return _dedupe_candidates(anchored_candidates or fallback_candidates)


def _collect_date_candidates(pages: list[ExtractedPage]) -> list[FieldCandidate]:
    pattern = re.compile(
        r"(?is)\b31C\s*:?\s*(?:DATE\s+OF\s+ISSUE)?\s*"
        r"(\d{6}|\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|"
        r"\d{1,2}[- ]?[A-Z]{3}[- ,]?\d{2,4})"
    )
    candidates = []
    for page in pages:
        compact = _compact_text(page.text)
        for match in pattern.finditer(compact):
            candidates.append(_candidate(page, match.group(1), match.group(0)))
    return _dedupe_candidates(candidates)


def _collect_amount_currency_candidates(
    pages: list[ExtractedPage],
) -> list[tuple[FieldCandidate, FieldCandidate]]:
    block_pattern = re.compile(
        r"(?is)\b32B\s*:?\s*(?:CURRENCY\s+CODE,\s*AMOUNT)?\s*"
        r"(.{0,180}?)(?=\b(?:39A|39C|41A|41D|41a|42A|42C|42M|42P)\b)"
    )
    candidates = []
    for page in pages:
        compact = _compact_text(page.text)
        for block_match in block_pattern.finditer(compact):
            block = block_match.group(1).strip()
            pair = _currency_amount_from_block(block)
            if pair is None:
                continue
            currency_raw, amount_raw = pair
            candidates.append(
                (
                    _candidate(page, currency_raw, block_match.group(0)),
                    _candidate(page, amount_raw, block_match.group(0)),
                )
            )
    unique = {}
    for currency_candidate, amount_candidate in candidates:
        key = (
            currency_candidate.raw.upper(),
            amount_candidate.raw,
            currency_candidate.page_number,
            currency_candidate.extraction_method,
        )
        unique.setdefault(key, (currency_candidate, amount_candidate))
    return list(unique.values())


def _currency_amount_from_block(block: str) -> tuple[str, str] | None:
    patterns = (
        re.compile(
            r"(?is)\bCURRENCY\s*:?\s*([A-Z]{3})\b.*?\bAMOUNT\s*:?\s*([0-9][0-9.,]*)"
        ),
        re.compile(r"(?is)\b([A-Z]{3})\s*([0-9][0-9.,]*)"),
        re.compile(r"(?is)\b([A-Z]{3})\b(?:\s+[A-Z]+){0,3}\s+([0-9][0-9.,]*)"),
    )
    for pattern in patterns:
        match = pattern.search(block)
        if match is not None:
            return match.group(1), match.group(2)
    return None


def _collect_pi_candidates(pages: list[ExtractedPage]) -> list[FieldCandidate]:
    pattern = re.compile(
        r"(?i)\b(?:BTL|KYL)(?:\s*[/\\-]\s*|\s+)"
        r"[A-Z0-9]{1,4}(?:\s*[/\\-]\s*|\s+)[A-Z0-9]{1,8}\b"
    )
    candidates = []
    for page in pages:
        if not _is_lc_message_page(page.text):
            continue
        compact = _compact_text(page.text)
        for match in pattern.finditer(compact):
            candidates.append(_candidate(page, match.group(0), match.group(0)))
    return _dedupe_candidates_in_order(candidates)


def _collect_related_export_lc_candidates(
    pages: list[ExtractedPage],
) -> list[FieldCandidate]:
    date_boundary = r"\s*[,;']?\s*(?=DATE(?:D)?\b|DAT\b|DT\b)"
    identifier = r"([A-Z0-9][A-Z0-9 /-]{2,38}?[A-Z0-9])"
    brac_candidate = _collect_brac_primary_related_export_lc_candidate(
        pages=pages,
        identifier=identifier,
        date_boundary=date_boundary,
    )
    if brac_candidate is not None:
        return [brac_candidate]

    patterns = (
        re.compile(
            r"(?is)\b(?:SALES\s+CONTRACT\s*/\s*)?"
            r"EXPORT\s+(?:L\s*/\s*C|LC)(?:\s*/\s*SC)?\s*"
            r"(?:NO|NUMBER)\s*[.:#-]?\s*"
            + identifier
            + date_boundary
        ),
        re.compile(
            r"(?is)\bEXPORT\s+CONTRACT\s+NUMBER\s*/\s*EXPORT\s+"
            r"(?:L\s*/\s*C|LC)\s+NUMBER\s*:\s*"
            + identifier
            + date_boundary
        ),
        re.compile(
            r"(?is)\bALL\s+SHIPPING\s+DOCUMENTS\s+MUST\s+BEAR\s+THE\s+"
            r"L\s*/\s*C\s+NUMBER(?:\s+WITH\s+DATE)?\s*[:#-]?\s*"
            + identifier
            + date_boundary
        ),
        re.compile(
            r"(?is)\bL\s*/\s*C\s+NUMBER\s+WITH\s+DATE\s+AND\s+"
            r"EXPORT\s+SALES\s+CONTRACT\s+(?:NO|NUMBER)\s*[.:#-]?\s*"
            + identifier
            + date_boundary
        ),
    )
    candidates = []
    for page in pages:
        compact = _compact_text(page.text)
        for pattern in patterns:
            for match in pattern.finditer(compact):
                candidates.append(
                    FieldCandidate(
                        raw=match.group(1),
                        page_number=page.page_number,
                        matched_text=match.group(0),
                        extraction_method=page.extraction_method,
                        confidence=_matched_token_confidence(page, match.group(1)),
                        hint="LC",
                    )
                )
    return _dedupe_candidates(candidates)


def _collect_brac_primary_related_export_lc_candidate(
    *,
    pages: list[ExtractedPage],
    identifier: str,
    date_boundary: str,
) -> FieldCandidate | None:
    if not _pages_identify_brac(pages):
        return None
    patterns = (
        re.compile(
            r"(?is)\bALL\s+SHIPPING\s+DOCUMENTS\s+MUST\s+BEAR\s+THE\s+"
            r"L\s*/\s*C\s+NUMBER\s+WITH\s+DATE(?:\s+AND)?\s+"
            r"EXPORT\s+(?:L\s*/\s*C|LC)\s+(?:NO|NUMBER)\s*[.:#-]*\s*"
            + identifier
            + date_boundary
        ),
        re.compile(
            r"(?is)\bALL\s+SHIPPING\s+DOCUMENTS\s+MUST\s+BEAR\s+THE\s+"
            r"L\s*/\s*C\s+NUMBER\s+WITH\s+DATE\s+AND\s+"
            r"EXPORT\s+SALES\s+CONTRACT\s+(?:NO|NUMBER)\s*[.:#-]*\s*"
            r"(?:EXPORT\s+(?:L\s*/\s*C|LC)\s+"
            r"(?:NO|NUMBER)\s*[.:#-]*\s*)?"
            + identifier
            + date_boundary
        ),
        re.compile(
            r"(?is)\bALL\s+SHIPPING\s+DOCUMENTS\s+MUST\s+BEAR\s+THE\s+"
            r"L\s*/\s*C\s+NUMBER\s+WITH\s+DATE\s*[:#-]?\s*"
            r"(?!AND\b)"
            + identifier
            + date_boundary
        ),
        re.compile(
            r"(?is)\bALL\s+SHIPPING\s+DOCUMENTS\s+MUST\s+BEAR\s+THE\s+"
            r"L\s*/\s*C\s+NUMBER(?!\s+WITH\b)\s*[:#-]\s*"
            + identifier
            + date_boundary
        ),
        re.compile(
            r"(?is)\bALL\s+SHIPPING\s+DOCUMENTS\s+MUST\s+BEAR\s+THE\s+"
            r"EXPORT(?:\s+SALES\s+CONTRACT)?\s+(?:NO|NUMBER)\s*[.:#-]*\s*"
            + identifier
            + date_boundary
        ),
    )
    matches = []
    for page in pages:
        compact = _compact_text(page.text)
        for pattern in patterns:
            for match in pattern.finditer(compact):
                matches.append((page.page_number, match.start(), page, match))
    if not matches:
        return None
    _page_number, _start, page, match = min(
        matches,
        key=lambda item: (item[0], item[1]),
    )
    return FieldCandidate(
        raw=match.group(1),
        page_number=page.page_number,
        matched_text=match.group(0),
        extraction_method=page.extraction_method,
        confidence=_matched_token_confidence(page, match.group(1)),
        hint="LC",
    )


def _pages_identify_brac(pages: list[ExtractedPage]) -> bool:
    compact = _compact_text(" ".join(page.text for page in pages))
    return bool(
        re.search(r"(?<!\d)3085\d{9}(?!\d)", compact)
        or re.search(r"(?i)\b(?:BRAKBDDH\w*|BRAC\s+BANK\s+PLC)\b", compact)
    )


def _resolve_field(
    *,
    field_name: str,
    candidates: list[FieldCandidate],
    canonicalizer: Callable[[str, str | None], str | None],
) -> dict[str, object]:
    matches = []
    canonical_groups: dict[str, list[FieldCandidate]] = {}
    invalid_candidates = []
    for candidate in candidates:
        canonical = canonicalizer(candidate.raw, candidate.hint)
        match_payload = {
            "raw": candidate.raw,
            "canonical": canonical,
            "page_number": candidate.page_number,
            "matched_text": candidate.matched_text,
            "extraction_method": candidate.extraction_method,
            "confidence": candidate.confidence,
        }
        matches.append(match_payload)
        if canonical is None:
            invalid_candidates.append(candidate)
        else:
            canonical_groups.setdefault(canonical, []).append(candidate)

    selected = None
    validation_status = "pass"
    validation_message = "The field was extracted and validated deterministically."
    validation_code = None
    if not candidates:
        validation_status = "hard_block"
        validation_message = "The required field was not found in the first three pages."
        validation_code = _FIELD_CODES[field_name]
    elif invalid_candidates or len(canonical_groups) != 1:
        validation_status = "hard_block"
        validation_message = (
            "The required field was malformed, ambiguous, or contained conflicting values."
        )
        validation_code = _FIELD_CODES[field_name]
    else:
        canonical = next(iter(canonical_groups))
        selected = sorted(
            canonical_groups[canonical],
            key=lambda item: (
                item.page_number,
                0 if item.extraction_method == "embedded_text" else 1,
                item.raw,
            ),
        )[0]
        threshold = _OCR_THRESHOLDS[field_name]
        if selected.extraction_method == "ocr" and selected.confidence < threshold:
            validation_status = "hard_block"
            validation_message = (
                f"OCR confidence {selected.confidence:.4f} is below the required "
                f"{threshold:.2f} threshold."
            )
            validation_code = "ocr_required_field_below_threshold"

    selected_canonical = (
        canonicalizer(selected.raw, selected.hint) if selected is not None else None
    )
    return {
        "raw": selected.raw if selected is not None else None,
        "canonical": selected_canonical,
        "page_number": selected.page_number if selected is not None else None,
        "matched_text": selected.matched_text if selected is not None else None,
        "extraction_method": selected.extraction_method if selected is not None else None,
        "confidence": selected.confidence if selected is not None else None,
        "validation": {
            "status": validation_status,
            "code": validation_code,
            "message": validation_message,
        },
        "matches": matches,
    }


def _resolve_multi_field(
    *,
    field_name: str,
    candidates: list[FieldCandidate],
    canonicalizer: Callable[[str, str | None], str | None],
) -> dict[str, object]:
    matches = []
    canonical_order: list[str] = []
    canonical_groups: dict[str, list[FieldCandidate]] = {}
    invalid_candidates = []
    for candidate in candidates:
        canonical = canonicalizer(candidate.raw, candidate.hint)
        matches.append(
            {
                "raw": candidate.raw,
                "canonical": canonical,
                "page_number": candidate.page_number,
                "matched_text": candidate.matched_text,
                "extraction_method": candidate.extraction_method,
                "confidence": candidate.confidence,
            }
        )
        if canonical is None:
            invalid_candidates.append(candidate)
            continue
        if canonical not in canonical_groups:
            canonical_order.append(canonical)
            canonical_groups[canonical] = []
        canonical_groups[canonical].append(candidate)

    selected_candidates = []
    low_confidence_candidates = []
    threshold = _OCR_THRESHOLDS[field_name]
    for canonical in canonical_order:
        selected = sorted(
            canonical_groups[canonical],
            key=lambda item: (
                0 if item.extraction_method == "embedded_text" else 1,
                -item.confidence,
                item.page_number,
            ),
        )[0]
        selected_candidates.append((canonical, selected))
        if selected.extraction_method == "ocr" and selected.confidence < threshold:
            low_confidence_candidates.append(selected)

    validation_status = "pass"
    validation_code = None
    validation_message = (
        "All distinct seller PI numbers were extracted and validated deterministically."
    )
    if not candidates:
        validation_status = "hard_block"
        validation_code = _FIELD_CODES[field_name]
        validation_message = (
            "No valid seller PI number was found in the first three pages."
        )
    elif invalid_candidates:
        validation_status = "hard_block"
        validation_code = _FIELD_CODES[field_name]
        validation_message = (
            "One or more seller PI-like values did not match an approved pattern."
        )
    elif low_confidence_candidates:
        validation_status = "hard_block"
        validation_code = "ocr_required_field_below_threshold"
        validation_message = (
            "One or more seller PI numbers were below the required OCR confidence "
            f"threshold of {threshold:.2f}."
        )

    values = [
        {
            "raw": selected.raw,
            "canonical": canonical,
            "page_number": selected.page_number,
            "matched_text": selected.matched_text,
            "extraction_method": selected.extraction_method,
            "confidence": selected.confidence,
            "validation": {
                "status": (
                    "hard_block"
                    if selected in low_confidence_candidates
                    else "pass"
                ),
                "code": (
                    "ocr_required_field_below_threshold"
                    if selected in low_confidence_candidates
                    else None
                ),
            },
        }
        for canonical, selected in selected_candidates
    ]
    return {
        "raw": [value["raw"] for value in values],
        "canonical": [value["canonical"] for value in values],
        "values": values,
        "validation": {
            "status": validation_status,
            "code": validation_code,
            "message": validation_message,
        },
        "matches": matches,
    }


def _canonicalize_btb_number(raw: str, _hint: str | None) -> str | None:
    normalized = _normalize_identifier_outer(raw)
    if any(character.isspace() for character in normalized):
        return None
    matches = [
        bank_id for bank_id, _bank_name, pattern in _BANK_PATTERNS if pattern.fullmatch(normalized)
    ]
    return normalized if len(matches) == 1 else None


def _canonicalize_date(raw: str) -> str | None:
    value = _safe_text(raw).strip()
    if re.fullmatch(r"\d{6}", value):
        try:
            return datetime.strptime(value, "%y%m%d").date().isoformat()
        except ValueError:
            return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None
    normalized = re.sub(r"\s+", " ", value)
    for date_format in (
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%m-%Y",
        "%d-%m-%y",
        "%d.%m.%Y",
        "%d.%m.%y",
        "%d-%b-%Y",
        "%d-%b-%y",
        "%d %b %Y",
        "%d %b %y",
    ):
        try:
            return datetime.strptime(normalized, date_format).date().isoformat()
        except ValueError:
            continue
    return None


def _canonicalize_amount(raw: str, *, bank_id: object) -> str | None:
    value = _safe_text(raw).strip()
    if not value or re.search(r"[^0-9.,]", value):
        return None
    if bank_id == "mutual_trust_bank_limited":
        canonical_text = _parse_grouped_decimal(
            value,
            grouping_separator=",",
            decimal_separator=".",
            decimal_places=(1, 2, 3),
        )
    elif bank_id in {
        "the_city_bank_plc",
        "al_arafah_islami_bank_plc",
        "brac_bank_plc",
        "standard_chartered_bank",
    }:
        canonical_text = _parse_grouped_decimal(
            value,
            grouping_separator=".",
            decimal_separator=",",
            decimal_places=(0, 1, 2),
        )
    else:
        canonical_text = _parse_unambiguous_decimal(value)
    if canonical_text is None:
        return None
    try:
        amount = Decimal(canonical_text)
    except InvalidOperation:
        return None
    return canonical_text if amount > 0 else None


def _parse_grouped_decimal(
    value: str,
    *,
    grouping_separator: str,
    decimal_separator: str,
    decimal_places: tuple[int, ...],
) -> str | None:
    if value.count(decimal_separator) > 1:
        return None
    if decimal_separator in value:
        integer_part, fractional_part = value.rsplit(decimal_separator, 1)
        if len(fractional_part) not in decimal_places:
            return None
        if fractional_part and not fractional_part.isdigit():
            return None
    else:
        integer_part, fractional_part = value, ""
    if grouping_separator in integer_part:
        groups = integer_part.split(grouping_separator)
        if not _valid_grouped_integer(groups):
            return None
        integer_digits = "".join(groups)
    else:
        if not integer_part.isdigit():
            return None
        integer_digits = integer_part
    if grouping_separator in fractional_part:
        return None
    return integer_digits + (f".{fractional_part}" if fractional_part else "")


def _parse_unambiguous_decimal(value: str) -> str | None:
    if "," in value and "." in value:
        decimal_separator = "," if value.rfind(",") > value.rfind(".") else "."
        grouping_separator = "." if decimal_separator == "," else ","
        return _parse_grouped_decimal(
            value,
            grouping_separator=grouping_separator,
            decimal_separator=decimal_separator,
            decimal_places=(1, 2, 3),
        )
    if "," not in value and "." not in value:
        return value if value.isdigit() else None
    separator = "," if "," in value else "."
    if value.count(separator) != 1:
        return None
    integer_part, fractional_part = value.split(separator)
    if not integer_part.isdigit() or not fractional_part.isdigit():
        return None
    if len(fractional_part) not in (1, 2):
        return None
    return f"{integer_part}.{fractional_part}"


def _canonicalize_currency(raw: str) -> str | None:
    value = _safe_text(raw).strip().upper()
    return value if re.fullmatch(r"[A-Z]{3}", value) else None


def _canonicalize_pi(raw: str, _hint: str | None) -> str | None:
    value = _safe_text(raw).strip().upper()
    return value if re.fullmatch(r"(?:BTL|KYL)/\d{2}/\d{4}", value) else None


def _canonicalize_related_export_lc(raw: str, hint: str | None) -> str | None:
    value = _normalize_identifier_outer(raw)
    match = re.fullmatch(r"(LC|SC)\s*[- ]*\s*(.+)", value)
    if match is not None:
        prefix = match.group(1)
        body = match.group(2).strip("-")
    elif hint in {"LC", "SC"}:
        prefix = hint
        body = value
    else:
        return None
    body = re.sub(r"\s+", "-", body)
    body = re.sub(r"-+", "-", body).strip("-")
    if not re.fullmatch(r"[A-Z0-9]+(?:(?:-|/)[A-Z0-9]+)*", body):
        return None
    return f"{prefix}-{body}"


def _valid_grouped_integer(groups: list[str]) -> bool:
    if len(groups) < 2:
        return False
    if not groups[0].isdigit() or not 1 <= len(groups[0]) <= 3:
        return False
    if any(not group.isdigit() for group in groups[1:]):
        return False
    western_grouping = all(len(group) == 3 for group in groups[1:])
    indian_grouping = (
        len(groups[-1]) == 3
        and all(len(group) == 2 for group in groups[1:-1])
    )
    return western_grouping or indian_grouping


def _is_lc_message_page(text: str) -> bool:
    compact = _compact_text(text)
    field_codes = set(
        re.findall(
            r"(?i)(?:^|\s):?(20|31C|32B|45A|46A|47A|71D|78):?(?=\s)",
            compact,
        )
    )
    if len(field_codes) >= 2:
        return True
    if re.search(
        r"(?i)(?:^|\s):?(?:"
        r"20:?\s+DOCUMENTARY\s+CREDIT\s+NUMBER|"
        r"31C:?\s+DATE\s+OF\s+ISSUE|"
        r"32B:?\s+CURRENCY\s+CODE|"
        r"45A:?\s+DESCRIPTION\s+OF\s+GOODS|"
        r"46A:?\s+DOCUMENTS\s+REQUIRED|"
        r"47A:?\s+ADDITIONAL\s+CONDITIONS|"
        r"71D:?\s+CHARGES|"
        r"78:?\s+INSTRUCTIONS"
        r")\b",
        compact,
    ):
        return True
    upper_text = compact.upper()
    return "MT700" in upper_text and "DOCUMENTARY CREDIT" in upper_text


def _detect_bank(
    *,
    canonical_btb_number: object,
    pages: list[ExtractedPage],
) -> dict[str, object]:
    evidence = []
    detected = []
    if isinstance(canonical_btb_number, str):
        for bank_id, bank_name, pattern in _BANK_PATTERNS:
            if pattern.fullmatch(canonical_btb_number):
                detected.append((bank_id, bank_name))
                evidence.append(
                    {
                        "type": "btb_number_pattern",
                        "bank_id": bank_id,
                        "matched_text": canonical_btb_number,
                    }
                )
    if len(detected) == 1:
        bank_id, bank_name = detected[0]
        for page in pages:
            compact = _compact_text(page.text)
            for marker_pattern in _BANK_MARKERS[bank_id]:
                marker = re.search(marker_pattern, compact, re.IGNORECASE)
                if marker is not None:
                    evidence.append(
                        {
                            "type": "text_marker",
                            "bank_id": bank_id,
                            "page_number": page.page_number,
                            "matched_text": marker.group(0),
                            "extraction_method": page.extraction_method,
                        }
                    )
        return {
            "status": "detected",
            "bank_id": bank_id,
            "bank_name": bank_name,
            "evidence": evidence,
        }
    return {
        "status": "ambiguous" if detected else "not_detected",
        "bank_id": None,
        "bank_name": None,
        "evidence": evidence,
    }


def _compare_filename_to_btb_number(
    *,
    filename: str,
    canonical_btb_number: object,
) -> dict[str, object]:
    normalized_stem = _normalize_identifier_outer(Path(filename).stem)
    normalized_number = (
        _normalize_identifier_outer(canonical_btb_number)
        if isinstance(canonical_btb_number, str)
        else None
    )
    return {
        "filename_stem_raw": Path(filename).stem,
        "filename_stem_normalized": normalized_stem,
        "btb_lc_number_normalized": normalized_number,
        "matches": normalized_stem == normalized_number if normalized_number else None,
    }


def _resolve_pdf_inputs(input_path: Path) -> list[Path]:
    resolved = input_path.resolve()
    if resolved.is_file():
        if resolved.suffix.casefold() != ".pdf":
            raise ValueError(f"Input file must be a PDF: {input_path}")
        return [resolved]
    if not resolved.is_dir():
        raise ValueError(f"Input path does not exist: {input_path}")
    pdf_paths = sorted(
        (
            path.resolve()
            for path in resolved.iterdir()
            if path.is_file() and path.suffix.casefold() == ".pdf"
        ),
        key=lambda path: (path.name.casefold(), str(path)),
    )
    if not pdf_paths:
        raise ValueError(f"Input directory contains no PDF files: {input_path}")
    return pdf_paths


def _candidate(page: ExtractedPage, raw: str, matched_text: str) -> FieldCandidate:
    return FieldCandidate(
        raw=_safe_text(raw).strip(),
        page_number=page.page_number,
        matched_text=_compact_text(matched_text),
        extraction_method=page.extraction_method,
        confidence=_matched_token_confidence(page, raw),
    )


def _dedupe_candidates(candidates: list[FieldCandidate]) -> list[FieldCandidate]:
    unique = {}
    for candidate in candidates:
        key = (
            candidate.raw,
            candidate.page_number,
            candidate.extraction_method,
            candidate.hint,
        )
        unique.setdefault(key, candidate)
    return sorted(
        unique.values(),
        key=lambda item: (
            item.page_number,
            0 if item.extraction_method == "embedded_text" else 1,
            item.raw,
        ),
    )


def _dedupe_candidates_in_order(
    candidates: list[FieldCandidate],
) -> list[FieldCandidate]:
    unique = {}
    for candidate in candidates:
        key = (
            candidate.raw,
            candidate.page_number,
            candidate.extraction_method,
            candidate.hint,
        )
        unique.setdefault(key, candidate)
    return list(unique.values())


def _normalize_identifier_outer(value: object) -> str:
    normalized = _safe_text(value).translate(_DASH_TRANSLATION).strip().upper()
    return normalized


def _safe_text(value: object) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", str(value or ""))
        if unicodedata.category(character) not in {"Cc", "Cf"} or character in "\n\t"
    )


def _compact_text(value: object) -> str:
    return re.sub(r"\s+", " ", _safe_text(value)).strip()


def _embedded_text_is_reliable(text: str) -> bool:
    compact = _compact_text(text)
    return len(compact) >= 80 and len(re.findall(r"[A-Za-z0-9]", compact)) >= 50


def _ocr_page_for_number(
    pages: list[ExtractedPage],
    page_number: int,
) -> ExtractedPage | None:
    return next((page for page in pages if page.page_number == page_number and page.text.strip()), None)


def _ocr_text_and_confidence(
    data: object,
) -> tuple[str, float, tuple[tuple[str, float], ...]]:
    if not isinstance(data, dict):
        return "", 0.0, ()
    texts = list(data.get("text", []))
    confidences = list(data.get("conf", []))
    block_numbers = list(data.get("block_num", []))
    paragraph_numbers = list(data.get("par_num", []))
    line_numbers = list(data.get("line_num", []))
    line_tokens: dict[tuple[int, int, int], list[str]] = {}
    accepted_confidences = []
    token_confidences = []
    for index, (raw_text, raw_confidence) in enumerate(zip(texts, confidences)):
        token = str(raw_text).strip()
        if not token:
            continue
        try:
            confidence = float(raw_confidence) / 100.0
        except (TypeError, ValueError):
            continue
        if confidence < 0:
            continue
        key = (
            int(block_numbers[index]) if index < len(block_numbers) else 0,
            int(paragraph_numbers[index]) if index < len(paragraph_numbers) else 0,
            int(line_numbers[index]) if index < len(line_numbers) else index,
        )
        line_tokens.setdefault(key, []).append(token)
        accepted_confidences.append(confidence)
        token_confidences.append((token, confidence))
    text = "\n".join(" ".join(tokens) for tokens in line_tokens.values())
    confidence = (
        round(sum(accepted_confidences) / len(accepted_confidences), 4)
        if accepted_confidences
        else 0.0
    )
    return text, confidence, tuple(token_confidences)


def _matched_token_confidence(page: ExtractedPage, raw_value: object) -> float:
    if page.extraction_method != "ocr" or not page.token_confidences:
        return page.confidence
    target = _confidence_match_key(raw_value)
    if not target:
        return page.confidence
    normalized_tokens = [
        (_confidence_match_key(token), confidence)
        for token, confidence in page.token_confidences
        if _confidence_match_key(token)
    ]
    direct_matches = [
        confidence for token, confidence in normalized_tokens if target in token
    ]
    if direct_matches:
        return round(max(direct_matches), 4)
    for start in range(len(normalized_tokens)):
        combined = ""
        confidences = []
        for token, confidence in normalized_tokens[start:]:
            combined += token
            confidences.append(confidence)
            if combined == target:
                return round(sum(confidences) / len(confidences), 4)
            if len(combined) >= len(target) or not target.startswith(combined):
                break
    return page.confidence


def _confidence_match_key(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", _safe_text(value).upper())


def _load_pymupdf():
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise ValueError("PyMuPDF is required for import BTB LC extraction") from exc
    return fitz


def _load_pytesseract():
    try:
        import pytesseract  # type: ignore
    except ImportError as exc:
        raise ValueError("pytesseract is required for import BTB LC OCR fallback") from exc
    return pytesseract


def _load_pillow_image():
    try:
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise ValueError("Pillow is required for import BTB LC OCR fallback") from exc
    return Image
