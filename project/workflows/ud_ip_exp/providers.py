from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from project.models import EmailMessage
from project.workflows.ud_ip_exp.parsing import (
    document_kind_from_number,
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


class UDDocumentPayloadProvider(Protocol):
    def get_documents(self, mail: EmailMessage) -> list[UDIPEXPDocumentPayload]:
        """Return deterministic UD/IP/EXP payloads for a snapshotted mail."""

    def get_ud_document(self, mail: EmailMessage) -> UDDocumentPayload | None:
        """Return a deterministic UD payload for a snapshotted mail, if available."""


class MappingUDDocumentPayloadProvider:
    def __init__(self, payloads_by_key: dict[str, UDIPEXPDocumentPayload | list[UDIPEXPDocumentPayload]]) -> None:
        self._payloads_by_key = {
            key: list(value) if isinstance(value, list) else [value]
            for key, value in payloads_by_key.items()
        }

    def get_documents(self, mail: EmailMessage) -> list[UDIPEXPDocumentPayload]:
        return list(self._payloads_by_key.get(mail.mail_id) or self._payloads_by_key.get(mail.entry_id) or [])

    def get_ud_document(self, mail: EmailMessage) -> UDDocumentPayload | None:
        return _first_ud_document(self.get_documents(mail))


class JsonManifestUDDocumentPayloadProvider:
    def __init__(self, manifest_path: Path) -> None:
        self._payloads_by_key = _load_payloads_by_key(manifest_path)

    def get_documents(self, mail: EmailMessage) -> list[UDIPEXPDocumentPayload]:
        return list(self._payloads_by_key.get(mail.mail_id) or self._payloads_by_key.get(mail.entry_id) or [])

    def get_ud_document(self, mail: EmailMessage) -> UDDocumentPayload | None:
        return _first_ud_document(self.get_documents(mail))


def _load_payloads_by_key(manifest_path: Path) -> dict[str, list[UDIPEXPDocumentPayload]]:
    content = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(content, list):
        raise ValueError("UD/IP/EXP payload manifest must be a list of records.")

    payloads_by_key: dict[str, list[UDIPEXPDocumentPayload]] = {}
    for index, item in enumerate(content):
        if not isinstance(item, dict):
            raise ValueError(f"UD/IP/EXP payload manifest item {index} must be an object.")
        keys = [
            str(item[key]).strip()
            for key in ("mail_id", "entry_id")
            if item.get(key) is not None and str(item[key]).strip()
        ]
        if not keys:
            raise ValueError(f"UD/IP/EXP payload manifest item {index} must include mail_id or entry_id.")
        payload = _payload_from_manifest_item(item, index)
        for key in keys:
            payloads_by_key.setdefault(key, []).append(payload)
    return payloads_by_key


def _payload_from_manifest_item(item: dict, index: int) -> UDIPEXPDocumentPayload:
    missing = [
        key
        for key in ("document_number", "document_date", "lc_sc_number")
        if item.get(key) is None
    ]
    if missing:
        raise ValueError(f"UD/IP/EXP payload manifest item {index} is missing fields: {missing}.")
    quantity = None
    if item.get("quantity") is not None:
        quantity = UDIPEXPQuantity(
            amount=Decimal(str(item["quantity"])),
            unit=str(item.get("quantity_unit", "YDS")),
        )
    document_number = _canonical_manifest_document_number(
        value=item["document_number"],
        document_kind=_manifest_document_kind(item.get("document_kind", "UD"), index),
        index=index,
    )
    document_kind = document_kind_from_number(document_number)
    if document_kind is None:
        raise ValueError(
            f"UD/IP/EXP payload manifest item {index} has unsupported document_number prefix."
        )
    payload_class = {
        UDIPEXPDocumentKind.UD: UDDocumentPayload,
        UDIPEXPDocumentKind.EXP: EXPDocumentPayload,
        UDIPEXPDocumentKind.IP: IPDocumentPayload,
    }[document_kind]
    return payload_class(
        document_number=DocumentExtractionField(
            value=document_number,
            confidence=_optional_float(item.get("document_number_confidence")),
            provenance=_optional_dict(item.get("document_number_provenance")),
        ),
        document_date=DocumentExtractionField(
            value=str(item["document_date"]),
            confidence=_optional_float(item.get("document_date_confidence")),
            provenance=_optional_dict(item.get("document_date_provenance")),
        ),
        lc_sc_number=DocumentExtractionField(
            value=str(item["lc_sc_number"]),
            confidence=_optional_float(item.get("lc_sc_number_confidence")),
            provenance=_optional_dict(item.get("lc_sc_number_provenance")),
        ),
        quantity=quantity,
        source_saved_document_id=(
            str(item["source_saved_document_id"])
            if item.get("source_saved_document_id") is not None
            else None
        ),
    )


def _manifest_document_kind(value, index: int) -> UDIPEXPDocumentKind:
    normalized = str(value).strip().upper()
    try:
        return UDIPEXPDocumentKind(normalized)
    except ValueError as exc:
        raise ValueError(
            f"UD/IP/EXP payload manifest item {index} has unsupported document_kind: {value!r}."
        ) from exc


def _canonical_manifest_document_number(
    *,
    value,
    document_kind: UDIPEXPDocumentKind,
    index: int,
) -> str:
    canonical = normalize_ud_ip_exp_document_number(str(value))
    if canonical is None:
        raise ValueError(
            f"UD/IP/EXP payload manifest item {index} has invalid document_number: {value!r}."
        )
    canonical_kind = document_kind_from_number(canonical)
    if canonical_kind != document_kind:
        raise ValueError(
            f"UD/IP/EXP payload manifest item {index} document_kind {document_kind.value!r} "
            f"does not match document_number {canonical!r}."
        )
    return canonical


def _first_ud_document(documents: list[UDIPEXPDocumentPayload]) -> UDDocumentPayload | None:
    return next((document for document in documents if isinstance(document, UDDocumentPayload)), None)


def _optional_float(value) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_dict(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("UD/IP/EXP provenance fields must be objects when supplied.")
    return dict(value)
