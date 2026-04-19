from __future__ import annotations

import json
import re
import unicodedata
from io import BytesIO
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

from project.models import SavedDocument
from project.erp.normalization import normalize_lc_sc_date, normalize_lc_sc_number


LC_SC_CANDIDATE_PATTERN = re.compile(r"(?i)\b(?:LC|SC)\s*[- ]\s*[A-Z0-9]+(?:\s*-\s*[A-Z0-9]+){0,8}\b")
LC_SC_LABEL_PATTERN = re.compile(
    r"(?i)\b(?:L|S)\s*/?\s*C\s*(?:NO|NUMBER)\.?\s*[:\-|]?\s*"
)
LC_SC_VALUE_BOUNDARY_PATTERN = re.compile(
    r"(?i)(?="
    r"\b(?:UD|IP|EXP)\s*(?:NO|NUMBER|(?:ISSUE\s*)?DATE)\b"
    r"|\b(?:QTY|QUANTITY)\b|\bPI\s*NO\b|\bAMENDMENT\b"
    r")"
)
PI_CANDIDATE_PATTERN = re.compile(r"(?i)\bPDL\s*[- ]*\s*\d{2}\s*[- ]*\s*\d{1,4}(?:\s*[- ]*\s*R\d+)?\b")
AMENDMENT_CANDIDATE_PATTERN = re.compile(
    r"(?i)\b(?:AMD|AMND|AMENDMENT)(?:\s*(?:NO|NUMBER|#)\.?\s*)?[-:|]?\s*0*(\d{1,3})\b"
)
UD_IP_EXP_CANDIDATE_PATTERN = re.compile(
    r"(?i)\b(?:UD|IP|EXP)(?:[\s./\\_:;,\-]+[A-Z0-9]+){1,10}\b"
)
UD_IP_EXP_DOCUMENT_LABEL_PATTERN = re.compile(
    r"(?i)\b(?:UD|IP|EXP)\s*(?:NO|NUMBER)\.?\s*[:\-|]?\s*"
)
UD_IP_EXP_DOCUMENT_VALUE_BOUNDARY_PATTERN = re.compile(
    r"(?i)(?="
    r"\b(?:UD|IP|EXP)\s*(?:ISSUE\s*)?DATE\b"
    r"|\bL\s*/?\s*C\s*(?:NO|NUMBER|(?:ISSUE\s*)?DATE)\b"
    r"|\bS\s*/?\s*C\s*(?:NO|NUMBER|(?:ISSUE\s*)?DATE)\b"
    r"|\b(?:BUYER(?:\s+NAME)?|NAME\s+OF\s+BUYERS?)\b"
    r"|\b(?:QTY|QUANTITY)\b|\bPI\s*NO\b|\bAMENDMENT\b"
    r")"
)
DOCUMENT_DATE_VALUE_PATTERN = (
    r"\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}-[A-Z]{3}-\d{2,4}"
)
DOCUMENT_SPECIFIC_DATE_LABEL_PATTERN = re.compile(
    rf"(?i)\b(?:UD|IP|EXP)\s*(?:ISSUE\s*)?DATE\b\s*[:\-|]?\s*({DOCUMENT_DATE_VALUE_PATTERN})"
)
DOCUMENT_DATE_LABEL_PATTERN = re.compile(
    rf"(?i)\bDATE\b\s*[:\-|]?\s*({DOCUMENT_DATE_VALUE_PATTERN})"
)
QUANTITY_LABEL_PATTERN = re.compile(
    r"(?i)\b(?:QTY|QUANTITY)\b\s*[:\-|]?\s*([\d,]+(?:\.\d+)?)\s*(YDS?|YARDS?|MTRS?|METER|METERS|METRE|METRES)\b"
)
_UD_IP_EXP_PREFIX_RE = re.compile(r"^(UD|IP|EXP)(?:[\s./\\_:;,\-]+|$)(.*)$")
_UD_IP_EXP_SEPARATOR_RE = re.compile(r"[\s/\\_\-]+")
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


@dataclass(slots=True, frozen=True)
class SavedDocumentAnalysis:
    analysis_basis: str
    extracted_lc_sc_number: str | None = None
    extracted_lc_sc_confidence: float | None = None
    extracted_pi_number: str | None = None
    extracted_pi_confidence: float | None = None
    extracted_document_number: str | None = None
    extracted_document_number_confidence: float | None = None
    extracted_document_date: str | None = None
    extracted_document_date_confidence: float | None = None
    extracted_quantity: str | None = None
    extracted_quantity_unit: str | None = None
    extracted_amendment_number: str | None = None
    clause_related_lc_sc_number: str | None = None
    clause_excerpt: str | None = None
    clause_confidence: float | None = None
    extracted_lc_sc_provenance: dict[str, object] | None = None
    extracted_pi_provenance: dict[str, object] | None = None
    extracted_document_number_provenance: dict[str, object] | None = None
    extracted_document_date_provenance: dict[str, object] | None = None
    extracted_quantity_provenance: dict[str, object] | None = None
    extracted_amendment_provenance: dict[str, object] | None = None
    clause_provenance: dict[str, object] | None = None


class SavedDocumentAnalysisProvider(Protocol):
    def analyze(self, *, saved_document: SavedDocument) -> SavedDocumentAnalysis:
        """Return deterministic analysis metadata for a saved document."""


def extract_saved_document_raw_report(
    *,
    saved_document: SavedDocument,
    mode: str = "layered",
    search_text: str | None = None,
    page_from: int | None = None,
    page_to: int | None = None,
) -> dict[str, object]:
    document_path = Path(saved_document.destination_path)
    if not document_path.exists():
        raise ValueError(f"Document path does not exist: {document_path}")

    if mode == "text":
        report = _build_text_extraction_report(document_path, _load_pymupdf_module())
    elif mode == "table":
        report = _build_table_extraction_report(document_path, _load_pdfplumber_module())
    elif mode == "img2table":
        report = _build_img2table_extraction_report(
            document_path=document_path,
            pdf_class=_load_img2table_pdf_class(),
            ocr_class=_load_img2table_tesseract_ocr_class(),
        )
    elif mode == "ocr":
        report = _build_ocr_extraction_report(
            document_path=document_path,
            fitz_module=_load_pymupdf_module(),
            pytesseract_module=_load_pytesseract_module(),
            pil_image_module=_load_pil_image_module(),
        )
    elif mode == "layered":
        try:
            img2table_pdf_class = _load_img2table_pdf_class()
            img2table_ocr_class = _load_img2table_tesseract_ocr_class()
        except ValueError:
            img2table_pdf_class = None
            img2table_ocr_class = None
        report = _build_layered_extraction_report(
            document_path=document_path,
            fitz_module=_load_pymupdf_module(),
            pdfplumber_module=_load_pdfplumber_module(),
            img2table_pdf_class=img2table_pdf_class,
            img2table_ocr_class=img2table_ocr_class,
            pytesseract_module=_load_pytesseract_module(),
            pil_image_module=_load_pil_image_module(),
        )
    else:
        raise ValueError(f"Unsupported raw-text extraction mode: {mode}")

    if search_text:
        report["search"] = _search_extraction_report(
            report=report,
            search_text=search_text,
            page_from=page_from,
            page_to=page_to,
        )
    return report


@dataclass(slots=True, frozen=True)
class NullSavedDocumentAnalysisProvider:
    def analyze(self, *, saved_document: SavedDocument) -> SavedDocumentAnalysis:
        del saved_document
        return SavedDocumentAnalysis(analysis_basis="none")


