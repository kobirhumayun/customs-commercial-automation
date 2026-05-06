from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from project.models import SavedDocument

if TYPE_CHECKING:
    from project.workflows.export_lc_sc.payloads import ExportMailPayload
    from project.workflows.ud_ip_exp.matching import UDAllocationResult


class UDIPEXPDocumentKind(StrEnum):
    UD = "UD"
    IP = "IP"
    EXP = "EXP"


@dataclass(slots=True, frozen=True)
class DocumentExtractionField:
    value: str
    confidence: float | None = None
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class UDIPEXPQuantity:
    amount: Decimal
    unit: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", Decimal(str(self.amount)))
        object.__setattr__(self, "unit", normalize_quantity_unit(self.unit))


@dataclass(slots=True, frozen=True, kw_only=True)
class UDIPEXPDocumentPayload:
    document_kind: UDIPEXPDocumentKind
    document_number: DocumentExtractionField
    document_date: DocumentExtractionField | None
    lc_sc_number: DocumentExtractionField
    lc_sc_date: DocumentExtractionField | None = None
    lc_sc_value: DocumentExtractionField | None = None
    lc_sc_value_currency: str | None = None
    quantity: UDIPEXPQuantity | None = None
    quantity_by_unit: dict[str, Decimal] = field(default_factory=dict)
    source_saved_document_id: str | None = None
    field_confidence_thresholds: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True, frozen=True, kw_only=True)
class UDDocumentPayload(UDIPEXPDocumentPayload):
    document_kind: UDIPEXPDocumentKind = UDIPEXPDocumentKind.UD


@dataclass(slots=True, frozen=True, kw_only=True)
class IPDocumentPayload(UDIPEXPDocumentPayload):
    document_kind: UDIPEXPDocumentKind = UDIPEXPDocumentKind.IP


@dataclass(slots=True, frozen=True, kw_only=True)
class EXPDocumentPayload(UDIPEXPDocumentPayload):
    document_kind: UDIPEXPDocumentKind = UDIPEXPDocumentKind.EXP


@dataclass(slots=True, frozen=True)
class UDIPEXPWorkflowPayload:
    documents: list[UDIPEXPDocumentPayload]
    saved_documents: list[SavedDocument] = field(default_factory=list)
    ud_allocation_result: UDAllocationResult | None = None
    export_payload: ExportMailPayload | None = None


def normalize_quantity_unit(unit: str) -> str:
    normalized = unit.strip().upper()
    if normalized in {"YD", "YDS", "YRD", "YRDS", "YARD", "YARDS"}:
        return "YDS"
    if normalized in {"MTR", "MTRS", "METER", "METERS", "METRE", "METRES"}:
        return "MTR"
    return normalized


def format_shared_column_entry(document_kind: UDIPEXPDocumentKind, document_number: str) -> str:
    normalized_number = document_number.strip()
    if document_kind == UDIPEXPDocumentKind.UD:
        return normalized_number
    if document_kind == UDIPEXPDocumentKind.EXP:
        return f"EXP: {normalized_number}"
    if document_kind == UDIPEXPDocumentKind.IP:
        return f"IP: {normalized_number}"
    raise ValueError(f"Unsupported UD/IP/EXP document kind: {document_kind}")


def format_shared_column_values(documents: list[UDIPEXPDocumentPayload]) -> str:
    ordered_documents = sorted(
        documents,
        key=lambda document: (
            _SHARED_COLUMN_ORDER[document.document_kind],
            document.document_number.value.strip(),
        ),
    )
    return "\n".join(
        format_shared_column_entry(document.document_kind, document.document_number.value)
        for document in ordered_documents
    )


_SHARED_COLUMN_ORDER = {
    UDIPEXPDocumentKind.UD: 0,
    UDIPEXPDocumentKind.EXP: 1,
    UDIPEXPDocumentKind.IP: 2,
}
