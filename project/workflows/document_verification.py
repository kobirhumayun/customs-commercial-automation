from __future__ import annotations

import getpass
import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project.documents import extract_saved_document_raw_report
from project.models import MailOutcomeRecord, MailProcessingStatus, RunReport, SavedDocument
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


@dataclass(slots=True, frozen=True)
class DocumentManualVerificationAcknowledgement:
    bundle_path: str
    acknowledged_document_count: int
    verified_document_count: int
    pending_document_count: int
    manual_verification_complete: bool
    payload: dict[str, Any]


def load_document_manual_verification_bundle(
    *,
    artifact_paths: RunArtifactPaths,
    allow_missing: bool = True,
) -> dict[str, Any] | None:
    try:
        return _load_manual_document_verification_payload(artifact_paths.manual_document_verification_path)
    except ValueError:
        if allow_missing:
            return None
        raise


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


def acknowledge_document_manual_verification(
    *,
    artifact_paths: RunArtifactPaths,
    saved_document_ids: list[str] | None = None,
    operator_notes: str | None = None,
) -> DocumentManualVerificationAcknowledgement:
    payload = _load_manual_document_verification_payload(artifact_paths.manual_document_verification_path)
    documents = payload.get("documents", [])
    if not isinstance(documents, list):
        raise ValueError("Manual document verification bundle must contain a documents array.")

    target_ids = {value.strip() for value in (saved_document_ids or []) if value.strip()}
    operator_identity = _current_operator_identity()
    acknowledged_document_count = 0
    for document in documents:
        if not isinstance(document, dict):
            continue
        saved_document = document.get("saved_document", {})
        if not isinstance(saved_document, dict):
            continue
        saved_document_id = str(saved_document.get("saved_document_id", "")).strip()
        if not saved_document_id:
            continue
        if target_ids and saved_document_id not in target_ids:
            continue
        if not target_ids and str(document.get("manual_verification_status", "")).strip() == "verified":
            continue
        document["manual_verification_status"] = "verified"
        document["verified_by"] = operator_identity["operator_id"]
        document["verified_host"] = operator_identity["host_name"]
        document["verified_process_id"] = operator_identity["process_id"]
        document["verified_at_utc"] = utc_timestamp()
        if operator_notes is not None:
            document["operator_notes"] = operator_notes
        acknowledged_document_count += 1

    if target_ids:
        found_ids = {
            str(document.get("saved_document", {}).get("saved_document_id", "")).strip()
            for document in documents
            if isinstance(document, dict)
            and isinstance(document.get("saved_document"), dict)
        }
        missing_ids = sorted(target_ids - found_ids)
        if missing_ids:
            raise ValueError(
                f"Saved document ids were not found in the manual verification bundle: {', '.join(missing_ids)}"
            )

    if acknowledged_document_count == 0:
        raise ValueError("No documents were updated in the manual verification bundle.")

    verified_document_count = sum(
        1
        for document in documents
        if isinstance(document, dict)
        and str(document.get("manual_verification_status", "")).strip() == "verified"
    )
    pending_document_count = sum(
        1
        for document in documents
        if isinstance(document, dict)
        and str(document.get("manual_verification_status", "")).strip() != "verified"
    )
    manual_verification_complete = pending_document_count == 0 and bool(documents)
    payload["documents"] = documents
    payload["verified_document_count"] = verified_document_count
    payload["pending_document_count"] = pending_document_count
    payload["manual_verification_complete"] = manual_verification_complete
    payload["last_acknowledged_at_utc"] = utc_timestamp()
    payload["last_acknowledged_by"] = operator_identity["operator_id"]
    if manual_verification_complete:
        payload["manual_verification_completed_at_utc"] = utc_timestamp()
    write_json(artifact_paths.manual_document_verification_path, payload)
    return DocumentManualVerificationAcknowledgement(
        bundle_path=str(artifact_paths.manual_document_verification_path),
        acknowledged_document_count=acknowledged_document_count,
        verified_document_count=verified_document_count,
        pending_document_count=pending_document_count,
        manual_verification_complete=manual_verification_complete,
        payload=payload,
    )


def summarize_manual_document_verification(
    *,
    run_report: RunReport,
    mail_outcomes: list[MailOutcomeRecord],
    artifact_paths: RunArtifactPaths,
) -> dict[str, Any]:
    bundle = load_document_manual_verification_bundle(
        artifact_paths=artifact_paths,
        allow_missing=True,
    )
    return {
        "run_id": run_report.run_id,
        "workflow_id": run_report.workflow_id.value,
        "write_phase_status": run_report.write_phase_status.value,
        "print_phase_status": run_report.print_phase_status.value,
        "mail_move_phase_status": run_report.mail_move_phase_status.value,
        "manual_verification_required": bool(
            isinstance(bundle, dict) and bundle.get("manual_verification_required", False)
        ),
        "bundle": _summarize_manual_verification_bundle(
            bundle=bundle,
            bundle_path=artifact_paths.manual_document_verification_path,
            audit_directory=artifact_paths.document_audits_dir,
        ),
        "mail_processing_status_counts": _mail_processing_status_counts(mail_outcomes),
        "phases": {
            "planning": _aggregate_mail_verification_summaries(
                outcome
                for outcome in mail_outcomes
                if str(outcome.print_group_id or "").strip()
            ),
            "printing": _aggregate_mail_verification_summaries(
                outcome
                for outcome in mail_outcomes
                if outcome.processing_status in {
                    MailProcessingStatus.PRINTED,
                    MailProcessingStatus.MOVED,
                }
            ),
            "mail_moves": _aggregate_mail_verification_summaries(
                outcome
                for outcome in mail_outcomes
                if outcome.processing_status == MailProcessingStatus.MOVED
            ),
        },
    }


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