@dataclass(slots=True, frozen=True)
class JsonManifestSavedDocumentAnalysisProvider:
    manifest_path: Path

    def analyze(self, *, saved_document: SavedDocument) -> SavedDocumentAnalysis:
        payload = _load_manifest(self.manifest_path)
        match = _match_manifest_record(payload, saved_document)
        if match is None:
            return SavedDocumentAnalysis(analysis_basis="none")
        extracted_document_number = _optional_canonical_ud_ip_exp_number(match.get("document_number"))
        extracted_document_number_confidence = _optional_float(match.get("document_number_confidence"))
        extracted_document_date = _optional_canonical_date(match.get("document_date"))
        extracted_document_date_confidence = _optional_float(match.get("document_date_confidence"))
        extracted_quantity = _optional_quantity(match.get("quantity"))
        extracted_quantity_unit = _optional_quantity_unit(match.get("quantity_unit"))
        extracted_lc_sc_number = _optional_string(match.get("extracted_lc_sc_number")) or _optional_string(
            match.get("lc_sc_number")
        )
        extracted_lc_sc_confidence = _optional_float_or_default(
            match.get("extracted_lc_sc_confidence"),
            default=_optional_float(match.get("lc_sc_number_confidence")),
        )
        extracted_pi_number = _optional_string(match.get("extracted_pi_number"))
        extracted_pi_confidence = _optional_float(match.get("extracted_pi_confidence"))
        extracted_amendment_number = _optional_amendment_number(match.get("extracted_amendment_number"))
        clause_related_lc_sc_number = _optional_string(match.get("clause_related_lc_sc_number"))
        clause_excerpt = _optional_string(match.get("clause_excerpt"))
        clause_confidence = _optional_float(match.get("clause_confidence"))
        return SavedDocumentAnalysis(
            analysis_basis="json_manifest",
            extracted_lc_sc_number=extracted_lc_sc_number,
            extracted_lc_sc_confidence=extracted_lc_sc_confidence,
            extracted_pi_number=extracted_pi_number,
            extracted_pi_confidence=extracted_pi_confidence,
            extracted_document_number=extracted_document_number,
            extracted_document_number_confidence=extracted_document_number_confidence,
            extracted_document_date=extracted_document_date,
            extracted_document_date_confidence=extracted_document_date_confidence,
            extracted_quantity=extracted_quantity,
            extracted_quantity_unit=extracted_quantity_unit,
            extracted_amendment_number=extracted_amendment_number,
            clause_related_lc_sc_number=clause_related_lc_sc_number,
            clause_excerpt=clause_excerpt,
            clause_confidence=clause_confidence,
            extracted_lc_sc_provenance=(
                _manifest_field_provenance(
                    record=match,
                    field_name="extracted_lc_sc",
                    default_confidence=extracted_lc_sc_confidence,
                    default_excerpt=clause_excerpt,
                )
                or _manifest_field_provenance(
                    record=match,
                    field_name="lc_sc_number",
                    default_confidence=extracted_lc_sc_confidence,
                    default_excerpt=clause_excerpt,
                )
            )
            if extracted_lc_sc_number
            else None,
            extracted_pi_provenance=_manifest_field_provenance(
                record=match,
                field_name="extracted_pi",
                default_confidence=extracted_pi_confidence,
                default_excerpt=clause_excerpt,
            )
            if extracted_pi_number
            else None,
            extracted_document_number_provenance=_manifest_field_provenance(
                record=match,
                field_name="document_number",
                default_confidence=extracted_document_number_confidence,
            )
            if extracted_document_number
            else None,
            extracted_document_date_provenance=_manifest_field_provenance(
                record=match,
                field_name="document_date",
                default_confidence=extracted_document_date_confidence,
            )
            if extracted_document_date
            else None,
            extracted_quantity_provenance=_manifest_field_provenance(
                record=match,
                field_name="quantity",
            )
            if extracted_quantity
            else None,
            extracted_amendment_provenance=_manifest_field_provenance(
                record=match,
                field_name="extracted_amendment",
                default_excerpt=clause_excerpt,
            )
            if extracted_amendment_number
            else None,
            clause_provenance=_manifest_field_provenance(
                record=match,
                field_name="clause",
                default_confidence=clause_confidence,
                default_excerpt=clause_excerpt,
            )
            if clause_excerpt or clause_confidence is not None or clause_related_lc_sc_number
            else None,
        )


@dataclass(slots=True)
class PyMuPDFSavedDocumentAnalysisProvider:
    _fitz_module: object | None = field(default=None, init=False, repr=False)

    def analyze(self, *, saved_document: SavedDocument) -> SavedDocumentAnalysis:
        document_path = Path(saved_document.destination_path)
        if not document_path.exists():
            return SavedDocumentAnalysis(analysis_basis="missing_saved_document")

        try:
            text_report = _build_text_extraction_report(document_path, self._get_fitz_module())
        except Exception:
            return SavedDocumentAnalysis(analysis_basis="pymupdf_text_error")

        if not str(text_report.get("combined_text", "")).strip():
            return SavedDocumentAnalysis(analysis_basis="pymupdf_text_empty")
        return _analysis_from_page_text_report(
            report=text_report,
            analysis_basis="pymupdf_text",
            extraction_method_resolver=lambda page: str(page.get("strategy", "text") or "text"),
            default_confidence=1.0,
        )

    def _get_fitz_module(self):
        if self._fitz_module is None:
            self._fitz_module = _load_pymupdf_module()
        return self._fitz_module


@dataclass(slots=True)
class PDFPlumberSavedDocumentAnalysisProvider:
    _pdfplumber_module: object | None = field(default=None, init=False, repr=False)

    def analyze(self, *, saved_document: SavedDocument) -> SavedDocumentAnalysis:
        document_path = Path(saved_document.destination_path)
        if not document_path.exists():
            return SavedDocumentAnalysis(analysis_basis="missing_saved_document")

        try:
            table_report = _build_table_extraction_report(document_path, self._get_pdfplumber_module())
        except Exception:
            return SavedDocumentAnalysis(analysis_basis="pdfplumber_table_error")

        if not str(table_report.get("combined_text", "")).strip():
            return SavedDocumentAnalysis(analysis_basis="pdfplumber_table_empty")
        analysis = _analysis_from_page_text_report(
            report=table_report,
            analysis_basis="pdfplumber_table",
            extraction_method_resolver=lambda page: "table",
            page_text_resolver=lambda page: _combine_table_page_text(page.get("tables", [])),
            default_confidence=1.0,
        )
        if not _analysis_has_identifier(analysis) and analysis.extracted_amendment_number is None:
            return SavedDocumentAnalysis(analysis_basis="pdfplumber_table_empty")
        return analysis

    def _get_pdfplumber_module(self):
        if self._pdfplumber_module is None:
            self._pdfplumber_module = _load_pdfplumber_module()
        return self._pdfplumber_module


@dataclass(slots=True)
class Img2TableSavedDocumentAnalysisProvider:
    _pdf_class: object | None = field(default=None, init=False, repr=False)
    _ocr_class: object | None = field(default=None, init=False, repr=False)

    def analyze(self, *, saved_document: SavedDocument) -> SavedDocumentAnalysis:
        document_path = Path(saved_document.destination_path)
        if not document_path.exists():
            return SavedDocumentAnalysis(analysis_basis="missing_saved_document")

        try:
            report = _build_img2table_extraction_report(
                document_path=document_path,
                pdf_class=self._get_pdf_class(),
                ocr_class=self._get_ocr_class(),
            )
        except Exception:
            return SavedDocumentAnalysis(analysis_basis="img2table_table_error")

        if not str(report.get("combined_text", "")).strip():
            return SavedDocumentAnalysis(analysis_basis="img2table_table_empty")

        analysis = _analysis_from_page_text_report(
            report=report,
            analysis_basis="img2table_table",
            extraction_method_resolver=lambda page: "img2table",
            page_text_resolver=lambda page: _combine_table_page_text(page.get("tables", [])),
            default_confidence=1.0,
        )
        if not _analysis_has_identifier(analysis) and analysis.extracted_amendment_number is None:
            return SavedDocumentAnalysis(analysis_basis="img2table_table_empty")
        return analysis

    def _get_pdf_class(self):
        if self._pdf_class is None:
            self._pdf_class = _load_img2table_pdf_class()
        return self._pdf_class

    def _get_ocr_class(self):
        if self._ocr_class is None:
            self._ocr_class = _load_img2table_tesseract_ocr_class()
        return self._ocr_class


@dataclass(slots=True)
class LayeredTableSavedDocumentAnalysisProvider:
    primary_provider: SavedDocumentAnalysisProvider = field(default_factory=PDFPlumberSavedDocumentAnalysisProvider)
    fallback_provider: SavedDocumentAnalysisProvider = field(default_factory=Img2TableSavedDocumentAnalysisProvider)

    def analyze(self, *, saved_document: SavedDocument) -> SavedDocumentAnalysis:
        primary_analysis = self.primary_provider.analyze(saved_document=saved_document)
        if _analysis_has_identifier(primary_analysis) or primary_analysis.extracted_amendment_number is not None:
            return primary_analysis
        fallback_analysis = self.fallback_provider.analyze(saved_document=saved_document)
        return _merge_analysis(primary_analysis, fallback_analysis)


