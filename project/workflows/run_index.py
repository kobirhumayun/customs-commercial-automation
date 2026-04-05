from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from project.models import WorkflowId
from project.workflows.duplicate_handling import classify_write_disposition, summarize_duplicate_decision_reasons


def list_workflow_runs(
    *,
    run_artifact_root: Path,
    workflow_id: WorkflowId,
    limit: int = 10,
) -> dict[str, Any]:
    if limit <= 0:
        raise ValueError("Run listing limit must be greater than zero.")

    workflow_root = run_artifact_root / workflow_id.value
    if not workflow_root.exists():
        runs: list[dict[str, Any]] = []
    else:
        runs = [
            _load_run_index_entry(run_dir)
            for run_dir in workflow_root.iterdir()
            if run_dir.is_dir()
        ]
        runs.sort(key=_run_index_sort_key, reverse=True)
        runs = runs[:limit]

    return {
        "workflow_id": workflow_id.value,
        "run_artifact_root": str(run_artifact_root),
        "workflow_run_root": str(workflow_root),
        "limit": limit,
        "run_count": len(runs),
        "runs": runs,
    }


def list_recovery_candidates(
    *,
    run_artifact_root: Path,
    workflow_id: WorkflowId,
    limit: int = 10,
) -> dict[str, Any]:
    payload = list_workflow_runs(
        run_artifact_root=run_artifact_root,
        workflow_id=workflow_id,
        limit=max(limit, 1_000_000),
    )
    candidates = [run for run in payload["runs"] if _is_recovery_candidate(run)]
    candidates = candidates[:limit]
    return {
        "workflow_id": workflow_id.value,
        "run_artifact_root": str(run_artifact_root),
        "workflow_run_root": str(run_artifact_root / workflow_id.value),
        "limit": limit,
        "run_count": len(candidates),
        "runs": candidates,
    }


def _load_run_index_entry(run_dir: Path) -> dict[str, Any]:
    metadata_path = run_dir / "run_metadata.json"
    manual_verification_path = run_dir / "document_manual_verification.json"
    discrepancies_path = run_dir / "discrepancies.jsonl"
    mail_outcomes_path = run_dir / "mail_outcomes.jsonl"
    if not metadata_path.exists():
        return {
            "run_id": run_dir.name,
            "run_root": str(run_dir),
            "metadata_status": "missing",
            "started_at_utc": None,
            "completed_at_utc": None,
            "write_phase_status": None,
            "print_phase_status": None,
            "mail_move_phase_status": None,
            "decision_summary": {},
            "print_group_count": 0,
            "discrepancy_count": _count_jsonl_records(discrepancies_path),
            "manual_verification_present": _nonempty_json_file_exists(manual_verification_path),
            "manual_verification_complete": None,
            "manual_verification_pending_count": None,
            **_summarize_mail_outcomes(mail_outcomes_path),
        }

    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "run_id": run_dir.name,
            "run_root": str(run_dir),
            "metadata_status": "error",
            "metadata_error": str(exc),
            "started_at_utc": None,
            "completed_at_utc": None,
            "write_phase_status": None,
            "print_phase_status": None,
            "mail_move_phase_status": None,
            "decision_summary": {},
            "print_group_count": 0,
            "discrepancy_count": _count_jsonl_records(discrepancies_path),
            "manual_verification_present": _nonempty_json_file_exists(manual_verification_path),
            "manual_verification_complete": None,
            "manual_verification_pending_count": None,
            **_summarize_mail_outcomes(mail_outcomes_path),
        }

    if not isinstance(payload, dict):
        raise ValueError(f"Run metadata must be a JSON object: {metadata_path}")

    manual_verification_summary = _summarize_manual_verification_bundle(manual_verification_path)
    mail_outcome_summary = _summarize_mail_outcomes(mail_outcomes_path)
    return {
        "run_id": str(payload.get("run_id", run_dir.name)),
        "run_root": str(run_dir),
        "metadata_status": "ready",
        "started_at_utc": payload.get("started_at_utc"),
        "completed_at_utc": payload.get("completed_at_utc"),
        "write_phase_status": payload.get("write_phase_status"),
        "print_phase_status": payload.get("print_phase_status"),
        "mail_move_phase_status": payload.get("mail_move_phase_status"),
        "decision_summary": (
            dict(payload.get("summary", {}))
            if isinstance(payload.get("summary"), dict)
            else {}
        ),
        "print_group_count": len(payload.get("print_group_order", []))
        if isinstance(payload.get("print_group_order"), list)
        else 0,
        "discrepancy_count": _count_jsonl_records(discrepancies_path),
        **manual_verification_summary,
        **mail_outcome_summary,
    }


