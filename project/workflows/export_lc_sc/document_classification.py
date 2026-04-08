from __future__ import annotations

import re
from dataclasses import dataclass, replace

from project.documents import NullSavedDocumentAnalysisProvider, SavedDocumentAnalysisProvider
from project.models import FinalDecision, SavedDocument
from project.workflows.export_lc_sc.payloads import ExportMailPayload


PI_FILENAME_PATTERN = re.compile(r"(?i)\bPDL[-_ ]?\d{2}[-_ ]?\d{1,4}(?:[-_ ]?R\d+)?\b")
PI_TOKEN_PATTERN = re.compile(r"(?i)(?:^|[^A-Z0-9])PI(?:[^A-Z0-9]|$)")
EXPORT_LC_SC_OCR_MIN_CONFIDENCE = 0.98
EXPORT_PI_OCR_MIN_CONFIDENCE = 0.95


@dataclass(slots=True, frozen=True)
class DocumentClassificationDiscrepancy:
    code: str
    severity: FinalDecision
    message: str
    details: dict[str, object]


@dataclass(slots=True, frozen=True)
class ClassifiedDocumentSet:
    saved_documents: list[SavedDocument]
    decision_reasons: list[str]
    discrepancies: list[DocumentClassificationDiscrepancy]


@dataclass(slots=True, frozen=True)
class _ClassificationResult:
    document_type: str
    classification_reason: str


@dataclass(slots=True, frozen=True)
class _RankedCandidate:
    saved_document: SavedDocument
    filename_score: int
    amendment_match_score: int
    clause_confidence: float
    attachment_index: int
    lexical_name: str


def classify_saved_export_documents(
    *,
    payload: ExportMailPayload,
    saved_documents: list[SavedDocument],
    analysis_provider: SavedDocumentAnalysisProvider | None = None,
) -> ClassifiedDocumentSet:
    provider = analysis_provider or NullSavedDocumentAnalysisProvider()
    annotated_documents = [
        _annotate_saved_document(saved_document=document, analysis_provider=provider)
        for document in saved_documents
    ]

    classified_documents = [
        replace(
            document,
            document_type=classification.document_type,
            classification_reason=classification.classification_reason,
            print_eligible=False,
        )
        for document, classification in (
            (document, _classify_saved_document(payload=payload, document=document))
            for document in annotated_documents
        )
    ]

    decision_reasons: list[str] = []

    finalized_documents = [
        replace(
            document,
            print_eligible=document.save_decision == "saved_new",
            classification_reason=_final_classification_reason(document=document),
        )
        for document in classified_documents
    ]

    for document in finalized_documents:
        decision_reasons.append(
            f"Classified saved document {document.normalized_filename} as {document.document_type}."
        )

    return ClassifiedDocumentSet(
        saved_documents=finalized_documents,
        decision_reasons=decision_reasons,
        discrepancies=[],
    )


def _annotate_saved_document(
    *,
    saved_document: SavedDocument,
    analysis_provider: SavedDocumentAnalysisProvider,
) -> SavedDocument:
    analysis = analysis_provider.analyze(saved_document=saved_document)
    return replace(
        saved_document,
        analysis_basis=analysis.analysis_basis,
        extracted_lc_sc_number=analysis.extracted_lc_sc_number,
        extracted_lc_sc_confidence=analysis.extracted_lc_sc_confidence,
        extracted_pi_number=analysis.extracted_pi_number,
        extracted_pi_confidence=analysis.extracted_pi_confidence,
        extracted_amendment_number=analysis.extracted_amendment_number,
        clause_related_lc_sc_number=analysis.clause_related_lc_sc_number,
        clause_excerpt=analysis.clause_excerpt,
        clause_confidence=analysis.clause_confidence,
        extracted_lc_sc_provenance=analysis.extracted_lc_sc_provenance,
        extracted_pi_provenance=analysis.extracted_pi_provenance,
        extracted_amendment_provenance=analysis.extracted_amendment_provenance,
        clause_provenance=analysis.clause_provenance,
    )


def _classify_saved_document(
    *,
    payload: ExportMailPayload,
    document: SavedDocument,
) -> _ClassificationResult:
    lc_score = _score_lc_candidate(document=document, payload=payload)
    pi_score = _score_pi_candidate(document=document)

    if lc_score > pi_score:
        return _ClassificationResult(
            document_type="export_lc_sc_document",
            classification_reason="Document evidence matches the export LC/SC class.",
        )
    if pi_score > lc_score:
        return _ClassificationResult(
            document_type="export_pi_document",
            classification_reason="Document evidence matches the export PI class.",
        )
    if lc_score == 0 and pi_score == 0:
        return _ClassificationResult(
            document_type="non_print_supporting_pdf",
            classification_reason="Document evidence does not match a deterministic LC/SC or PI class.",
        )
    return _ClassificationResult(
        document_type="ambiguous_export_pdf",
        classification_reason="Document evidence matched multiple export print classes equally.",
    )


