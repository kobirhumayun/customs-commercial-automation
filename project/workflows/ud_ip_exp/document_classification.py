from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
import re

from project.documents.providers import NullSavedDocumentAnalysisProvider, SavedDocumentAnalysisProvider
from project.erp.normalization import normalize_lc_sc_number
from project.models import FinalDecision, SavedDocument
from project.workflows.export_lc_sc.document_classification import DocumentClassificationDiscrepancy
from project.workflows.ud_ip_exp.parsing import (
    document_kind_from_number,
    is_bgmea_ud_am_document_number,
    normalize_ud_ip_exp_document_number,
)
from project.workflows.ud_ip_exp.payloads import (
    DocumentExtractionField,
    EXPDocumentPayload,
    IPDocumentPayload,
    UDDocumentPayload,
    UDIPEXPDocumentKind,
    UDIPEXPDocumentPayload,
    UDIPEXPQuantity,
)

_REQUIRED_DOCUMENT_NUMBER_CONFIDENCE = {
    UDIPEXPDocumentKind.UD: 0.97,
    UDIPEXPDocumentKind.IP: 0.97,
    UDIPEXPDocumentKind.EXP: 0.97,
}
_EXP_FILENAME_RE = re.compile(r"^\d+-EXP$", re.IGNORECASE)


@dataclass(slots=True, frozen=True)
class ClassifiedUDIPEXPDocumentSet:
    saved_documents: list[SavedDocument]
    documents: list[UDIPEXPDocumentPayload]
    decision_reasons: list[str]
    discrepancies: list[DocumentClassificationDiscrepancy]


def classify_saved_ud_ip_exp_documents(
    *,
    saved_documents: list[SavedDocument],
    analysis_provider: SavedDocumentAnalysisProvider | None = None,
) -> ClassifiedUDIPEXPDocumentSet:
    provider = analysis_provider or NullSavedDocumentAnalysisProvider()
    annotated_documents: list[SavedDocument] = []
    documents: list[UDIPEXPDocumentPayload] = []
    decision_reasons: list[str] = []
    discrepancies: list[DocumentClassificationDiscrepancy] = []

    for saved_document in saved_documents:
        filename_kind = document_kind_from_filename(saved_document.normalized_filename)
        if filename_kind is None:
            annotated_documents.append(
                replace(
                    saved_document,
                    document_type="supporting_pdf",
                    classification_reason=(
                        "Filename does not match UD/IP/EXP workflow naming conventions; document was skipped."
                    ),
                    print_eligible=False,
                )
            )
            decision_reasons.append(
                f"Skipped saved document {saved_document.normalized_filename} because its filename does not match UD/IP/EXP naming conventions."
            )
            continue

        analysis = provider.analyze(saved_document=saved_document)
        document_number, discrepancy = _classification_document_number(
            saved_document=saved_document,
            analysis=analysis,
            filename_kind=filename_kind,
        )
        if discrepancy is not None:
            discrepancies.append(discrepancy)
        document_kind = filename_kind
        annotated_document = replace(
            saved_document,
            analysis_basis=analysis.analysis_basis,
            extracted_lc_sc_number=analysis.extracted_lc_sc_number,
            extracted_lc_sc_confidence=analysis.extracted_lc_sc_confidence,
            extracted_pi_number=analysis.extracted_pi_number,
            extracted_pi_confidence=analysis.extracted_pi_confidence,
            extracted_document_number=document_number,
            extracted_document_number_confidence=analysis.extracted_document_number_confidence,
            extracted_document_date=analysis.extracted_document_date,
            extracted_document_date_confidence=analysis.extracted_document_date_confidence,
            extracted_document_subtype=analysis.extracted_document_subtype,
            extracted_lc_sc_date=analysis.extracted_lc_sc_date,
            extracted_lc_sc_value=analysis.extracted_lc_sc_value,
            extracted_lc_sc_value_currency=analysis.extracted_lc_sc_value_currency,
            extracted_quantity=analysis.extracted_quantity,
            extracted_quantity_unit=analysis.extracted_quantity_unit,
            extracted_quantity_by_unit=analysis.extracted_quantity_by_unit,
            extracted_amendment_number=analysis.extracted_amendment_number,
            clause_related_lc_sc_number=analysis.clause_related_lc_sc_number,
            clause_excerpt=analysis.clause_excerpt,
            clause_confidence=analysis.clause_confidence,
            extracted_lc_sc_provenance=analysis.extracted_lc_sc_provenance,
            extracted_pi_provenance=analysis.extracted_pi_provenance,
            extracted_document_number_provenance=analysis.extracted_document_number_provenance,
            extracted_document_date_provenance=analysis.extracted_document_date_provenance,
            extracted_lc_sc_date_provenance=analysis.extracted_lc_sc_date_provenance,
            extracted_lc_sc_value_provenance=analysis.extracted_lc_sc_value_provenance,
            extracted_quantity_provenance=analysis.extracted_quantity_provenance,
            extracted_amendment_provenance=analysis.extracted_amendment_provenance,
            clause_provenance=analysis.clause_provenance,
            document_type=_document_type(document_kind),
            classification_reason=_classification_reason(document_kind),
            print_eligible=saved_document.save_decision == "saved_new",
        )
        annotated_documents.append(annotated_document)
        decision_reasons.append(
            f"Classified saved document {annotated_document.normalized_filename} as {annotated_document.document_type}."
        )
        payload = _build_payload(annotated_document, document_kind=document_kind)
        if payload is not None:
            documents.append(payload)

    return ClassifiedUDIPEXPDocumentSet(
        saved_documents=annotated_documents,
        documents=documents,
        decision_reasons=decision_reasons,
            discrepancies=discrepancies,
        )


