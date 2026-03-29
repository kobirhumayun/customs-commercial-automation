from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from project.documents import extract_saved_document_raw_report
from project.models import MailOutcomeRecord, RunReport, SavedDocument
from project.storage import RunArtifactPaths, write_json
from project.utils.json import to_jsonable
from project.utils.time import utc_timestamp


@dataclass(slots=True, frozen=True)
class DocumentManualVerificationResult:
    bundle_path: str
    audit_directory: str
    document_count: int
    audit_ready_count: int
    audit_error_count: int
    payload: dict[str, Any]


def build_document_manual_verification_bundle(
    *,
    run_report: RunReport,
    mail_outcomes: list[MailOutcomeRecord],
    artifact_paths: RunArtifactPaths,
    extraction_mode: str = "layered",
) -> DocumentManualVerificationResult:
    artifact_paths.document_audits_dir.mkdir(parents=True, exist_ok=True)
    documents: list[dict[str, Any]] = []
    audit_ready_count = 0
    audit_error_count = 0

    for outcome in mail_outcomes:
        for saved_document_payload in outcome.saved_documents:
            saved_document = _saved_document_from_payload(saved_document_payload, mail_id=outcome.mail_id)
            audit_path = artifact_paths.document_audits_dir / f"{saved_document.saved_document_id}.{extraction_mode}.json"
            document_entry = {
                "mail_id": outcome.mail_id,
                "subject_raw": outcome.subject_raw,
                "sender_address": outcome.sender_address,
                "final_decision": outcome.final_decision.value if outcome.final_decision is not None else None,
                "saved_document": to_jsonable(saved_document),
                "manual_verification_status": "pending",
                "verified_by": None,
                "verified_at_utc": None,
                "operator_notes": "",
                "verification_scope": [
                    "confirm_document_identity_manually",
                    "confirm_pdf_derived_values_manually",
                ],
                "audit_report_path": str(audit_path),
            }
            try:
                audit_report = extract_saved_document_raw_report(
                    saved_document=saved_document,
                    mode=extraction_mode,
                )
                write_json(audit_path, audit_report)
                document_entry["audit_status"] = "ready"
                audit_ready_count += 1
            except ValueError as exc:
                document_entry["audit_status"] = "error"
                document_entry["audit_error"] = str(exc)
                audit_error_count += 1
            documents.append(document_entry)

    payload = {
        "run_id": run_report.run_id,
        "workflow_id": run_report.workflow_id.value,
        "generated_at_utc": utc_timestamp(),
        "extraction_mode": extraction_mode,
        "manual_verification_required": True,
        "document_count": len(documents),
        "audit_ready_count": audit_ready_count,
        "audit_error_count": audit_error_count,
        "audit_directory": str(artifact_paths.document_audits_dir),
        "documents": documents,
    }
    write_json(artifact_paths.manual_document_verification_path, payload)
    return DocumentManualVerificationResult(
        bundle_path=str(artifact_paths.manual_document_verification_path),
        audit_directory=str(artifact_paths.document_audits_dir),
        document_count=len(documents),
        audit_ready_count=audit_ready_count,
        audit_error_count=audit_error_count,
        payload=payload,
    )


def _saved_document_from_payload(payload: dict[str, Any], *, mail_id: str) -> SavedDocument:
    return SavedDocument(
        saved_document_id=str(payload["saved_document_id"]),
        mail_id=str(payload.get("mail_id", mail_id)),
        attachment_name=str(payload["attachment_name"]),
        normalized_filename=str(payload["normalized_filename"]),
        destination_path=str(payload["destination_path"]),
        file_sha256=str(payload.get("file_sha256", "")),
        save_decision=str(payload["save_decision"]),
        attachment_index=_optional_int(payload.get("attachment_index")),
        document_type=_optional_str(payload.get("document_type")),
        classification_reason=_optional_str(payload.get("classification_reason")),
        print_eligible=bool(payload.get("print_eligible", False)),
        analysis_basis=_optional_str(payload.get("analysis_basis")),
        extracted_lc_sc_number=_optional_str(payload.get("extracted_lc_sc_number")),
        extracted_lc_sc_confidence=_optional_float(payload.get("extracted_lc_sc_confidence")),
        extracted_pi_number=_optional_str(payload.get("extracted_pi_number")),
        extracted_pi_confidence=_optional_float(payload.get("extracted_pi_confidence")),
        extracted_amendment_number=_optional_str(payload.get("extracted_amendment_number")),
        clause_related_lc_sc_number=_optional_str(payload.get("clause_related_lc_sc_number")),
        clause_excerpt=_optional_str(payload.get("clause_excerpt")),
        clause_confidence=_optional_float(payload.get("clause_confidence")),
        extracted_lc_sc_provenance=_optional_dict(payload.get("extracted_lc_sc_provenance")),
        extracted_pi_provenance=_optional_dict(payload.get("extracted_pi_provenance")),
        extracted_amendment_provenance=_optional_dict(payload.get("extracted_amendment_provenance")),
        clause_provenance=_optional_dict(payload.get("clause_provenance")),
    )


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _optional_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _optional_dict(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    return None
