from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project.exceptions import ArtifactError
from project.models import (
    DiscrepancyReport,
    FinalDecision,
    MailMovePhaseStatus,
    PrintPhaseStatus,
    WorkflowId,
    WorkbookTargetProbe,
    WriteOperation,
    WritePhaseStatus,
)
from project.utils.hashing import HASH_ALGORITHM, HEX_DIGEST_LENGTH, canonical_json_hash, sha256_file
from project.utils.time import utc_timestamp
from project.workbook import EXPORT_HEADER_SPECS, WorkbookSnapshot, resolve_header_mapping


@dataclass(slots=True, frozen=True)
class RecoveryAssessment:
    run_id: str
    workflow_id: WorkflowId
    outcome: str
    current_workbook_hash: str | None
    backup_hash: str | None
    staged_write_plan_hash: str | None
    target_probes: list[WorkbookTargetProbe]
    discrepancies: list[DiscrepancyReport]
    details: dict[str, Any]


def assess_recovery(
    *,
    workflow_id: WorkflowId,
    run_artifact_root: Path,
    backup_root: Path,
    run_id: str,
    workbook_snapshot: WorkbookSnapshot,
    current_workbook_path: Path,
) -> RecoveryAssessment:
    created_at_utc = utc_timestamp()
    run_dir = run_artifact_root / workflow_id.value / run_id
    backup_dir = backup_root / workflow_id.value / run_id
    try:
        metadata = _load_json(run_dir / "run_metadata.json")
        staged_write_plan_payload = _load_json(run_dir / "staged_write_plan.json")
    except ArtifactError as exc:
        return RecoveryAssessment(
            run_id=run_id,
            workflow_id=workflow_id,
            outcome="hard_block",
            current_workbook_hash=(sha256_file(current_workbook_path) if current_workbook_path.exists() else None),
            backup_hash=None,
            staged_write_plan_hash=None,
            target_probes=[],
            discrepancies=[
                _build_discrepancy(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    code="missing_recovery_artifact",
                    message="Required recovery artifacts are missing or unreadable.",
                    created_at_utc=created_at_utc,
                    details={"error": str(exc)},
                )
            ],
            details={"reason": "artifact_validation_failed"},
        )
    if not isinstance(metadata, dict) or not isinstance(staged_write_plan_payload, list):
        return RecoveryAssessment(
            run_id=run_id,
            workflow_id=workflow_id,
            outcome="hard_block",
            current_workbook_hash=(sha256_file(current_workbook_path) if current_workbook_path.exists() else None),
            backup_hash=None,
            staged_write_plan_hash=None,
            target_probes=[],
            discrepancies=[
                _build_discrepancy(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    code="missing_recovery_artifact",
                    message="Recovery artifacts are malformed.",
                    created_at_utc=created_at_utc,
                    details={},
                )
            ],
            details={"reason": "artifact_validation_failed"},
        )

    backup_workbook_path = backup_dir / "master_workbook_backup.xlsx"
    backup_hash_path = backup_dir / "backup_hash.txt"
    discrepancies: list[DiscrepancyReport] = []
    target_probes: list[WorkbookTargetProbe] = []

    backup_hash = _validate_backup_artifacts(
        workflow_id=workflow_id,
        run_id=run_id,
        metadata=metadata,
        backup_workbook_path=backup_workbook_path,
        backup_hash_path=backup_hash_path,
        created_at_utc=created_at_utc,
        discrepancies=discrepancies,
    )
    staged_write_plan, staged_write_plan_hash = _validate_staged_write_plan(
        workflow_id=workflow_id,
        run_id=run_id,
        metadata=metadata,
        staged_write_plan_payload=staged_write_plan_payload,
        created_at_utc=created_at_utc,
        discrepancies=discrepancies,
    )
    current_workbook_hash = (
        sha256_file(current_workbook_path) if current_workbook_path.exists() else None
    )

    if discrepancies:
        return RecoveryAssessment(
            run_id=run_id,
            workflow_id=workflow_id,
            outcome="hard_block",
            current_workbook_hash=current_workbook_hash,
            backup_hash=backup_hash,
            staged_write_plan_hash=staged_write_plan_hash,
            target_probes=[],
            discrepancies=discrepancies,
            details={"reason": "artifact_validation_failed"},
        )

    probe_result = _probe_staged_write_plan(
        workflow_id=workflow_id,
        run_id=run_id,
        workbook_snapshot=workbook_snapshot,
        staged_write_plan=staged_write_plan,
    )
    target_probes = probe_result["target_probes"]
    if probe_result["discrepancy"] is not None:
        discrepancies.append(probe_result["discrepancy"])
        return RecoveryAssessment(
            run_id=run_id,
            workflow_id=workflow_id,
            outcome="hard_block",
            current_workbook_hash=current_workbook_hash,
            backup_hash=backup_hash,
            staged_write_plan_hash=staged_write_plan_hash,
            target_probes=target_probes,
            discrepancies=discrepancies,
            details={"reason": "probe_failed"},
        )

    unique_classifications = {probe.classification for probe in target_probes}
    write_phase_status = str(metadata.get("write_phase_status", ""))
    if "mismatch_unknown" in unique_classifications:
        discrepancies.append(
            _build_discrepancy(
                workflow_id=workflow_id,
                run_id=run_id,
                code="workbook_probe_unknown_state",
                message="Recovery probe produced an unknown target state.",
                created_at_utc=created_at_utc,
                details={"probe_summary": _summarize_probe_classifications(target_probes)},
            )
        )
        return RecoveryAssessment(
            run_id=run_id,
            workflow_id=workflow_id,
            outcome="hard_block",
            current_workbook_hash=current_workbook_hash,
            backup_hash=backup_hash,
            staged_write_plan_hash=staged_write_plan_hash,
            target_probes=target_probes,
            discrepancies=discrepancies,
            details={"reason": "unknown_probe_state"},
        )
    if unique_classifications == {"matches_pre_write"} or not target_probes:
        if write_phase_status in {
            WritePhaseStatus.NOT_STARTED.value,
            WritePhaseStatus.UNCERTAIN_NOT_COMMITTED.value,
        }:
            return RecoveryAssessment(
                run_id=run_id,
                workflow_id=workflow_id,
                outcome="safe_reapply_staged_writes",
                current_workbook_hash=current_workbook_hash,
                backup_hash=backup_hash,
                staged_write_plan_hash=staged_write_plan_hash,
                target_probes=target_probes,
                discrepancies=[],
                details={"probe_summary": _summarize_probe_classifications(target_probes)},
            )
        discrepancies.append(
            _build_discrepancy(
                workflow_id=workflow_id,
                run_id=run_id,
                code="metadata_probe_contradiction",
                message="Recovery probe indicates pre-write targets, but persisted write phase metadata indicates otherwise.",
                created_at_utc=created_at_utc,
                details={"write_phase_status": write_phase_status},
            )
        )
        return RecoveryAssessment(
            run_id=run_id,
            workflow_id=workflow_id,
            outcome="hard_block",
            current_workbook_hash=current_workbook_hash,
            backup_hash=backup_hash,
            staged_write_plan_hash=staged_write_plan_hash,
            target_probes=target_probes,
            discrepancies=discrepancies,
            details={"reason": "metadata_probe_contradiction"},
        )
    if unique_classifications == {"matches_post_write"}:
        idempotency_discrepancy = _evaluate_resume_idempotency(
            workflow_id=workflow_id,
            run_id=run_id,
            metadata=metadata,
            run_dir=run_dir,
            created_at_utc=created_at_utc,
        )
        if idempotency_discrepancy is not None:
            discrepancies.append(idempotency_discrepancy)
            return RecoveryAssessment(
                run_id=run_id,
                workflow_id=workflow_id,
                outcome="hard_block",
                current_workbook_hash=current_workbook_hash,
                backup_hash=backup_hash,
                staged_write_plan_hash=staged_write_plan_hash,
                target_probes=target_probes,
                discrepancies=discrepancies,
                details={"reason": "resume_idempotency_failed"},
            )
        if write_phase_status == WritePhaseStatus.COMMITTED.value:
            return RecoveryAssessment(
                run_id=run_id,
                workflow_id=workflow_id,
                outcome="safe_resume",
                current_workbook_hash=current_workbook_hash,
                backup_hash=backup_hash,
                staged_write_plan_hash=staged_write_plan_hash,
                target_probes=target_probes,
                discrepancies=[],
                details={"probe_summary": _summarize_probe_classifications(target_probes)},
            )
        discrepancies.append(
            _build_discrepancy(
                workflow_id=workflow_id,
                run_id=run_id,
                code="metadata_probe_contradiction",
                message="Recovery probe indicates committed workbook values, but persisted write phase metadata does not.",
                created_at_utc=created_at_utc,
                details={"write_phase_status": write_phase_status},
            )
        )
        return RecoveryAssessment(
            run_id=run_id,
            workflow_id=workflow_id,
            outcome="hard_block",
            current_workbook_hash=current_workbook_hash,
            backup_hash=backup_hash,
            staged_write_plan_hash=staged_write_plan_hash,
            target_probes=target_probes,
            discrepancies=discrepancies,
            details={"reason": "metadata_probe_contradiction"},
        )

    discrepancies.append(
        _build_discrepancy(
            workflow_id=workflow_id,
            run_id=run_id,
            code="mixed_target_probe_state",
            message="Recovery probe found mixed pre-write and post-write target states.",
            created_at_utc=created_at_utc,
            details={"probe_summary": _summarize_probe_classifications(target_probes)},
        )
    )
    return RecoveryAssessment(
        run_id=run_id,
        workflow_id=workflow_id,
        outcome="hard_block",
        current_workbook_hash=current_workbook_hash,
        backup_hash=backup_hash,
        staged_write_plan_hash=staged_write_plan_hash,
        target_probes=target_probes,
        discrepancies=discrepancies,
        details={"reason": "mixed_target_probe_state"},
    )