def _document_number_from_filename(saved_document: SavedDocument) -> str | None:
    filename_kind = document_kind_from_filename(saved_document.normalized_filename)
    if filename_kind is None:
        return None
    stem = Path(saved_document.normalized_filename).stem
    normalized = normalize_ud_ip_exp_document_number(stem)
    if normalized is not None:
        return normalized
    if filename_kind == UDIPEXPDocumentKind.EXP:
        return f"EXP-{stem.upper()}"
    return None


def is_processable_ud_ip_exp_filename(normalized_filename: str) -> bool:
    return document_kind_from_filename(normalized_filename) is not None


def document_kind_from_filename(normalized_filename: str) -> UDIPEXPDocumentKind | None:
    filename = Path(normalized_filename).name.strip()
    stem = Path(filename).stem.strip()
    upper_filename = filename.upper()
    if upper_filename.startswith("UD-"):
        return UDIPEXPDocumentKind.UD
    if upper_filename.startswith("IP-"):
        return UDIPEXPDocumentKind.IP
    if _EXP_FILENAME_RE.fullmatch(stem):
        return UDIPEXPDocumentKind.EXP
    return None


def _classification_document_number(
    *,
    saved_document: SavedDocument,
    analysis,
    filename_kind: UDIPEXPDocumentKind,
) -> tuple[str | None, DocumentClassificationDiscrepancy | None]:
    analyzed_number = analysis.extracted_document_number
    analyzed_kind = document_kind_from_number(analyzed_number) if analyzed_number else None
    if analyzed_kind == filename_kind and _document_number_confidence_is_acceptable(
        document_kind=analyzed_kind,
        confidence=analysis.extracted_document_number_confidence,
    ):
        if filename_kind != UDIPEXPDocumentKind.UD or is_bgmea_ud_am_document_number(analyzed_number):
            return analyzed_number, None
    if filename_kind == UDIPEXPDocumentKind.UD:
        return None, DocumentClassificationDiscrepancy(
            code="ud_document_number_pattern_mismatch",
            severity=FinalDecision.HARD_BLOCK,
            message=(
                "UD/AM workbook writes require an extracted BGMEA UD/AM document number; "
                "the attachment filename is not accepted as a fallback write value."
            ),
            details={
                "attachment_name": saved_document.attachment_name,
                "normalized_filename": saved_document.normalized_filename,
                "analysis_basis": analysis.analysis_basis,
                "extracted_document_number": analyzed_number,
                "expected_pattern": "BGMEA/<office>/<UD-or-AM>/...",
            },
        )
    return _document_number_from_filename(saved_document), None