@dataclass(slots=True)
class OCRSavedDocumentAnalysisProvider:
    _fitz_module: object | None = field(default=None, init=False, repr=False)
    _pytesseract_module: object | None = field(default=None, init=False, repr=False)
    _pil_image_module: object | None = field(default=None, init=False, repr=False)

    def analyze(self, *, saved_document: SavedDocument) -> SavedDocumentAnalysis:
        document_path = Path(saved_document.destination_path)
        if not document_path.exists():
            return SavedDocumentAnalysis(analysis_basis="missing_saved_document")

        try:
            ocr_report = _build_ocr_extraction_report(
                document_path=document_path,
                fitz_module=self._get_fitz_module(),
                pytesseract_module=self._get_pytesseract_module(),
                pil_image_module=self._get_pil_image_module(),
            )
        except Exception:
            return SavedDocumentAnalysis(analysis_basis="ocr_text_error")

        if not str(ocr_report.get("combined_text", "")).strip():
            return SavedDocumentAnalysis(analysis_basis="ocr_text_empty")
        return _analysis_from_ocr_report(ocr_report, analysis_basis="ocr_text")

    def _get_fitz_module(self):
        if self._fitz_module is None:
            self._fitz_module = _load_pymupdf_module()
        return self._fitz_module

    def _get_pytesseract_module(self):
        if self._pytesseract_module is None:
            self._pytesseract_module = _load_pytesseract_module()
        return self._pytesseract_module

    def _get_pil_image_module(self):
        if self._pil_image_module is None:
            self._pil_image_module = _load_pil_image_module()
        return self._pil_image_module


@dataclass(slots=True)
class LayeredSavedDocumentAnalysisProvider:
    text_provider: SavedDocumentAnalysisProvider = field(default_factory=PyMuPDFSavedDocumentAnalysisProvider)
    table_provider: SavedDocumentAnalysisProvider = field(default_factory=LayeredTableSavedDocumentAnalysisProvider)
    ocr_provider: SavedDocumentAnalysisProvider = field(default_factory=OCRSavedDocumentAnalysisProvider)

    def analyze(self, *, saved_document: SavedDocument) -> SavedDocumentAnalysis:
        text_analysis = self.text_provider.analyze(saved_document=saved_document)
        table_analysis = self.table_provider.analyze(saved_document=saved_document)
        merged_analysis = _merge_analysis(text_analysis, table_analysis)
        if _analysis_has_identifier(merged_analysis) and not _analysis_needs_ud_ip_exp_completion(merged_analysis):
            return merged_analysis
        ocr_analysis = self.ocr_provider.analyze(saved_document=saved_document)
        return _merge_analysis(merged_analysis, ocr_analysis)