def _rank_candidates(
    *,
    payload: ExportMailPayload,
    saved_documents: list[SavedDocument],
    document_class: str,
) -> list[_RankedCandidate]:
    ranked: list[_RankedCandidate] = []
    for document in saved_documents:
        if document.document_type == "ambiguous_export_pdf":
            continue
        if document_class == "lc_sc":
            filename_score = _score_lc_candidate(document=document, payload=payload)
        else:
            filename_score = _score_pi_candidate(document=document)
        if filename_score <= 0:
            continue
        ranked.append(
            _RankedCandidate(
                saved_document=document,
                filename_score=filename_score,
                amendment_match_score=_amendment_match_score(document=document, payload=payload),
                clause_confidence=_clause_confidence_for_family_match(document=document, payload=payload),
                attachment_index=document.attachment_index if document.attachment_index is not None else 10**6,
                lexical_name=document.normalized_filename,
            )
        )
    ranked.sort(
        key=lambda item: (
            -item.filename_score,
            -item.amendment_match_score,
            -item.clause_confidence,
            item.attachment_index,
            item.lexical_name,
        )
    )
    return ranked


def _select_best_candidate(
    candidates: list[_RankedCandidate],
    *,
    target_document_type: str,
) -> tuple[SavedDocument | None, DocumentClassificationDiscrepancy | None]:
    if not candidates:
        return None, None
    if len(candidates) > 1 and _candidate_tie_key(candidates[0]) == _candidate_tie_key(candidates[1]):
        return None, DocumentClassificationDiscrepancy(
            code="attachment_classification_ambiguous",
            severity=FinalDecision.HARD_BLOCK,
            message="Multiple saved PDFs remained tied for one required export document class.",
            details={
                "target_document_type": target_document_type,
                "candidate_filenames": [candidates[0].saved_document.normalized_filename, candidates[1].saved_document.normalized_filename],
            },
        )
    return candidates[0].saved_document, None


def _candidate_tie_key(candidate: _RankedCandidate) -> tuple[int, int, float, int, str]:
    return (
        candidate.filename_score,
        candidate.amendment_match_score,
        candidate.clause_confidence,
        candidate.attachment_index,
        candidate.lexical_name,
    )


def _score_lc_candidate(*, document: SavedDocument, payload: ExportMailPayload) -> int:
    score = _score_lc_sc_filename(document.normalized_filename.upper(), payload)
    if document.extracted_lc_sc_number and _is_family_lc_sc_match(document.extracted_lc_sc_number, payload):
        score = max(score, 5)
    return score


def _score_pi_candidate(*, document: SavedDocument) -> int:
    score = _score_pi_filename(document.normalized_filename.upper())
    if document.extracted_pi_number:
        score = max(score, 5)
    return score


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


def _clause_confidence_for_family_match(*, document: SavedDocument, payload: ExportMailPayload) -> float:
    if document.clause_related_lc_sc_number and _is_family_lc_sc_match(document.clause_related_lc_sc_number, payload):
        return float(document.clause_confidence or 0.0)
    return 0.0


def _amendment_match_score(*, document: SavedDocument, payload: ExportMailPayload) -> int:
    subject_amendment = _subject_amendment_number(payload)
    if subject_amendment is None:
        return 0
    if document.extracted_amendment_number == subject_amendment:
        return 1
    return 0


def _subject_amendment_number(payload: ExportMailPayload) -> str | None:
    if payload.parsed_subject is None:
        return None
    tokens = [token.strip().upper() for token in payload.parsed_subject.suffix_tokens if token.strip()]
    for index, token in enumerate(tokens):
        if token in {"AMD", "AMND", "AMENDMENT"} and index + 1 < len(tokens):
            candidate = tokens[index + 1]
            if candidate.isdigit():
                return str(int(candidate))
        match = re.fullmatch(r"(?:AMD|AMND|AMENDMENT)[- ]?0*(\d{1,3})", token)
        if match is not None:
            return str(int(match.group(1)))
    return None


def _is_family_lc_sc_match(candidate_lc_sc_number: str, payload: ExportMailPayload) -> bool:
    compact_candidate = _compact_alnum(candidate_lc_sc_number)
    family_numbers = {
        _compact_alnum(value)
        for value in (
            payload.verified_family.lc_sc_number if payload.verified_family is not None else None,
            payload.parsed_subject.lc_sc_number if payload.parsed_subject is not None else None,
        )
        if value
    }
    return compact_candidate in family_numbers


def _final_classification_reason(
    *,
    document: SavedDocument,
) -> str:
    base_reason = document.classification_reason or "Document classification completed."
    return f"{base_reason} Saved PDF remains eligible for export print planning."


def _compact_alnum(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())