def _validate_backup_artifacts(
    *,
    workflow_id: WorkflowId,
    run_id: str,
    metadata: dict[str, Any],
    backup_workbook_path: Path,
    backup_hash_path: Path,
    created_at_utc: str,
    discrepancies: list[DiscrepancyReport],
) -> str | None:
    if not backup_workbook_path.exists() or not backup_hash_path.exists():
        discrepancies.append(
            _build_discrepancy(
                workflow_id=workflow_id,
                run_id=run_id,
                code="missing_recovery_artifact",
                message="Required backup workbook artifacts are missing.",
                created_at_utc=created_at_utc,
                details={"backup_workbook_path": str(backup_workbook_path), "backup_hash_path": str(backup_hash_path)},
            )
        )
        return None
    computed_hash = sha256_file(backup_workbook_path)
    persisted_backup_hash = backup_hash_path.read_text(encoding="utf-8").strip()
    expected_backup_hash = str(metadata.get("run_start_backup_hash", "")).strip()
    if not _is_valid_sha256(expected_backup_hash) or not _is_valid_sha256(persisted_backup_hash):
        discrepancies.append(
            _build_discrepancy(
                workflow_id=workflow_id,
                run_id=run_id,
                code="missing_recovery_artifact",
                message="Persisted backup hash is missing or malformed.",
                created_at_utc=created_at_utc,
                details={"persisted_backup_hash": persisted_backup_hash, "expected_backup_hash": expected_backup_hash},
            )
        )
        return computed_hash
    if computed_hash != persisted_backup_hash or computed_hash != expected_backup_hash:
        discrepancies.append(
            _build_discrepancy(
                workflow_id=workflow_id,
                run_id=run_id,
                code="backup_hash_mismatch",
                message="Backup hash validation failed during recovery.",
                created_at_utc=created_at_utc,
                details={
                    "computed_backup_hash": computed_hash,
                    "persisted_backup_hash": persisted_backup_hash,
                    "expected_backup_hash": expected_backup_hash,
                },
            )
        )
    return computed_hash