def _load_manifest(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("Saved-document analysis manifest must be a JSON array.")
    if not all(isinstance(item, dict) for item in payload):
        raise ValueError("Saved-document analysis manifest entries must be JSON objects.")
    return payload


def _match_manifest_record(payload: list[dict], saved_document: SavedDocument) -> dict | None:
    destination_path = Path(saved_document.destination_path)
    for item in payload:
        saved_document_id = _optional_string(item.get("saved_document_id"))
        if saved_document_id and saved_document_id == saved_document.saved_document_id:
            return item

        record_destination_path = _optional_string(item.get("destination_path"))
        if record_destination_path:
            try:
                if Path(record_destination_path) == destination_path:
                    return item
            except Exception:
                pass

        normalized_filename = _optional_string(item.get("normalized_filename"))
        if normalized_filename and normalized_filename == saved_document.normalized_filename:
            return item
    return None


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_canonical_ud_ip_exp_number(value: object) -> str | None:
    raw_value = _optional_string(value)
    if raw_value is None:
        return None
    canonical = _normalize_ud_ip_exp_document_number(raw_value)
    if canonical is None:
        raise ValueError("Saved-document analysis manifest document_number must be a valid UD/IP/EXP identifier.")
    return canonical


def _optional_canonical_date(value: object) -> str | None:
    raw_value = _optional_string(value)
    if raw_value is None:
        return None
    canonical = normalize_lc_sc_date(raw_value)
    if canonical is None:
        raise ValueError("Saved-document analysis manifest document_date must be a parseable date string.")
    return canonical


def _optional_quantity(value: object) -> str | None:
    raw_value = _optional_string(value)
    if raw_value is None:
        return None
    try:
        numeric = Decimal(raw_value.replace(",", ""))
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError("Saved-document analysis manifest quantity must be numeric when present.") from exc
    return _format_decimal(numeric)


def _optional_quantity_unit(value: object) -> str | None:
    raw_value = _optional_string(value)
    if raw_value is None:
        return None
    return _normalize_quantity_unit(raw_value)


def _format_decimal(value: Decimal) -> str:
    normalized = format(value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raise ValueError("Saved-document analysis manifest clause_confidence must be numeric when present.")


def _optional_amendment_number(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        match = AMENDMENT_CANDIDATE_PATTERN.search(stripped)
        if match is not None:
            return str(int(match.group(1)))
        if stripped.isdigit():
            return str(int(stripped))
    raise ValueError("Saved-document analysis manifest extracted_amendment_number must be numeric when present.")


def _manifest_field_provenance(
    *,
    record: dict[str, object],
    field_name: str,
    default_confidence: float | None = None,
    default_excerpt: str | None = None,
) -> dict[str, object] | None:
    nested = record.get(f"{field_name}_provenance")
    page_number: int | None = None
    extraction_method: str | None = None
    confidence = default_confidence
    excerpt = default_excerpt
    if isinstance(nested, dict):
        page_number = _optional_int(nested.get("page_number"))
        extraction_method = _optional_string(nested.get("extraction_method"))
        confidence = _optional_float_or_default(nested.get("confidence"), default=confidence)
        excerpt = _optional_string(nested.get("excerpt")) or excerpt
    page_number = page_number if page_number is not None else _optional_int(record.get(f"{field_name}_page_number"))
    extraction_method = extraction_method or _optional_string(record.get(f"{field_name}_extraction_method")) or "json_manifest"
    excerpt = excerpt or _optional_string(record.get(f"{field_name}_excerpt"))
    if (
        page_number is None
        and extraction_method is None
        and confidence is None
        and (excerpt is None or not excerpt.strip())
    ):
        return None
    payload: dict[str, object] = {"extraction_method": extraction_method}
    if page_number is not None:
        payload["page_number"] = page_number
    if confidence is not None:
        payload["confidence"] = confidence
    if excerpt is not None and excerpt.strip():
        payload["excerpt"] = excerpt.strip()
    return payload


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ValueError("Saved-document analysis manifest provenance page_number must be an integer when present.")


def _optional_float_or_default(value: object, *, default: float | None) -> float | None:
    if value is None:
        return default
    return _optional_float(value)


def _extract_pdf_text(path: Path, fitz_module: object) -> str:
    document = fitz_module.open(str(path))
    try:
        return "\n".join(_extract_page_text(page)[0] for page in document)
    finally:
        close = getattr(document, "close", None)
        if callable(close):
            close()


def _build_text_extraction_report(path: Path, fitz_module: object) -> dict[str, object]:
    document = fitz_module.open(str(path))
    try:
        pages: list[dict[str, object]] = []
        for page_index, page in enumerate(document, start=1):
            pages.append(_build_text_page_report(page_number=page_index, page=page))
    finally:
        close = getattr(document, "close", None)
        if callable(close):
            close()

    return {
        "mode": "text",
        "document_path": str(path),
        "page_count": len(pages),
        "combined_text": "\n".join(str(page["text"]) for page in pages),
        "pages": pages,
    }


def _build_layered_extraction_report(
    *,
    document_path: Path,
    fitz_module: object,
    pdfplumber_module: object,
    img2table_pdf_class: object | None,
    img2table_ocr_class: object | None,
    pytesseract_module: object,
    pil_image_module: object,
) -> dict[str, object]:
    text_report = _build_text_extraction_report(document_path, fitz_module)
    table_report = _build_table_extraction_report(document_path, pdfplumber_module)
    if img2table_pdf_class is not None and img2table_ocr_class is not None:
        try:
            img2table_report = _build_img2table_extraction_report(
                document_path=document_path,
                pdf_class=img2table_pdf_class,
                ocr_class=img2table_ocr_class,
            )
        except Exception as exc:
            img2table_report = {
                "mode": "img2table",
                "document_path": str(document_path),
                "page_count": 0,
                "combined_text": "",
                "pages": [],
                "status": "error",
                "error": str(exc),
            }
    else:
        img2table_report = {
            "mode": "img2table",
            "document_path": str(document_path),
            "page_count": 0,
            "combined_text": "",
            "pages": [],
            "status": "unavailable",
        }

    text_pages = {int(page["page_number"]): page for page in text_report["pages"] if isinstance(page, dict)}
    table_pages = {int(page["page_number"]): page for page in table_report["pages"] if isinstance(page, dict)}
    img2table_pages = {int(page["page_number"]): page for page in img2table_report["pages"] if isinstance(page, dict)}
    page_numbers = sorted(set(text_pages) | set(table_pages) | set(img2table_pages))

    ocr_pages: dict[int, dict[str, object]] = {}
    if page_numbers:
        document = fitz_module.open(str(document_path))
        try:
            document_pages = list(document)
            for page_number in page_numbers:
                text_page = text_pages.get(page_number, {})
                table_page = table_pages.get(page_number, {})
                img2table_page = img2table_pages.get(page_number, {})
                if not _page_requires_ocr(text_page, table_page, img2table_page):
                    continue
                ocr_pages[page_number] = _build_ocr_page_report(
                    page_number=page_number,
                    page=document_pages[page_number - 1],
                    pytesseract_module=pytesseract_module,
                    pil_image_module=pil_image_module,
                )
        finally:
            close = getattr(document, "close", None)
            if callable(close):
                close()

    layered_pages: list[dict[str, object]] = []
    for page_number in page_numbers:
        text_page = text_pages.get(page_number, {})
        table_page = table_pages.get(page_number, {})
        img2table_page = img2table_pages.get(page_number, {})
        ocr_page = ocr_pages.get(page_number, {})
        text_value = str(text_page.get("text", "") or "")
        table_tables = table_page.get("tables", []) if isinstance(table_page, dict) else []
        table_text = _combine_table_page_text(table_tables)
        img2table_tables = img2table_page.get("tables", []) if isinstance(img2table_page, dict) else []
        img2table_text = _combine_table_page_text(img2table_tables)
        if _page_has_sufficient_text(text_value):
            selected_source = "text"
            selected_text = text_value
        elif table_text.strip():
            selected_source = "table"
            selected_text = table_text
        elif img2table_text.strip():
            selected_source = "img2table"
            selected_text = img2table_text
        else:
            selected_source = "ocr"
            selected_text = str(ocr_page.get("text", "") or "")
        searchable_text = _join_non_empty_sections(
            selected_text,
            table_text if selected_source != "table" else "",
            img2table_text if selected_source != "img2table" else "",
        )
        layered_pages.append(
            {
                "page_number": page_number,
                "selected_source": selected_source,
                "selected_text": selected_text,
                "searchable_text": searchable_text,
                "ocr_attempted": page_number in ocr_pages,
                "text": text_page,
                "table": {
                    "page_number": page_number,
                    "tables": table_tables,
                    "combined_text": table_text,
                },
                "img2table": {
                    "page_number": page_number,
                    "tables": img2table_tables,
                    "combined_text": img2table_text,
                },
                "ocr": ocr_page,
            }
        )

    combined_text = "\n".join(
        str(page["searchable_text"]).strip()
        for page in layered_pages
        if str(page["searchable_text"]).strip()
    )
    return {
        "mode": "layered",
        "document_path": str(document_path),
        "page_count": len(layered_pages),
        "combined_text": combined_text,
        "pages": layered_pages,
        "categories": {
            "text": text_report,
            "table": table_report,
            "img2table": img2table_report,
            "ocr": {
                "mode": "ocr",
                "document_path": str(document_path),
                "page_count": len(page_numbers),
                "attempted_page_numbers": sorted(ocr_pages),
                "combined_text": "\n".join(
                    str(page.get("text", "")).strip() for page in ocr_pages.values() if str(page.get("text", "")).strip()
                ),
                "average_confidence": _average_confidence_for_pages(list(ocr_pages.values())),
                "pages": [ocr_pages[page_number] for page_number in sorted(ocr_pages)],
            },
        },
    }


def _extract_pdf_table_text(path: Path, pdfplumber_module: object) -> str:
    with pdfplumber_module.open(str(path)) as document:
        rows: list[str] = []
        for page in document.pages:
            extract_tables = getattr(page, "extract_tables", None)
            if not callable(extract_tables):
                continue
            for table in extract_tables() or []:
                for raw_row in table or []:
                    cells: list[str] = []
                    for cell in raw_row or []:
                        cell_text = " ".join(str(cell or "").split())
                        if cell_text:
                            cells.append(cell_text)
                    if cells:
                        rows.append(" | ".join(cells))
        return "\n".join(rows)


def _build_table_extraction_report(path: Path, pdfplumber_module: object) -> dict[str, object]:
    with pdfplumber_module.open(str(path)) as document:
        pages: list[dict[str, object]] = []
        combined_rows: list[str] = []
        for page_index, page in enumerate(document.pages, start=1):
            extract_tables = getattr(page, "extract_tables", None)
            page_tables: list[dict[str, object]] = []
            if callable(extract_tables):
                for table_index, table in enumerate(extract_tables() or [], start=1):
                    normalized_rows: list[list[str]] = []
                    combined_table_rows: list[str] = []
                    for raw_row in table or []:
                        normalized_row = [" ".join(str(cell or "").split()) for cell in raw_row or []]
                        normalized_rows.append(normalized_row)
                        row_text = " | ".join(cell for cell in normalized_row if cell)
                        if row_text:
                            combined_table_rows.append(row_text)
                            combined_rows.append(row_text)
                    page_tables.append(
                        {
                            "table_index": table_index,
                            "rows": normalized_rows,
                            "combined_text": "\n".join(combined_table_rows),
                        }
                    )
            pages.append(
                {
                    "page_number": page_index,
                    "tables": page_tables,
                }
            )

    return {
        "mode": "table",
        "document_path": str(path),
        "page_count": len(pages),
        "combined_text": "\n".join(combined_rows),
        "pages": pages,
    }


def _build_img2table_extraction_report(
    *,
    document_path: Path,
    pdf_class: object,
    ocr_class: object,
) -> dict[str, object]:
    pdf_document = pdf_class(str(document_path))
    ocr_instance = ocr_class(n_threads=1, lang="eng")
    extracted = pdf_document.extract_tables(
        ocr=ocr_instance,
        implicit_rows=True,
        implicit_columns=True,
        borderless_tables=True,
        min_confidence=50,
    )
    normalized_pages = _normalize_img2table_page_payload(extracted)
    combined_rows: list[str] = []
    pages: list[dict[str, object]] = []
    for page_number in sorted(normalized_pages):
        page_tables: list[dict[str, object]] = []
        for table_index, raw_table in enumerate(normalized_pages[page_number], start=1):
            normalized_rows = _normalize_img2table_rows(raw_table)
            combined_table_rows: list[str] = []
            for normalized_row in normalized_rows:
                row_text = " | ".join(cell for cell in normalized_row if cell)
                if row_text:
                    combined_table_rows.append(row_text)
                    combined_rows.append(row_text)
            page_tables.append(
                {
                    "table_index": table_index,
                    "rows": normalized_rows,
                    "combined_text": "\n".join(combined_table_rows),
                }
            )
        pages.append(
            {
                "page_number": page_number,
                "tables": page_tables,
            }
        )

    return {
        "mode": "img2table",
        "document_path": str(document_path),
        "page_count": len(pages),
        "combined_text": "\n".join(combined_rows),
        "pages": pages,
    }


def _extract_pdf_text_with_ocr(
    *,
    document_path: Path,
    fitz_module: object,
    pytesseract_module: object,
    pil_image_module: object,
) -> tuple[str, float | None, list[str], list[float]]:
    document = fitz_module.open(str(document_path))
    try:
        page_payloads = [_extract_ocr_page_payload(page, pytesseract_module, pil_image_module) for page in document]
    finally:
        close = getattr(document, "close", None)
        if callable(close):
            close()

    tokens = [token for payload in page_payloads for token in payload["tokens"]]
    confidences = [confidence for payload in page_payloads for confidence in payload["confidences"]]
    text = " ".join(tokens)
    if not confidences:
        return text, None, tokens, confidences
    return text, round(sum(confidences) / len(confidences), 4), tokens, confidences


def _build_ocr_extraction_report(
    *,
    document_path: Path,
    fitz_module: object,
    pytesseract_module: object,
    pil_image_module: object,
) -> dict[str, object]:
    document = fitz_module.open(str(document_path))
    try:
        pages: list[dict[str, object]] = []
        all_tokens: list[str] = []
        all_confidences: list[float] = []
        for page_index, page in enumerate(document, start=1):
            payload = _extract_ocr_page_payload(page, pytesseract_module, pil_image_module)
            page_text = " ".join(payload["tokens"])
            confidences = [float(value) for value in payload["confidences"]]
            page_confidence = round(sum(confidences) / len(confidences), 4) if confidences else None
            pages.append(
                {
                    "page_number": page_index,
                    "text": page_text,
                    "tokens": list(payload["tokens"]),
                    "confidences": confidences,
                    "average_confidence": page_confidence,
                }
            )
            all_tokens.extend(payload["tokens"])
            all_confidences.extend(confidences)
    finally:
        close = getattr(document, "close", None)
        if callable(close):
            close()

    combined_text = " ".join(all_tokens)
    combined_confidence = round(sum(all_confidences) / len(all_confidences), 4) if all_confidences else None
    return {
        "mode": "ocr",
        "document_path": str(document_path),
        "page_count": len(pages),
        "combined_text": combined_text,
        "average_confidence": combined_confidence,
        "pages": pages,
    }


def _build_text_page_report(*, page_number: int, page: object) -> dict[str, object]:
    text, strategy = _extract_page_text(page)
    return {
        "page_number": page_number,
        "text": text,
        "line_count": len([line for line in text.splitlines() if line.strip()]),
        "character_count": len(text),
        "strategy": strategy,
    }


def _build_ocr_page_report(
    *,
    page_number: int,
    page: object,
    pytesseract_module: object,
    pil_image_module: object,
) -> dict[str, object]:
    payload = _extract_ocr_page_payload(page, pytesseract_module, pil_image_module)
    page_text = " ".join(payload["tokens"])
    confidences = [float(value) for value in payload["confidences"]]
    page_confidence = round(sum(confidences) / len(confidences), 4) if confidences else None
    return {
        "page_number": page_number,
        "text": page_text,
        "tokens": list(payload["tokens"]),
        "confidences": confidences,
        "average_confidence": page_confidence,
    }


def _analysis_from_page_text_report(
    report: dict[str, object],
    *,
    analysis_basis: str,
    extraction_method_resolver,
    page_text_resolver=None,
    default_confidence: float | None = None,
) -> SavedDocumentAnalysis:
    pages = report.get("pages", [])
    if not isinstance(pages, list):
        pages = []
    document_match = _first_match_from_pages(
        pages,
        matcher=_first_ud_ip_exp_document_match,
        extraction_method_resolver=extraction_method_resolver,
        page_text_resolver=page_text_resolver,
        confidence_resolver=lambda _page, _value: default_confidence,
    )
    document_date_match = _first_match_from_pages(
        pages,
        matcher=_first_document_date_match,
        extraction_method_resolver=extraction_method_resolver,
        page_text_resolver=page_text_resolver,
        confidence_resolver=lambda _page, _value: default_confidence,
    )
    quantity_match = _first_match_from_pages(
        pages,
        matcher=_first_quantity_match,
        extraction_method_resolver=extraction_method_resolver,
        page_text_resolver=page_text_resolver,
        confidence_resolver=lambda _page, _value: default_confidence,
    )
    lc_sc_match = _first_match_from_pages(
        pages,
        matcher=_first_lc_sc_match,
        extraction_method_resolver=extraction_method_resolver,
        page_text_resolver=page_text_resolver,
        confidence_resolver=lambda _page, _value: default_confidence,
    )
    pi_match = _first_match_from_pages(
        pages,
        matcher=_first_pi_match,
        extraction_method_resolver=extraction_method_resolver,
        page_text_resolver=page_text_resolver,
        confidence_resolver=lambda _page, _value: default_confidence,
    )
    amendment_match = _first_match_from_pages(
        pages,
        matcher=_first_amendment_number_match,
        extraction_method_resolver=extraction_method_resolver,
        page_text_resolver=page_text_resolver,
        confidence_resolver=lambda _page, _value: default_confidence,
    )
    clause_match = lc_sc_match or pi_match
    return SavedDocumentAnalysis(
        analysis_basis=analysis_basis,
        extracted_lc_sc_number=lc_sc_match["value"] if lc_sc_match is not None else None,
        extracted_lc_sc_confidence=lc_sc_match["confidence"] if lc_sc_match is not None else None,
        extracted_pi_number=pi_match["value"] if pi_match is not None else None,
        extracted_pi_confidence=pi_match["confidence"] if pi_match is not None else None,
        extracted_document_number=document_match["value"] if document_match is not None else None,
        extracted_document_number_confidence=document_match["confidence"] if document_match is not None else None,
        extracted_document_date=document_date_match["value"] if document_date_match is not None else None,
        extracted_document_date_confidence=(
            document_date_match["confidence"] if document_date_match is not None else None
        ),
        extracted_quantity=quantity_match["value"].split(" ", 1)[0] if quantity_match is not None else None,
        extracted_quantity_unit=(
            quantity_match["value"].split(" ", 1)[1] if quantity_match is not None else None
        ),
        extracted_amendment_number=amendment_match["value"] if amendment_match is not None else None,
        clause_related_lc_sc_number=lc_sc_match["value"] if lc_sc_match is not None else None,
        clause_excerpt=clause_match["excerpt"] if clause_match is not None else None,
        clause_confidence=clause_match["confidence"] if clause_match is not None else None,
        extracted_lc_sc_provenance=_provenance_from_match(lc_sc_match),
        extracted_pi_provenance=_provenance_from_match(pi_match),
        extracted_document_number_provenance=_provenance_from_match(document_match),
        extracted_document_date_provenance=_provenance_from_match(document_date_match),
        extracted_quantity_provenance=_provenance_from_match(quantity_match),
        extracted_amendment_provenance=_provenance_from_match(amendment_match),
        clause_provenance=_provenance_from_match(clause_match),
    )


def _analysis_from_ocr_report(report: dict[str, object], *, analysis_basis: str) -> SavedDocumentAnalysis:
    pages = report.get("pages", [])
    if not isinstance(pages, list):
        pages = []
    document_match = _first_match_from_pages(
        pages,
        matcher=_first_ud_ip_exp_document_match,
        extraction_method_resolver=lambda _page: "ocr",
        confidence_resolver=lambda page, _value: _safe_float(page.get("average_confidence")),
    )
    document_date_match = _first_match_from_pages(
        pages,
        matcher=_first_document_date_match,
        extraction_method_resolver=lambda _page: "ocr",
        confidence_resolver=lambda page, _value: _safe_float(page.get("average_confidence")),
    )
    quantity_match = _first_match_from_pages(
        pages,
        matcher=_first_quantity_match,
        extraction_method_resolver=lambda _page: "ocr",
        confidence_resolver=lambda page, _value: _safe_float(page.get("average_confidence")),
    )
    lc_sc_match = _first_match_from_pages(
        pages,
        matcher=_first_lc_sc_match,
        extraction_method_resolver=lambda _page: "ocr",
        confidence_resolver=lambda page, value: _field_confidence_from_tokens(
            _string_list(page.get("tokens")),
            _float_list(page.get("confidences")),
            value,
            normalize_lc_sc_number,
        ),
    )
    pi_match = _first_match_from_pages(
        pages,
        matcher=_first_pi_match,
        extraction_method_resolver=lambda _page: "ocr",
        confidence_resolver=lambda page, value: _field_confidence_from_tokens(
            _string_list(page.get("tokens")),
            _float_list(page.get("confidences")),
            value,
            _normalize_pi_number,
        ),
    )
    amendment_match = _first_match_from_pages(
        pages,
        matcher=_first_amendment_number_match,
        extraction_method_resolver=lambda _page: "ocr",
        confidence_resolver=lambda page, _value: _safe_float(page.get("average_confidence")),
    )
    clause_match = _clause_match_with_page_confidence(pages, lc_sc_match or pi_match)
    return SavedDocumentAnalysis(
        analysis_basis=analysis_basis,
        extracted_lc_sc_number=lc_sc_match["value"] if lc_sc_match is not None else None,
        extracted_lc_sc_confidence=lc_sc_match["confidence"] if lc_sc_match is not None else None,
        extracted_pi_number=pi_match["value"] if pi_match is not None else None,
        extracted_pi_confidence=pi_match["confidence"] if pi_match is not None else None,
        extracted_document_number=document_match["value"] if document_match is not None else None,
        extracted_document_number_confidence=document_match["confidence"] if document_match is not None else None,
        extracted_document_date=document_date_match["value"] if document_date_match is not None else None,
        extracted_document_date_confidence=(
            document_date_match["confidence"] if document_date_match is not None else None
        ),
        extracted_quantity=quantity_match["value"].split(" ", 1)[0] if quantity_match is not None else None,
        extracted_quantity_unit=(
            quantity_match["value"].split(" ", 1)[1] if quantity_match is not None else None
        ),
        extracted_amendment_number=amendment_match["value"] if amendment_match is not None else None,
        clause_related_lc_sc_number=lc_sc_match["value"] if lc_sc_match is not None else None,
        clause_excerpt=clause_match["excerpt"] if clause_match is not None else None,
        clause_confidence=clause_match["confidence"] if clause_match is not None else None,
        extracted_lc_sc_provenance=_provenance_from_match(lc_sc_match),
        extracted_pi_provenance=_provenance_from_match(pi_match),
        extracted_document_number_provenance=_provenance_from_match(document_match),
        extracted_document_date_provenance=_provenance_from_match(document_date_match),
        extracted_quantity_provenance=_provenance_from_match(quantity_match),
        extracted_amendment_provenance=_provenance_from_match(amendment_match),
        clause_provenance=_provenance_from_match(clause_match),
    )


def _first_match_from_pages(
    pages: list[object],
    *,
    matcher,
    extraction_method_resolver,
    page_text_resolver=None,
    confidence_resolver=None,
) -> dict[str, object] | None:
    for raw_page in pages:
        if not isinstance(raw_page, dict):
            continue
        text = (
            page_text_resolver(raw_page)
            if callable(page_text_resolver)
            else str(raw_page.get("text", "") or "")
        )
        if not text.strip():
            continue
        match = matcher(text)
        if match is None:
            continue
        confidence = confidence_resolver(raw_page, match[0]) if callable(confidence_resolver) else None
        return {
            "value": match[0],
            "page_number": _safe_int(raw_page.get("page_number")),
            "extraction_method": extraction_method_resolver(raw_page),
            "confidence": confidence,
            "excerpt": _build_clause_excerpt(text, match[1]),
        }
    return None


def _provenance_from_match(match: dict[str, object] | None) -> dict[str, object] | None:
    if match is None:
        return None
    payload: dict[str, object] = {}
    page_number = _safe_int(match.get("page_number"))
    extraction_method = _optional_string(match.get("extraction_method"))
    confidence = _safe_float(match.get("confidence"))
    excerpt = _optional_string(match.get("excerpt"))
    if page_number is not None:
        payload["page_number"] = page_number
    if extraction_method is not None:
        payload["extraction_method"] = extraction_method
    if confidence is not None:
        payload["confidence"] = confidence
    if excerpt is not None:
        payload["excerpt"] = excerpt
    return payload or None


def _clause_match_with_page_confidence(
    pages: list[object],
    match: dict[str, object] | None,
) -> dict[str, object] | None:
    if match is None:
        return None
    page_number = _safe_int(match.get("page_number"))
    if page_number is None:
        return match
    for page in pages:
        if not isinstance(page, dict):
            continue
        if _safe_int(page.get("page_number")) != page_number:
            continue
        page_confidence = _safe_float(page.get("average_confidence"))
        if page_confidence is None:
            return match
        return {**match, "confidence": page_confidence}
    return match


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _float_list(value: object) -> list[float]:
    if not isinstance(value, list):
        return []
    floats: list[float] = []
    for item in value:
        numeric = _safe_float(item)
        if numeric is not None:
            floats.append(numeric)
    return floats


def _extract_ocr_page_payload(page: object, pytesseract_module: object, pil_image_module: object) -> dict[str, list]:
    get_pixmap = getattr(page, "get_pixmap", None)
    if not callable(get_pixmap):
        return {"tokens": [], "confidences": []}
    pixmap = get_pixmap()
    tobytes = getattr(pixmap, "tobytes", None)
    if not callable(tobytes):
        return {"tokens": [], "confidences": []}
    image_bytes = tobytes("png")
    image = pil_image_module.open(BytesIO(image_bytes))
    output_type = getattr(getattr(pytesseract_module, "Output", object()), "DICT", None)
    data = pytesseract_module.image_to_data(image, output_type=output_type)
    texts = list(data.get("text", [])) if isinstance(data, dict) else []
    raw_confidences = list(data.get("conf", [])) if isinstance(data, dict) else []
    tokens: list[str] = []
    confidences: list[float] = []
    for raw_text, raw_confidence in zip(texts, raw_confidences):
        token = str(raw_text).strip()
        confidence = _normalize_ocr_confidence(raw_confidence)
        if not token or confidence is None:
            continue
        tokens.append(token)
        confidences.append(confidence)
    return {"tokens": tokens, "confidences": confidences}


def _extract_page_text(page: object) -> tuple[str, str]:
    structured_text = _extract_page_text_from_words(page)
    if structured_text is not None:
        return structured_text, "words_reconstructed"
    get_text = getattr(page, "get_text", None)
    if not callable(get_text):
        return "", "no_text_method"
    return str(get_text("text") or ""), "plain_text"


def _extract_page_text_from_words(page: object) -> str | None:
    get_text = getattr(page, "get_text", None)
    if not callable(get_text):
        return None
    try:
        raw_words = get_text("words")
    except Exception:
        return None
    if not isinstance(raw_words, list) or not raw_words:
        return None

    visual_lines: list[dict[str, object]] = []
    for raw_word in raw_words:
        if not isinstance(raw_word, (list, tuple)) or len(raw_word) < 8:
            continue
        x0 = _safe_float(raw_word[0])
        y0 = _safe_float(raw_word[1])
        x1 = _safe_float(raw_word[2])
        y1 = _safe_float(raw_word[3])
        token = " ".join(str(raw_word[4] or "").split())
        block_no = _safe_int(raw_word[5], default=0)
        if x0 is None or x1 is None or y0 is None or y1 is None or not token:
            continue
        y_center = (y0 + y1) / 2.0
        line = _find_matching_visual_line(visual_lines, block_no=block_no, y_center=y_center)
        if line is None:
            line = {
                "block_no": block_no,
                "y_center": y_center,
                "words": [],
            }
            visual_lines.append(line)
        else:
            existing_center = _safe_float(line.get("y_center"))
            if existing_center is not None:
                line["y_center"] = round((existing_center + y_center) / 2.0, 4)
        words = line.setdefault("words", [])
        if isinstance(words, list):
            words.append((x0, x1, token))

    if not visual_lines:
        return None

    line_texts: list[str] = []
    sorted_lines = sorted(
        visual_lines,
        key=lambda item: (
            _safe_int(item.get("block_no"), default=0),
            _safe_float(item.get("y_center")) or 0.0,
        ),
    )
    for line in sorted_lines:
        words = line.get("words", [])
        if not isinstance(words, list):
            continue
        line_text = _join_words_with_gap_preservation(sorted(words, key=lambda item: item[0]))
        if line_text.strip():
            line_texts.append(line_text)
    return "\n".join(line_texts)


def _join_words_with_gap_preservation(words: list[tuple[float, float, str]]) -> str:
    if not words:
        return ""
    parts = [words[0][2]]
    previous_x1 = words[0][1]
    for x0, x1, token in words[1:]:
        gap = max(0.0, x0 - previous_x1)
        separator = "    " if gap >= 24.0 else " "
        parts.append(separator)
        parts.append(token)
        previous_x1 = x1
    return "".join(parts).strip()


def _find_matching_visual_line(
    visual_lines: list[dict[str, object]],
    *,
    block_no: int,
    y_center: float,
) -> dict[str, object] | None:
    best_line: dict[str, object] | None = None
    best_distance: float | None = None
    for line in visual_lines:
        if _safe_int(line.get("block_no"), default=-1) != block_no:
            continue
        existing_center = _safe_float(line.get("y_center"))
        if existing_center is None:
            continue
        distance = abs(existing_center - y_center)
        if distance > 3.0:
            continue
        if best_distance is None or distance < best_distance:
            best_line = line
            best_distance = distance
    return best_line


def _page_has_sufficient_text(text: str) -> bool:
    collapsed = " ".join(text.split())
    if len(collapsed) >= 24:
        return True
    return len(re.findall(r"[A-Za-z0-9]", collapsed)) >= 12


def _page_requires_ocr(
    text_page: dict[str, object],
    table_page: dict[str, object],
    img2table_page: dict[str, object] | None = None,
) -> bool:
    text_value = str(text_page.get("text", "") or "")
    if _page_has_sufficient_text(text_value):
        return False
    table_text = _combine_table_page_text(table_page.get("tables", []))
    if table_text.strip():
        return False
    img2table_text = _combine_table_page_text((img2table_page or {}).get("tables", []))
    return not img2table_text.strip()


def _combine_table_page_text(tables: object) -> str:
    if not isinstance(tables, list):
        return ""
    rows: list[str] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        combined_text = str(table.get("combined_text", "") or "").strip()
        if combined_text:
            rows.append(combined_text)
    return "\n".join(rows)


def _average_confidence_for_pages(pages: list[dict[str, object]]) -> float | None:
    confidences: list[float] = []
    for page in pages:
        value = page.get("average_confidence")
        if isinstance(value, (float, int)):
            confidences.append(float(value))
    if not confidences:
        return None
    return round(sum(confidences) / len(confidences), 4)


def _join_non_empty_sections(*sections: str) -> str:
    return "\n".join(section.strip() for section in sections if section and section.strip())


def _normalize_img2table_page_payload(extracted: object) -> dict[int, list[object]]:
    if not isinstance(extracted, dict):
        return {}
    raw_page_numbers = [page_number for page_number in extracted if isinstance(page_number, int)]
    zero_based = 0 in raw_page_numbers
    normalized: dict[int, list[object]] = {}
    for raw_page_number, tables in extracted.items():
        if not isinstance(raw_page_number, int):
            continue
        page_number = raw_page_number + 1 if zero_based else raw_page_number
        if page_number < 1:
            continue
        if not isinstance(tables, list):
            continue
        normalized[page_number] = list(tables)
    return normalized


def _normalize_img2table_rows(raw_table: object) -> list[list[str]]:
    dataframe = getattr(raw_table, "df", None)
    if dataframe is None:
        return []
    fillna = getattr(dataframe, "fillna", None)
    if callable(fillna):
        dataframe = fillna("")
    values = getattr(dataframe, "values", None)
    if values is None:
        return []
    tolist = getattr(values, "tolist", None)
    raw_rows = tolist() if callable(tolist) else values
    if not isinstance(raw_rows, list):
        return []
    normalized_rows: list[list[str]] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, list):
            continue
        normalized_rows.append([" ".join(str(cell or "").split()) for cell in raw_row])
    return normalized_rows


def _search_extraction_report(
    *,
    report: dict[str, object],
    search_text: str,
    page_from: int | None,
    page_to: int | None,
) -> dict[str, object]:
    if not search_text.strip():
        raise ValueError("Search text must not be blank.")

    pages = report.get("pages", [])
    if not isinstance(pages, list):
        pages = []
    page_count = len(pages)
    start_page, end_page = _normalize_page_window(page_count=page_count, page_from=page_from, page_to=page_to)
    needle = search_text.casefold()
    matches: list[dict[str, object]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_number = _safe_int(page.get("page_number"))
        if page_number is None or page_number < start_page or page_number > end_page:
            continue
        searchable_text = _page_searchable_text(report_mode=str(report.get("mode", "")), page=page)
        if not searchable_text:
            continue
        page_matches = _find_text_occurrences(searchable_text, needle)
        if not page_matches:
            continue
        match_payload: dict[str, object] = {
            "page_number": page_number,
            "count": len(page_matches),
            "excerpts": [_build_search_excerpt(searchable_text, index, len(search_text)) for index in page_matches],
        }
        selected_source = page.get("selected_source")
        if isinstance(selected_source, str) and selected_source.strip():
            match_payload["selected_source"] = selected_source
        matches.append(match_payload)

    return {
        "search_text": search_text,
        "page_from": start_page,
        "page_to": end_page,
        "match_count": sum(int(match["count"]) for match in matches),
        "matches": matches,
    }


def _normalize_page_window(*, page_count: int, page_from: int | None, page_to: int | None) -> tuple[int, int]:
    if page_count <= 0:
        return 1, 0
    start_page = page_from if page_from is not None else 1
    end_page = page_to if page_to is not None else page_count
    if start_page < 1:
        raise ValueError("page_from must be at least 1.")
    if end_page < 1:
        raise ValueError("page_to must be at least 1.")
    if start_page > end_page:
        raise ValueError("page_from must be less than or equal to page_to.")
    if start_page > page_count:
        raise ValueError(f"page_from {start_page} exceeds page_count {page_count}.")
    if end_page > page_count:
        raise ValueError(f"page_to {end_page} exceeds page_count {page_count}.")
    return start_page, end_page


def _page_searchable_text(*, report_mode: str, page: dict[str, object]) -> str:
    if report_mode == "layered":
        return str(page.get("searchable_text", "") or "")
    if report_mode in {"table", "img2table"}:
        return _combine_table_page_text(page.get("tables", []))
    return str(page.get("text", "") or "")


def _find_text_occurrences(text: str, needle: str) -> list[int]:
    haystack = text.casefold()
    matches: list[int] = []
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            break
        matches.append(index)
        start = index + max(1, len(needle))
    return matches


def _build_search_excerpt(text: str, start_index: int, match_length: int) -> str:
    excerpt_start = max(0, start_index - 80)
    excerpt_end = min(len(text), start_index + match_length + 80)
    return " ".join(text[excerpt_start:excerpt_end].split())


def _safe_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: object, *, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _first_lc_sc_match(text: str) -> tuple[str, int] | None:
    labeled_match = _first_labeled_lc_sc_match(text)
    if labeled_match is not None:
        return labeled_match
    seen: set[str] = set()
    for match in LC_SC_CANDIDATE_PATTERN.finditer(text):
        if _is_embedded_ud_ip_exp_lc_sc_candidate(text, match.start()):
            continue
        normalized = normalize_lc_sc_number(match.group(0))
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        return normalized, match.start()
    return None


def _first_ud_ip_exp_document_match(text: str) -> tuple[str, int] | None:
    labeled_match = _first_labeled_ud_ip_exp_document_match(text)
    if labeled_match is not None:
        return labeled_match
    seen: set[str] = set()
    for match in UD_IP_EXP_CANDIDATE_PATTERN.finditer(text):
        normalized = _normalize_ud_ip_exp_document_number(match.group(0))
        if normalized is None or normalized in seen or _is_label_only_ud_ip_exp_identifier(normalized):
            continue
        seen.add(normalized)
        return normalized, match.start()
    return None


def _first_document_date_match(text: str) -> tuple[str, int] | None:
    for pattern in (DOCUMENT_SPECIFIC_DATE_LABEL_PATTERN, DOCUMENT_DATE_LABEL_PATTERN):
        for match in pattern.finditer(text):
            if pattern is DOCUMENT_DATE_LABEL_PATTERN and _is_lc_sc_issue_date_label(text, match.start()):
                continue
            normalized = normalize_lc_sc_date(match.group(1))
            if normalized is None:
                continue
            return normalized, match.start(1)
    return None


def _first_quantity_match(text: str) -> tuple[str, int] | None:
    match = QUANTITY_LABEL_PATTERN.search(text)
    if match is None:
        return None
    quantity = _optional_quantity(match.group(1))
    unit = _optional_quantity_unit(match.group(2))
    if quantity is None or unit is None:
        return None
    return f"{quantity} {unit}", match.start(1)


def _first_pi_match(text: str) -> tuple[str, int] | None:
    seen: set[str] = set()
    for match in PI_CANDIDATE_PATTERN.finditer(text):
        normalized = _normalize_pi_number(match.group(0))
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        return normalized, match.start()
    return None


def _first_amendment_number_match(text: str) -> tuple[str, int] | None:
    match = AMENDMENT_CANDIDATE_PATTERN.search(text)
    if match is None:
        return None
    return str(int(match.group(1))), match.start()


def _normalize_pi_number(raw_value: str) -> str | None:
    normalized = raw_value.strip().upper()
    normalized = normalized.replace("_", "-").replace(" ", "-")
    normalized = re.sub(r"-+", "-", normalized)
    match = re.fullmatch(r"PDL-(\d{2})-(\d{1,4})(?:-R(\d+))?", normalized)
    if match is None:
        return None
    year = match.group(1)
    serial = int(match.group(2))
    revision = match.group(3)
    canonical = f"PDL-{year}-{serial:04d}"
    if revision is not None:
        canonical += f"-R{int(revision)}"
    return canonical


def _normalize_ud_ip_exp_document_number(raw_value: str) -> str | None:
    normalized = _apply_ud_ip_exp_identifier_primitives(raw_value)
    match = _UD_IP_EXP_PREFIX_RE.match(normalized)
    if match is None:
        return None

    prefix = match.group(1)
    body = match.group(2).strip().strip(".,;:")
    if not body:
        return None

    body_tokens = [token for token in _UD_IP_EXP_SEPARATOR_RE.split(body) if token]
    if not body_tokens:
        return None
    if len(body_tokens) >= 2 and body_tokens[0] in {"LC", "SC"}:
        remainder = " ".join(body_tokens[2:])
        if remainder:
            return f"{prefix}-{body_tokens[0]}-{body_tokens[1]}-{remainder}"
        return f"{prefix}-{body_tokens[0]}-{body_tokens[1]}"
    return f"{prefix}-{'-'.join(body_tokens)}"


def _first_labeled_ud_ip_exp_document_match(text: str) -> tuple[str, int] | None:
    for match in UD_IP_EXP_DOCUMENT_LABEL_PATTERN.finditer(text):
        trailing_text = text[match.end() :]
        field_text = _slice_labeled_ud_ip_exp_value(trailing_text)
        if not field_text.strip():
            continue
        candidate_match = UD_IP_EXP_CANDIDATE_PATTERN.search(field_text)
        if candidate_match is None or candidate_match.start() > 12:
            continue
        normalized = _normalize_ud_ip_exp_document_number(candidate_match.group(0))
        if normalized is None or _is_label_only_ud_ip_exp_identifier(normalized):
            continue
        return normalized, match.end() + candidate_match.start()
    return None


def _slice_labeled_ud_ip_exp_value(text: str) -> str:
    lines = text.splitlines()
    first_line = lines[0] if lines else text
    boundary = UD_IP_EXP_DOCUMENT_VALUE_BOUNDARY_PATTERN.search(first_line)
    if boundary is not None:
        return first_line[: boundary.start()]
    return first_line


def _first_labeled_lc_sc_match(text: str) -> tuple[str, int] | None:
    for match in LC_SC_LABEL_PATTERN.finditer(text):
        trailing_text = text[match.end() :]
        field_text = _slice_labeled_lc_sc_value(trailing_text)
        if not field_text.strip():
            continue
        candidate_match = LC_SC_CANDIDATE_PATTERN.search(field_text)
        if candidate_match is None or candidate_match.start() > 8:
            continue
        normalized = normalize_lc_sc_number(candidate_match.group(0))
        if normalized is None:
            continue
        return normalized, match.end() + candidate_match.start()
    return None


def _slice_labeled_lc_sc_value(text: str) -> str:
    lines = text.splitlines()
    first_line = lines[0] if lines else text
    boundary = LC_SC_VALUE_BOUNDARY_PATTERN.search(first_line)
    if boundary is not None:
        return first_line[: boundary.start()]
    return first_line


def _is_embedded_ud_ip_exp_lc_sc_candidate(text: str, start_index: int) -> bool:
    if start_index <= 0:
        return False
    prefix_window = text[max(0, start_index - 12) : start_index]
    return re.search(r"(?i)(?:UD|IP|EXP)[\s./\\_:;,\-]+$", prefix_window) is not None


def _is_lc_sc_issue_date_label(text: str, start_index: int) -> bool:
    if start_index <= 0:
        return False
    prefix_window = text[max(0, start_index - 24) : start_index]
    return (
        re.search(
            r"(?i)(?:\bL\s*/?\s*C\b|\bS\s*/?\s*C\b|\bLC\b|\bSC\b)\s*(?:ISSUE\s*)?$",
            prefix_window,
        )
        is not None
    )


def _is_label_only_ud_ip_exp_identifier(value: str) -> bool:
    segments = [segment.strip().upper() for segment in value.split("-") if segment.strip()]
    if len(segments) < 2:
        return False
    return segments[1] in {"NO", "NUMBER", "DATE"}


def _apply_ud_ip_exp_identifier_primitives(raw_value: str) -> str:
    cleaned = "".join(_clean_ud_ip_exp_identifier_char(character) for character in str(raw_value))
    return re.sub(r"\s+", " ", cleaned).strip().upper()


def _clean_ud_ip_exp_identifier_char(character: str) -> str:
    if character in _ZERO_WIDTH or unicodedata.category(character)[0] == "C":
        return ""
    if character in _UNICODE_DASHES:
        return "-"
    return character


def _normalize_quantity_unit(raw_value: str) -> str:
    normalized = raw_value.strip().upper()
    if normalized in {"YD", "YARD", "YARDS"}:
        return "YDS"
    if normalized in {"MTR", "MTRS", "METER", "METERS", "METRE", "METRES"}:
        return "MTR"
    return normalized


def _build_clause_excerpt(text: str, seed_index: int | None) -> str | None:
    if seed_index is None:
        return None
    start = max(0, seed_index - 80)
    end = min(len(text), seed_index + 80)
    excerpt = " ".join(text[start:end].split())
    return excerpt or None


def _analysis_has_identifier(analysis: SavedDocumentAnalysis) -> bool:
    return bool(
        (analysis.extracted_document_number and analysis.extracted_document_number.strip())
        or (analysis.extracted_document_date and analysis.extracted_document_date.strip())
        or
        (analysis.extracted_lc_sc_number and analysis.extracted_lc_sc_number.strip())
        or (analysis.extracted_pi_number and analysis.extracted_pi_number.strip())
    )


def _analysis_needs_ud_ip_exp_completion(analysis: SavedDocumentAnalysis) -> bool:
    document_number = _optional_string(analysis.extracted_document_number)
    if document_number is None or _normalize_ud_ip_exp_document_number(document_number) is None:
        return False
    return not all(
        (
            _optional_string(analysis.extracted_lc_sc_number),
            _optional_string(analysis.extracted_document_date),
            _optional_string(analysis.extracted_quantity),
            _optional_string(analysis.extracted_quantity_unit),
        )
    )


def _merge_analysis(primary: SavedDocumentAnalysis, secondary: SavedDocumentAnalysis) -> SavedDocumentAnalysis:
    return SavedDocumentAnalysis(
        analysis_basis=_merge_analysis_basis(primary.analysis_basis, secondary.analysis_basis),
        extracted_lc_sc_number=primary.extracted_lc_sc_number or secondary.extracted_lc_sc_number,
        extracted_lc_sc_confidence=primary.extracted_lc_sc_confidence or secondary.extracted_lc_sc_confidence,
        extracted_pi_number=primary.extracted_pi_number or secondary.extracted_pi_number,
        extracted_pi_confidence=primary.extracted_pi_confidence or secondary.extracted_pi_confidence,
        extracted_document_number=primary.extracted_document_number or secondary.extracted_document_number,
        extracted_document_number_confidence=(
            primary.extracted_document_number_confidence or secondary.extracted_document_number_confidence
        ),
        extracted_document_date=primary.extracted_document_date or secondary.extracted_document_date,
        extracted_document_date_confidence=(
            primary.extracted_document_date_confidence or secondary.extracted_document_date_confidence
        ),
        extracted_quantity=primary.extracted_quantity or secondary.extracted_quantity,
        extracted_quantity_unit=primary.extracted_quantity_unit or secondary.extracted_quantity_unit,
        extracted_amendment_number=primary.extracted_amendment_number or secondary.extracted_amendment_number,
        clause_related_lc_sc_number=primary.clause_related_lc_sc_number or secondary.clause_related_lc_sc_number,
        clause_excerpt=primary.clause_excerpt or secondary.clause_excerpt,
        clause_confidence=primary.clause_confidence or secondary.clause_confidence,
        extracted_lc_sc_provenance=primary.extracted_lc_sc_provenance or secondary.extracted_lc_sc_provenance,
        extracted_pi_provenance=primary.extracted_pi_provenance or secondary.extracted_pi_provenance,
        extracted_document_number_provenance=(
            primary.extracted_document_number_provenance or secondary.extracted_document_number_provenance
        ),
        extracted_document_date_provenance=(
            primary.extracted_document_date_provenance or secondary.extracted_document_date_provenance
        ),
        extracted_quantity_provenance=(
            primary.extracted_quantity_provenance or secondary.extracted_quantity_provenance
        ),
        extracted_amendment_provenance=(
            primary.extracted_amendment_provenance or secondary.extracted_amendment_provenance
        ),
        clause_provenance=primary.clause_provenance or secondary.clause_provenance,
    )


def _merge_analysis_basis(primary_basis: str, secondary_basis: str) -> str:
    empty_bases = {
        "none",
        "missing_saved_document",
        "pdfplumber_table_empty",
        "pdfplumber_table_error",
        "img2table_table_empty",
        "img2table_table_error",
        "ocr_text_empty",
        "ocr_text_error",
        "pymupdf_text_empty",
        "pymupdf_text_error",
    }
    if secondary_basis in empty_bases:
        return primary_basis
    if primary_basis in empty_bases:
        return secondary_basis
    if primary_basis == secondary_basis:
        return primary_basis
    return f"{primary_basis}+{secondary_basis}"


def _normalize_ocr_confidence(value: object) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    return round(numeric / 100.0, 4)


def _field_confidence_from_tokens(
    tokens: list[str],
    confidences: list[float],
    target: str,
    normalizer,
) -> float | None:
    if not tokens or len(tokens) != len(confidences):
        return None
    normalized_target = normalizer(target)
    if normalized_target is None:
        return None
    best: float | None = None
    for start in range(len(tokens)):
        for end in range(start + 1, min(len(tokens), start + 6) + 1):
            candidate = " ".join(tokens[start:end])
            candidate_normalized = normalizer(candidate)
            if candidate_normalized != normalized_target:
                continue
            average_confidence = round(sum(confidences[start:end]) / (end - start), 4)
            if best is None or average_confidence > best:
                best = average_confidence
    return best


def _load_pymupdf_module():
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise ValueError("PyMuPDF is required for live saved-document analysis") from exc
    return fitz


def _load_pytesseract_module():
    try:
        import pytesseract  # type: ignore
    except ImportError as exc:
        raise ValueError("pytesseract is required for OCR saved-document analysis") from exc
    return pytesseract


def _load_pdfplumber_module():
    try:
        import pdfplumber  # type: ignore
    except ImportError as exc:
        raise ValueError("pdfplumber is required for table-aware saved-document analysis") from exc
    return pdfplumber


def _load_img2table_pdf_class():
    try:
        from img2table.document import PDF  # type: ignore
    except ImportError as exc:
        raise ValueError("img2table is required for scanned-table saved-document analysis") from exc
    return PDF


def _load_img2table_tesseract_ocr_class():
    try:
        from img2table.ocr import TesseractOCR  # type: ignore
    except ImportError as exc:
        raise ValueError("img2table OCR support is required for scanned-table saved-document analysis") from exc
    return TesseractOCR


def _load_pil_image_module():
    try:
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise ValueError("Pillow is required for OCR saved-document analysis") from exc
    return Image
