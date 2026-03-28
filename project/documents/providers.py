from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from project.models import SavedDocument


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