def _load_manual_document_verification_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"Manual document verification bundle does not exist: {path}")
    raw_text = path.read_text(encoding="utf-8").strip()
    if not raw_text:
        raise ValueError(f"Manual document verification bundle is empty: {path}")
    payload = json.loads(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("Manual document verification bundle must be a JSON object.")
    return payload


def _current_operator_identity() -> dict[str, object]:
    username = getpass.getuser()
    return {
        "operator_id": username,
        "host_name": socket.gethostname(),
        "process_id": os.getpid(),
    }


def _summarize_manual_verification_bundle(
    *,
    bundle: dict[str, Any] | None,
    bundle_path: Path,
    audit_directory: Path,
) -> dict[str, Any]:
    if not isinstance(bundle, dict):
        return {
            "bundle_present": False,
            "bundle_path": str(bundle_path),
            "audit_directory": str(audit_directory),
            "generated_at_utc": None,
            "extraction_mode": None,
            "document_count": 0,
            "verified_document_count": 0,
            "pending_document_count": 0,
            "audit_ready_count": 0,
            "audit_error_count": 0,
            "manual_verification_complete": False,
            "last_acknowledged_at_utc": None,
            "last_acknowledged_by": None,
        }

    documents = bundle.get("documents", [])
    document_entries = documents if isinstance(documents, list) else []
    verified_document_count = sum(
        1
        for document in document_entries
        if isinstance(document, dict)
        and str(document.get("manual_verification_status", "")).strip() == "verified"
    )
    pending_document_count = sum(
        1
        for document in document_entries
        if isinstance(document, dict)
        and str(document.get("manual_verification_status", "")).strip() != "verified"
    )
    document_count = _coerce_int(bundle.get("document_count"), default=len(document_entries))
    audit_ready_count = _coerce_int(
        bundle.get("audit_ready_count"),
        default=sum(
            1
            for document in document_entries
            if isinstance(document, dict) and str(document.get("audit_status", "")).strip() == "ready"
        ),
    )
    audit_error_count = _coerce_int(
        bundle.get("audit_error_count"),
        default=sum(
            1
            for document in document_entries
            if isinstance(document, dict) and str(document.get("audit_status", "")).strip() == "error"
        ),
    )
    manual_verification_complete = bool(
        bundle.get(
            "manual_verification_complete",
            document_count > 0 and pending_document_count == 0,
        )
    )
    return {
        "bundle_present": True,
        "bundle_path": str(bundle_path),
        "audit_directory": str(bundle.get("audit_directory") or audit_directory),
        "generated_at_utc": bundle.get("generated_at_utc"),
        "extraction_mode": bundle.get("extraction_mode"),
        "document_count": document_count,
        "verified_document_count": verified_document_count,
        "pending_document_count": pending_document_count,
        "audit_ready_count": audit_ready_count,
        "audit_error_count": audit_error_count,
        "manual_verification_complete": manual_verification_complete,
        "last_acknowledged_at_utc": bundle.get("last_acknowledged_at_utc"),
        "last_acknowledged_by": bundle.get("last_acknowledged_by"),
    }


def _mail_processing_status_counts(mail_outcomes: list[MailOutcomeRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for outcome in mail_outcomes:
        status = outcome.processing_status.value
        counts[status] = counts.get(status, 0) + 1
    return counts


def _aggregate_mail_verification_summaries(
    outcomes: list[MailOutcomeRecord] | tuple[MailOutcomeRecord, ...] | Any,
) -> dict[str, int]:
    items = list(outcomes)
    summary_available_mail_count = 0
    document_count = 0
    verified_count = 0
    pending_count = 0
    untracked_count = 0
    for outcome in items:
        if not isinstance(outcome.manual_document_verification_summary, dict):
            continue
        summary_available_mail_count += 1
        summary = outcome.manual_document_verification_summary
        document_count += _coerce_int(summary.get("document_count"), default=0)
        verified_count += _coerce_int(summary.get("verified_count"), default=0)
        pending_count += _coerce_int(summary.get("pending_count"), default=0)
        untracked_count += _coerce_int(summary.get("untracked_count"), default=0)
    return {
        "mail_count": len(items),
        "summary_available_mail_count": summary_available_mail_count,
        "document_count": document_count,
        "verified_count": verified_count,
        "pending_count": pending_count,
        "untracked_count": untracked_count,
    }


def _coerce_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return default
