from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from project.models import SavedDocument
from project.erp.normalization import normalize_lc_sc_number


LC_SC_CANDIDATE_PATTERN = re.compile(r"(?i)\b(?:LC|SC)\s*[- ]\s*[A-Z0-9]+(?:\s*-\s*[A-Z0-9]+){0,8}\b")
PI_CANDIDATE_PATTERN = re.compile(r"(?i)\bPDL\s*[- ]*\s*\d{2}\s*[- ]*\s*\d{1,4}(?:\s*[- ]*\s*R\d+)?\b")


@dataclass(slots=True, frozen=True)
class SavedDocumentAnalysis:
    analysis_basis: str
    extracted_lc_sc_number: str | None = None
    extracted_pi_number: str | None = None
    clause_related_lc_sc_number: str | None = None
    clause_excerpt: str | None = None
    clause_confidence: float | None = None


class SavedDocumentAnalysisProvider(Protocol):
    def analyze(self, *, saved_document: SavedDocument) -> SavedDocumentAnalysis:
        """Return deterministic analysis metadata for a saved document."""


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
        return SavedDocumentAnalysis(
            analysis_basis="json_manifest",
            extracted_lc_sc_number=_optional_string(match.get("extracted_lc_sc_number")),
            extracted_pi_number=_optional_string(match.get("extracted_pi_number")),
            clause_related_lc_sc_number=_optional_string(match.get("clause_related_lc_sc_number")),
            clause_excerpt=_optional_string(match.get("clause_excerpt")),
            clause_confidence=_optional_float(match.get("clause_confidence")),
        )


@dataclass(slots=True)
class PyMuPDFSavedDocumentAnalysisProvider:
    _fitz_module: object | None = field(default=None, init=False, repr=False)

    def analyze(self, *, saved_document: SavedDocument) -> SavedDocumentAnalysis:
        document_path = Path(saved_document.destination_path)
        if not document_path.exists():
            return SavedDocumentAnalysis(analysis_basis="missing_saved_document")

        try:
            extracted_text = _extract_pdf_text(document_path, self._get_fitz_module())
        except Exception:
            return SavedDocumentAnalysis(analysis_basis="pymupdf_text_error")

        if not extracted_text.strip():
            return SavedDocumentAnalysis(analysis_basis="pymupdf_text_empty")

        lc_sc_match = _first_lc_sc_match(extracted_text)
        pi_match = _first_pi_match(extracted_text)
        excerpt_seed = lc_sc_match[1] if lc_sc_match is not None else pi_match[1] if pi_match is not None else None
        return SavedDocumentAnalysis(
            analysis_basis="pymupdf_text",
            extracted_lc_sc_number=lc_sc_match[0] if lc_sc_match is not None else None,
            extracted_pi_number=pi_match[0] if pi_match is not None else None,
            clause_related_lc_sc_number=lc_sc_match[0] if lc_sc_match is not None else None,
            clause_excerpt=_build_clause_excerpt(extracted_text, excerpt_seed),
            clause_confidence=1.0 if excerpt_seed is not None else None,
        )

    def _get_fitz_module(self):
        if self._fitz_module is None:
            self._fitz_module = _load_pymupdf_module()
        return self._fitz_module


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


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raise ValueError("Saved-document analysis manifest clause_confidence must be numeric when present.")


def _extract_pdf_text(path: Path, fitz_module: object) -> str:
    document = fitz_module.open(str(path))
    try:
        return "\n".join(_extract_page_text(page) for page in document)
    finally:
        close = getattr(document, "close", None)
        if callable(close):
            close()


def _extract_page_text(page: object) -> str:
    get_text = getattr(page, "get_text", None)
    if not callable(get_text):
        return ""
    return str(get_text("text") or "")


def _first_lc_sc_match(text: str) -> tuple[str, int] | None:
    seen: set[str] = set()
    for match in LC_SC_CANDIDATE_PATTERN.finditer(text):
        normalized = normalize_lc_sc_number(match.group(0))
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        return normalized, match.start()
    return None


def _first_pi_match(text: str) -> tuple[str, int] | None:
    seen: set[str] = set()
    for match in PI_CANDIDATE_PATTERN.finditer(text):
        normalized = _normalize_pi_number(match.group(0))
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        return normalized, match.start()
    return None


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


def _build_clause_excerpt(text: str, seed_index: int | None) -> str | None:
    if seed_index is None:
        return None
    start = max(0, seed_index - 80)
    end = min(len(text), seed_index + 80)
    excerpt = " ".join(text[start:end].split())
    return excerpt or None


def _load_pymupdf_module():
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise ValueError("PyMuPDF is required for live saved-document analysis") from exc
    return fitz
