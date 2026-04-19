from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from project.models import WorkflowId
from project.utils.time import utc_timestamp


def build_summary_catalog(
    *,
    report_root: Path,
    workflow_id: WorkflowId,
) -> dict[str, Any]:
    workflow_summaries = _collect_paths(
        artifact_type="workflow_summary",
        paths=[report_root / "workflow_summaries" / f"{workflow_id.value}.summary.json"],
    )
    workflow_handoffs = _collect_paths(
        artifact_type="workflow_handoff",
        paths=[report_root / "workflow_handoffs" / f"{workflow_id.value}.handoff.json"],
    )
    run_summaries = _collect_paths(
        artifact_type="run_summary",
        paths=sorted(
            (report_root / "run_summaries").glob(f"{workflow_id.value}.*.summary.json")
        ) if (report_root / "run_summaries").exists() else [],
    )
    run_handoffs = _collect_paths(
        artifact_type="run_handoff",
        paths=sorted(
            (report_root / "run_handoffs").glob(f"{workflow_id.value}.*.handoff.json")
        ) if (report_root / "run_handoffs").exists() else [],
    )
    recovery_packets = _collect_paths(
        artifact_type="recovery_packet",
        paths=[report_root / "recovery_packets" / f"{workflow_id.value}.recovery.json"],
    )
    retention_reports = _collect_paths(
        artifact_type="retention_summary",
        paths=[report_root / "retention_reports" / f"{workflow_id.value}.retention.json"],
    )

    return {
        "generated_at_utc": utc_timestamp(),
        "workflow_id": workflow_id.value,
        "report_root": str(report_root),
        "summary_counts": {
            "workflow_summary_count": len(workflow_summaries),
            "workflow_handoff_count": len(workflow_handoffs),
            "run_summary_count": len(run_summaries),
            "run_handoff_count": len(run_handoffs),
            "recovery_packet_count": len(recovery_packets),
            "retention_summary_count": len(retention_reports),
            "total_summary_count": len(workflow_summaries)
            + len(workflow_handoffs)
            + len(run_summaries)
            + len(run_handoffs)
            + len(recovery_packets)
            + len(retention_reports),
        },
        "workflow_summaries": workflow_summaries,
        "workflow_handoffs": workflow_handoffs,
        "run_summaries": run_summaries,
        "run_handoffs": run_handoffs,
        "recovery_packets": recovery_packets,
        "retention_summaries": retention_reports,
    }


def _collect_paths(*, artifact_type: str, paths) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        entries.append(
            {
                "artifact_type": artifact_type,
                "path": str(path),
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "modified_at_utc": _modified_at_utc(path),
                "run_id": _extract_run_id(path, artifact_type),
                "artifact_metadata": _extract_artifact_metadata(path, artifact_type),
            }
        )
    entries.sort(key=lambda item: (str(item["modified_at_utc"]), str(item["filename"])), reverse=True)
    return entries


def _modified_at_utc(path: Path) -> str:
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return utc_timestamp(modified)


def _extract_run_id(path: Path, artifact_type: str) -> str | None:
    if artifact_type not in {"run_summary", "run_handoff"}:
        return None
    parts = path.name.split(".")
    if len(parts) < 4:
        return None
    return parts[1]


def _extract_artifact_metadata(path: Path, artifact_type: str) -> dict[str, Any]:
    if artifact_type not in {"workflow_handoff", "run_handoff"}:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}

    if artifact_type == "workflow_handoff":
        summary_counts = payload.get("summary_counts")
        if not isinstance(summary_counts, dict):
            return {}
        return {
            "generated_at_utc": payload.get("generated_at_utc"),
            "recent_run_count": summary_counts.get("recent_run_count"),
            "operator_queue_count": summary_counts.get("operator_queue_count"),
            "recovery_candidate_count": summary_counts.get("recovery_candidate_count"),
            "manual_verification_pending_count": summary_counts.get(
                "manual_verification_pending_count"
            ),
            "recent_handoff_count": summary_counts.get("recent_handoff_count"),
            "total_handoff_count": summary_counts.get("total_handoff_count"),
        }

    handoff_counts = payload.get("handoff_counts")
    if not isinstance(handoff_counts, dict):
        return {}
    return {
        "generated_at_utc": payload.get("generated_at_utc"),
        "mail_count": handoff_counts.get("mail_count"),
        "discrepancy_count": handoff_counts.get("discrepancy_count"),
        "manual_verification_pending_count": handoff_counts.get(
            "manual_verification_pending_count"
        ),
        "duplicate_file_skip_count": handoff_counts.get("duplicate_file_skip_count"),
        "duplicate_only_mail_count": handoff_counts.get("duplicate_only_mail_count"),
        "mixed_duplicate_and_new_mail_count": handoff_counts.get(
            "mixed_duplicate_and_new_mail_count"
        ),
        "print_marker_count": handoff_counts.get("print_marker_count"),
        "mail_move_marker_count": handoff_counts.get("mail_move_marker_count"),
    }