def _document_number_confidence_is_acceptable(
    *,
    document_kind: UDIPEXPDocumentKind | None,
    confidence: float | None,
) -> bool:
    if document_kind is None:
        return False
    threshold = _REQUIRED_DOCUMENT_NUMBER_CONFIDENCE[document_kind]
    return confidence is None or confidence >= threshold


def _document_type(document_kind: UDIPEXPDocumentKind | None) -> str:
    if document_kind == UDIPEXPDocumentKind.UD:
        return "ud_document"
    if document_kind == UDIPEXPDocumentKind.EXP:
        return "exp_document"
    if document_kind == UDIPEXPDocumentKind.IP:
        return "ip_document"
    return "supporting_pdf"


def _classification_reason(document_kind: UDIPEXPDocumentKind | None) -> str:
    if document_kind == UDIPEXPDocumentKind.UD:
        return "Document evidence matches a UD identifier."
    if document_kind == UDIPEXPDocumentKind.EXP:
        return "Document evidence matches an EXP identifier."
    if document_kind == UDIPEXPDocumentKind.IP:
        return "Document evidence matches an IP identifier."
    return "Document evidence does not yet support deterministic UD/IP/EXP payload extraction."


def _build_payload(
    saved_document: SavedDocument,
    *,
    document_kind: UDIPEXPDocumentKind | None,
) -> UDIPEXPDocumentPayload | None:
    if document_kind is None or saved_document.extracted_document_number is None:
        return None

    payload_class = {
        UDIPEXPDocumentKind.UD: UDDocumentPayload,
        UDIPEXPDocumentKind.EXP: EXPDocumentPayload,
        UDIPEXPDocumentKind.IP: IPDocumentPayload,
    }[document_kind]
    quantity = None
    if saved_document.extracted_quantity and saved_document.extracted_quantity_unit:
        quantity = UDIPEXPQuantity(
            amount=Decimal(saved_document.extracted_quantity),
            unit=saved_document.extracted_quantity_unit,
        )
    quantity_by_unit = {
        key: Decimal(str(value))
        for key, value in (saved_document.extracted_quantity_by_unit or {}).items()
    }

    lc_sc_number = normalize_lc_sc_number(saved_document.extracted_lc_sc_number or "") or _lc_sc_number_from_document_number(
        saved_document.extracted_document_number
    )
    return payload_class(
        document_number=DocumentExtractionField(
            value=saved_document.extracted_document_number,
            confidence=saved_document.extracted_document_number_confidence,
            provenance=dict(saved_document.extracted_document_number_provenance or {}),
        ),
        document_date=(
            DocumentExtractionField(
                value=saved_document.extracted_document_date,
                confidence=saved_document.extracted_document_date_confidence,
                provenance=dict(saved_document.extracted_document_date_provenance or {}),
            )
            if saved_document.extracted_document_date
            else None
        ),
        lc_sc_number=DocumentExtractionField(
            value=lc_sc_number or "",
            confidence=saved_document.extracted_lc_sc_confidence,
            provenance=dict(saved_document.extracted_lc_sc_provenance or {}),
        ),
        lc_sc_date=(
            DocumentExtractionField(
                value=saved_document.extracted_lc_sc_date,
                provenance=dict(saved_document.extracted_lc_sc_date_provenance or {}),
            )
            if saved_document.extracted_lc_sc_date
            else None
        ),
        lc_sc_value=(
            DocumentExtractionField(
                value=saved_document.extracted_lc_sc_value,
                provenance=dict(saved_document.extracted_lc_sc_value_provenance or {}),
            )
            if saved_document.extracted_lc_sc_value
            else None
        ),
        lc_sc_value_currency=saved_document.extracted_lc_sc_value_currency,
        quantity=quantity,
        quantity_by_unit=quantity_by_unit,
        source_saved_document_id=saved_document.saved_document_id,
    )


def _lc_sc_number_from_document_number(document_number: str | None) -> str | None:
    if not document_number:
        return None
    segments = document_number.split("-")
    if len(segments) < 3:
        return None
    prefix = segments[1].strip().upper()
    if prefix not in {"LC", "SC"}:
        return None
    return normalize_lc_sc_number(f"{prefix}-{segments[2].strip()}")