def _validate_staged_write_plan(
    *,
    workflow_id: WorkflowId,
    run_id: str,
    metadata: dict[str, Any],
    staged_write_plan_payload: list[Any],
    created_at_utc: str,
    discrepancies: list[DiscrepancyReport],
) -> tuple[list[WriteOperation], str | None]:
    expected_hash = str(metadata.get("staged_write_plan_hash", "")).strip()
    hash_algorithm = str(metadata.get("hash_algorithm", "")).strip()
    if hash_algorithm != HASH_ALGORITHM or not _is_valid_sha256(expected_hash):
        discrepancies.append(
            _build_discrepancy(
                workflow_id=workflow_id,
                run_id=run_id,
                code="missing_recovery_artifact",
                message="Run metadata is missing a valid staged write plan hash contract.",
                created_at_utc=created_at_utc,
                details={"hash_algorithm": hash_algorithm, "staged_write_plan_hash": expected_hash},
            )
        )
        return ([], None)
    computed_hash = canonical_json_hash(staged_write_plan_payload)
    if computed_hash != expected_hash:
        discrepancies.append(
            _build_discrepancy(
                workflow_id=workflow_id,
                run_id=run_id,
                code="staged_plan_hash_mismatch",
                message="Canonical staged write plan hash does not match persisted run metadata.",
                created_at_utc=created_at_utc,
                details={"computed_staged_write_plan_hash": computed_hash, "expected_staged_write_plan_hash": expected_hash},
            )
        )
    staged_write_plan: list[WriteOperation] = []
    try:
        for item in staged_write_plan_payload:
            staged_write_plan.append(_parse_write_operation(item))
    except (KeyError, TypeError, ValueError) as exc:
        discrepancies.append(
            _build_discrepancy(
                workflow_id=workflow_id,
                run_id=run_id,
                code="missing_recovery_artifact",
                message="Staged write plan is unreadable or malformed.",
                created_at_utc=created_at_utc,
                details={"error": str(exc)},
            )
        )
        return ([], computed_hash)
    return (staged_write_plan, computed_hash)