def _summarize_manual_verification_bundle(path: Path) -> dict[str, Any]:
    if not _nonempty_json_file_exists(path):
        return {
            "manual_verification_present": False,
            "manual_verification_complete": None,
            "manual_verification_pending_count": None,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "manual_verification_present": True,
            "manual_verification_complete": None,
            "manual_verification_pending_count": None,
        }
    if not isinstance(payload, dict):
        return {
            "manual_verification_present": True,
            "manual_verification_complete": None,
            "manual_verification_pending_count": None,
        }

    pending_count = payload.get("pending_document_count")
    if not isinstance(pending_count, int):
        documents = payload.get("documents", [])
        pending_count = (
            sum(
                1
                for document in documents
                if isinstance(document, dict)
                and str(document.get("manual_verification_status", "")).strip() != "verified"
            )
            if isinstance(documents, list)
            else None
        )
    manual_complete = payload.get("manual_verification_complete")
    if not isinstance(manual_complete, bool):
        manual_complete = pending_count == 0 if isinstance(pending_count, int) else None
    return {
        "manual_verification_present": True,
        "manual_verification_complete": manual_complete,
        "manual_verification_pending_count": pending_count,
    }


def _run_index_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    metadata_rank = 1 if item.get("metadata_status") == "ready" else 0
    started_at_utc = str(item.get("started_at_utc") or "")
    run_id = str(item.get("run_id") or "")
    return metadata_rank, started_at_utc, run_id


def _summarize_mail_outcomes(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "mail_outcome_count": 0,
            "write_disposition_counts": {},
            "duplicate_summary": {
                "duplicate_file_skip_count": 0,
                "duplicate_in_workbook_file_count": 0,
                "duplicate_in_run_file_count": 0,
                "duplicate_affected_mail_count": 0,
                "duplicate_only_mail_count": 0,
                "mixed_duplicate_and_new_mail_count": 0,
            },
        }

    try:
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError):
        return {
            "mail_outcome_count": 0,
            "write_disposition_counts": {},
            "duplicate_summary": {
                "duplicate_file_skip_count": 0,
                "duplicate_in_workbook_file_count": 0,
                "duplicate_in_run_file_count": 0,
                "duplicate_affected_mail_count": 0,
                "duplicate_only_mail_count": 0,
                "mixed_duplicate_and_new_mail_count": 0,
            },
        }

    write_disposition_counts: dict[str, int] = {}
    duplicate_in_workbook_file_count = 0
    duplicate_in_run_file_count = 0
    duplicate_affected_mail_count = 0
    duplicate_only_mail_count = 0
    mixed_duplicate_and_new_mail_count = 0

    for record in records:
        if not isinstance(record, dict):
            continue
        decision_reasons = (
            list(record.get("decision_reasons", []))
            if isinstance(record.get("decision_reasons"), list)
            else []
        )
        staged_write_operations = (
            list(record.get("staged_write_operations", []))
            if isinstance(record.get("staged_write_operations"), list)
            else []
        )
        disposition = str(
            record.get("write_disposition")
            or classify_write_disposition(
                decision_reasons=decision_reasons,
                staged_write_operations=staged_write_operations,
            )
        ).strip()
        if disposition:
            write_disposition_counts[disposition] = write_disposition_counts.get(disposition, 0) + 1

        duplicate_counts = summarize_duplicate_decision_reasons(decision_reasons)
        duplicate_file_skip_count = duplicate_counts["duplicate_file_skip_count"]
        if duplicate_file_skip_count == 0:
            continue

        duplicate_in_workbook_file_count += duplicate_counts["duplicate_in_workbook_file_count"]
        duplicate_in_run_file_count += duplicate_counts["duplicate_in_run_file_count"]
        duplicate_affected_mail_count += 1
        if disposition == "duplicate_only_noop":
            duplicate_only_mail_count += 1
        elif disposition == "mixed_duplicate_and_new_writes":
            mixed_duplicate_and_new_mail_count += 1

    return {
        "mail_outcome_count": len([record for record in records if isinstance(record, dict)]),
        "write_disposition_counts": write_disposition_counts,
        "duplicate_summary": {
            "duplicate_file_skip_count": duplicate_in_workbook_file_count + duplicate_in_run_file_count,
            "duplicate_in_workbook_file_count": duplicate_in_workbook_file_count,
            "duplicate_in_run_file_count": duplicate_in_run_file_count,
            "duplicate_affected_mail_count": duplicate_affected_mail_count,
            "duplicate_only_mail_count": duplicate_only_mail_count,
            "mixed_duplicate_and_new_mail_count": mixed_duplicate_and_new_mail_count,
        },
    }


def _is_recovery_candidate(item: dict[str, Any]) -> bool:
    if item.get("metadata_status") != "ready":
        return False
    write_phase_status = str(item.get("write_phase_status") or "")
    print_phase_status = str(item.get("print_phase_status") or "")
    mail_move_phase_status = str(item.get("mail_move_phase_status") or "")
    return (
        write_phase_status in {
            "prevalidating_targets",
            "prevalidated",
            "applying",
            "uncertain_not_committed",
        }
        or print_phase_status in {
            "printing",
            "uncertain_incomplete",
        }
        or mail_move_phase_status in {
            "moving",
            "uncertain_incomplete",
        }
    )


def _count_jsonl_records(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _nonempty_json_file_exists(path: Path) -> bool:
    return path.exists() and bool(path.read_text(encoding="utf-8").strip())
