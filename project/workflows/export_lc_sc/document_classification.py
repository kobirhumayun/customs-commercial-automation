from __future__ import annotations

import re
from dataclasses import dataclass, replace

from project.models import SavedDocument
from project.workflows.export_lc_sc.payloads import ExportMailPayload


PI_FILENAME_PATTERN = re.compile(r"(?i)\bPDL[-_ ]?\d{2}[-_ ]?\d{1,4}(?:[-_ ]?R\d+)?\b")
PI_TOKEN_PATTERN = re.compile(r"(?i)(?:^|[^A-Z0-9])PI(?:[^A-Z0-9]|$)")


@dataclass(slots=True, frozen=True)
class ClassifiedDocumentSet:
    saved_documents: list[SavedDocument]
    decision_reasons: list[str]


def classify_saved_export_documents(
    *,
    payload: ExportMailPayload,
    saved_documents: list[SavedDocument],
) -> ClassifiedDocumentSet:
    classified_documents: list[SavedDocument] = []
    decision_reasons: list[str] = []

    for document in saved_documents:
        classification = _classify_saved_document(payload=payload, document=document)
        classified_documents.append(
            replace(
                document,
                document_type=classification.document_type,
                classification_reason=classification.classification_reason,
                print_eligible=classification.print_eligible,
            )
        )
        decision_reasons.append(
            f"Classified saved document {document.normalized_filename} as {classification.document_type}."
        )

    return ClassifiedDocumentSet(
        saved_documents=classified_documents,
        decision_reasons=decision_reasons,
    )


@dataclass(slots=True, frozen=True)
class _ClassificationResult:
    document_type: str
    classification_reason: str
    print_eligible: bool


def _classify_saved_document(
    *,
    payload: ExportMailPayload,
    document: SavedDocument,
) -> _ClassificationResult:
    filename_upper = document.normalized_filename.upper()
    lc_score = _score_lc_sc_filename(filename_upper, payload)
    pi_score = _score_pi_filename(filename_upper)

    if lc_score > pi_score:
        return _ClassificationResult(
            document_type="export_lc_sc_document",
            classification_reason="Filename matches the export LC/SC naming convention.",
            print_eligible=True,
        )
    if pi_score > lc_score:
        return _ClassificationResult(
            document_type="export_pi_document",
            classification_reason="Filename matches the export PI naming convention.",
            print_eligible=True,
        )
    if lc_score == 0 and pi_score == 0:
        return _ClassificationResult(
            document_type="non_print_supporting_pdf",
            classification_reason="Filename does not match a deterministic LC/SC or PI print pattern.",
            print_eligible=False,
        )
    return _ClassificationResult(
        document_type="ambiguous_export_pdf",
        classification_reason="Filename matched multiple export print patterns equally and is excluded from print planning.",
        print_eligible=False,
    )


def _score_lc_sc_filename(filename_upper: str, payload: ExportMailPayload) -> int:
    score = 0
    canonical_candidates = {
        _compact_alnum(value)
        for value in (
            payload.verified_family.lc_sc_number if payload.verified_family is not None else None,
            payload.parsed_subject.lc_sc_number if payload.parsed_subject is not None else None,
        )
        if value
    }
    compact_filename = _compact_alnum(filename_upper)
    if any(candidate and candidate in compact_filename for candidate in canonical_candidates):
        score = max(score, 3)
    if filename_upper.startswith("LC") or filename_upper.startswith("SC"):
        score = max(score, 1)
    return score


def _score_pi_filename(filename_upper: str) -> int:
    if PI_FILENAME_PATTERN.search(filename_upper):
        return 3
    normalized = filename_upper.replace("_", " ").replace("-", " ")
    if PI_TOKEN_PATTERN.search(normalized):
        return 1
    return 0


def _compact_alnum(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())