def _probe_staged_write_plan(
    *,
    workflow_id: WorkflowId,
    run_id: str,
    workbook_snapshot: WorkbookSnapshot,
    staged_write_plan: list[WriteOperation],
) -> dict[str, Any]:
    mapping = _resolve_workflow_header_mapping(workflow_id, workbook_snapshot)
    if mapping is None:
        return {
            "target_probes": [],
            "discrepancy": _build_discrepancy(
                workflow_id=workflow_id,
                run_id=run_id,
                code="workbook_header_mapping_invalid",
                message="Required workbook headers could not be resolved during recovery probing.",
                created_at_utc=utc_timestamp(),
                details={"sheet_name": workbook_snapshot.sheet_name},
            ),
        }

    rows_by_index = {row.row_index: row for row in workbook_snapshot.rows}
    target_probes: list[WorkbookTargetProbe] = []
    for operation in staged_write_plan:
        column_index = mapping.get(operation.column_key)
        row = rows_by_index.get(operation.row_index)
        observed_value = None if row is None or column_index is None else row.values.get(column_index, "")
        classification = _classify_recovery_probe(
            expected_pre_write_value=operation.expected_pre_write_value,
            expected_post_write_value=operation.expected_post_write_value,
            observed_value=observed_value,
            sheet_matches=(operation.sheet_name == workbook_snapshot.sheet_name),
            column_index=column_index,
        )
        target_probes.append(
            WorkbookTargetProbe(
                write_operation_id=operation.write_operation_id,
                run_id=operation.run_id,
                mail_id=operation.mail_id,
                probe_stage="recovery",
                sheet_name=operation.sheet_name,
                row_index=operation.row_index,
                column_key=operation.column_key,
                column_index=column_index,
                expected_pre_write_value=operation.expected_pre_write_value,
                expected_post_write_value=operation.expected_post_write_value,
                observed_value=observed_value,
                classification=classification,
            )
        )
    return {"target_probes": target_probes, "discrepancy": None}


def _evaluate_resume_idempotency(
    *,
    workflow_id: WorkflowId,
    run_id: str,
    metadata: dict[str, Any],
    run_dir: Path,
    created_at_utc: str,
) -> DiscrepancyReport | None:
    print_phase_status = str(metadata.get("print_phase_status", ""))
    mail_move_phase_status = str(metadata.get("mail_move_phase_status", ""))
    print_markers_dir = run_dir / "print_markers"
    mail_move_markers_dir = run_dir / "mail_move_markers"

    if (
        print_phase_status == PrintPhaseStatus.COMPLETED.value
        and print_markers_dir.exists()
        and not any(print_markers_dir.iterdir())
    ):
        return _build_discrepancy(
            workflow_id=workflow_id,
            run_id=run_id,
            code="metadata_probe_contradiction",
            message="Print phase metadata indicates completion, but no print completion markers were found.",
            created_at_utc=created_at_utc,
            details={"print_phase_status": print_phase_status},
        )
    if (
        mail_move_phase_status == MailMovePhaseStatus.COMPLETED.value
        and mail_move_markers_dir.exists()
        and not any(mail_move_markers_dir.iterdir())
    ):
        return _build_discrepancy(
            workflow_id=workflow_id,
            run_id=run_id,
            code="metadata_probe_contradiction",
            message="Mail-move phase metadata indicates completion, but no mail-move markers were found.",
            created_at_utc=created_at_utc,
            details={"mail_move_phase_status": mail_move_phase_status},
        )
    return None


