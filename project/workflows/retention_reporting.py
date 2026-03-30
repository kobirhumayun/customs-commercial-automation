from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from project.models import WorkflowId
from project.utils.time import utc_now, utc_timestamp


def build_retention_report(
    *,
    run_artifact_root: Path,
    backup_root: Path,
    report_root: Path,
    workflow_id: WorkflowId,
    older_than_days: int = 30,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    if older_than_days <= 0:
        raise ValueError("Retention report threshold must be greater than zero days.")

    reference_time = now_utc or utc_now()
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=UTC)
    cutoff = reference_time - timedelta(days=older_than_days)

    stale_runs, stale_run_ids = _collect_stale_runs(
        run_artifact_root=run_artifact_root,
        workflow_id=workflow_id,
        cutoff=cutoff,
        reference_time=reference_time,
    )
    stale_backups = _collect_stale_backups(
        backup_root=backup_root,
        workflow_id=workflow_id,
        cutoff=cutoff,
        reference_time=reference_time,
        stale_run_ids=stale_run_ids,
    )
    stale_reports = _collect_stale_reports(
        report_root=report_root,
        workflow_id=workflow_id,
        cutoff=cutoff,
        reference_time=reference_time,
    )

    return {
        "generated_at_utc": utc_timestamp(reference_time),
        "workflow_id": workflow_id.value,
        "older_than_days": older_than_days,
        "cutoff_utc": utc_timestamp(cutoff),
        "summary_counts": {
            "stale_run_count": len(stale_runs),
            "stale_backup_count": len(stale_backups),
            "stale_report_count": len(stale_reports),
        },
        "stale_runs": stale_runs,
        "stale_backups": stale_backups,
        "stale_reports": stale_reports,
    }


def _collect_stale_runs(
    *,
    run_artifact_root: Path,
    workflow_id: WorkflowId,
    cutoff: datetime,
    reference_time: datetime,
) -> tuple[list[dict[str, Any]], set[str]]:
    workflow_root = run_artifact_root / workflow_id.value
    if not workflow_root.exists():
        return [], set()

    candidates: list[dict[str, Any]] = []
    stale_run_ids: set[str] = set()
    for run_dir in workflow_root.iterdir():
        if not run_dir.is_dir():
            continue
        entry = _build_run_entry(run_dir=run_dir, cutoff=cutoff, reference_time=reference_time)
        if entry is None:
            continue
        candidates.append(entry)
        stale_run_ids.add(entry["run_id"])
    candidates.sort(key=lambda item: (int(item["age_days"]), str(item["run_id"])), reverse=True)
    return candidates, stale_run_ids


def _build_run_entry(
    *,
    run_dir: Path,
    cutoff: datetime,
    reference_time: datetime,
) -> dict[str, Any] | None:
    metadata_path = run_dir / "run_metadata.json"
    if not metadata_path.exists():
        last_modified = _last_modified_utc(run_dir)
        if last_modified > cutoff:
            return None
        return {
            "run_id": run_dir.name,
            "run_root": str(run_dir),
            "age_days": _age_days(last_modified, reference_time),
            "started_at_utc": None,
            "write_phase_status": None,
            "print_phase_status": None,
            "mail_move_phase_status": None,
            "reason": "old_run_artifact_missing_metadata",
        }

    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        last_modified = _last_modified_utc(metadata_path)
        if last_modified > cutoff:
            return None
        return {
            "run_id": run_dir.name,
            "run_root": str(run_dir),
            "age_days": _age_days(last_modified, reference_time),
            "started_at_utc": None,
            "write_phase_status": None,
            "print_phase_status": None,
            "mail_move_phase_status": None,
            "reason": "old_run_artifact_unreadable_metadata",
        }

    if not isinstance(payload, dict):
        raise ValueError(f"Run metadata must be a JSON object: {metadata_path}")

    started_at_utc = _parse_timestamp(payload.get("started_at_utc")) or _last_modified_utc(metadata_path)
    if started_at_utc > cutoff:
        return None
    if not _run_is_retention_candidate(payload):
        return None
    return {
        "run_id": str(payload.get("run_id", run_dir.name)),
        "run_root": str(run_dir),
        "age_days": _age_days(started_at_utc, reference_time),
        "started_at_utc": payload.get("started_at_utc"),
        "write_phase_status": payload.get("write_phase_status"),
        "print_phase_status": payload.get("print_phase_status"),
        "mail_move_phase_status": payload.get("mail_move_phase_status"),
        "reason": "old_terminal_run_artifact",
    }


def _run_is_retention_candidate(payload: dict[str, Any]) -> bool:
    write_phase_status = str(payload.get("write_phase_status", "")).strip()
    print_phase_status = str(payload.get("print_phase_status", "")).strip()
    mail_move_phase_status = str(payload.get("mail_move_phase_status", "")).strip()
    if write_phase_status in {
        "prevalidating_targets",
        "prevalidated",
        "applying",
        "uncertain_not_committed",
    }:
        return False
    if print_phase_status in {"printing", "uncertain_incomplete"}:
        return False
    if mail_move_phase_status in {"moving", "uncertain_incomplete"}:
        return False
    return True


def _collect_stale_backups(
    *,
    backup_root: Path,
    workflow_id: WorkflowId,
    cutoff: datetime,
    reference_time: datetime,
    stale_run_ids: set[str],
) -> list[dict[str, Any]]:
    workflow_root = backup_root / workflow_id.value
    if not workflow_root.exists():
        return []

    candidates: list[dict[str, Any]] = []
    for backup_dir in workflow_root.iterdir():
        if not backup_dir.is_dir():
            continue
        modified = _last_modified_utc(backup_dir)
        if modified > cutoff:
            continue
        run_id = backup_dir.name
        reason = "backup_for_stale_run" if run_id in stale_run_ids else "orphan_or_untracked_old_backup"
        candidates.append(
            {
                "run_id": run_id,
                "backup_root": str(backup_dir),
                "age_days": _age_days(modified, reference_time),
                "reason": reason,
            }
        )
    candidates.sort(key=lambda item: (int(item["age_days"]), str(item["run_id"])), reverse=True)
    return candidates


def _collect_stale_reports(
    *,
    report_root: Path,
    workflow_id: WorkflowId,
    cutoff: datetime,
    reference_time: datetime,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    report_files = [
        (report_root / "workflow_summaries" / f"{workflow_id.value}.summary.json", "workflow_summary"),
        (report_root / "recovery_packets" / f"{workflow_id.value}.recovery.json", "recovery_packet"),
    ]
    run_summary_root = report_root / "run_summaries"
    if run_summary_root.exists():
        report_files.extend(
            (path, "run_summary")
            for path in run_summary_root.glob(f"{workflow_id.value}.*.summary.json")
            if path.is_file()
        )
    run_handoff_root = report_root / "run_handoffs"
    if run_handoff_root.exists():
        report_files.extend(
            (path, "run_handoff")
            for path in run_handoff_root.glob(f"{workflow_id.value}.*.handoff.json")
            if path.is_file()
        )

    for path, artifact_type in report_files:
        if not path.exists():
            continue
        modified = _last_modified_utc(path)
        if modified > cutoff:
            continue
        candidates.append(
            {
                "artifact_type": artifact_type,
                "path": str(path),
                "age_days": _age_days(modified, reference_time),
                "reason": f"old_{artifact_type}",
            }
        )
    candidates.sort(key=lambda item: (int(item["age_days"]), str(item["path"])), reverse=True)
    return candidates


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _last_modified_utc(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def _age_days(older_time: datetime, newer_time: datetime) -> int:
    return int((newer_time - older_time).total_seconds() // 86400)