def _resolve_workflow_header_mapping(
    workflow_id: WorkflowId,
    workbook_snapshot: WorkbookSnapshot,
) -> dict[str, int] | None:
    if workflow_id == WorkflowId.EXPORT_LC_SC:
        return resolve_header_mapping(workbook_snapshot, EXPORT_HEADER_SPECS)
    return {}


def _classify_recovery_probe(
    *,
    expected_pre_write_value: str | int | float | None,
    expected_post_write_value: str | int | float | None,
    observed_value: str | int | float | None,
    sheet_matches: bool,
    column_index: int | None,
) -> str:
    if not sheet_matches or column_index is None:
        return "mismatch_unknown"
    observed = _normalize_value(observed_value)
    expected_pre = _normalize_value(expected_pre_write_value)
    expected_post = _normalize_value(expected_post_write_value)
    if observed == expected_post:
        return "matches_post_write"
    if observed == expected_pre:
        return "matches_pre_write"
    if expected_pre_write_value is None and observed in {"", None}:
        return "matches_pre_write"
    return "mismatch_unknown"


def _build_discrepancy(
    *,
    workflow_id: WorkflowId,
    run_id: str,
    code: str,
    message: str,
    created_at_utc: str,
    details: dict[str, Any],
) -> DiscrepancyReport:
    return DiscrepancyReport(
        run_id=run_id,
        workflow_id=workflow_id,
        severity=FinalDecision.HARD_BLOCK,
        code=code,
        message=message,
        created_at_utc=created_at_utc,
        details=details,
    )


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise ArtifactError(f"Required recovery artifact is missing: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _parse_write_operation(item: Any) -> WriteOperation:
    if not isinstance(item, dict):
        raise ValueError("Write operation must be an object")
    return WriteOperation(
        write_operation_id=str(item["write_operation_id"]),
        run_id=str(item["run_id"]),
        mail_id=str(item["mail_id"]),
        operation_index_within_mail=int(item["operation_index_within_mail"]),
        sheet_name=str(item["sheet_name"]),
        row_index=int(item["row_index"]),
        column_key=str(item["column_key"]),
        expected_pre_write_value=item.get("expected_pre_write_value"),
        expected_post_write_value=item.get("expected_post_write_value"),
        row_eligibility_checks=[str(value) for value in item.get("row_eligibility_checks", [])],
        number_format=str(item["number_format"]) if item.get("number_format") is not None else None,
    )


def _is_valid_sha256(value: str) -> bool:
    return len(value) == HEX_DIGEST_LENGTH and all(character in "0123456789abcdef" for character in value)


def _normalize_value(value: str | int | float | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return normalized

    date_value = _try_normalize_date(normalized)
    if date_value is not None:
        return date_value

    numeric_value = _try_normalize_decimal(normalized)
    if numeric_value is not None:
        return numeric_value

    return normalized


def _try_normalize_date(value: str) -> str | None:
    try:
        parsed_datetime = datetime.fromisoformat(value)
    except ValueError:
        parsed_datetime = None
    if parsed_datetime is not None:
        if parsed_datetime.time() == datetime.min.time():
            return parsed_datetime.date().isoformat()
        return parsed_datetime.isoformat()

    for fmt in ("%Y-%m-%d", "%d-%b-%y", "%d-%b-%Y", "%d/%m/%y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _try_normalize_decimal(value: str) -> str | None:
    candidate = value.replace(",", "")
    try:
        decimal_value = Decimal(candidate)
    except InvalidOperation:
        return None
    normalized = format(decimal_value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _summarize_probe_classifications(probes: list[WorkbookTargetProbe]) -> dict[str, int]:
    return {
        "matches_post_write": sum(1 for probe in probes if probe.classification == "matches_post_write"),
        "matches_pre_write": sum(1 for probe in probes if probe.classification == "matches_pre_write"),
        "mismatch_unknown": sum(1 for probe in probes if probe.classification == "mismatch_unknown"),
    }
