from __future__ import annotations

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
    run_summaries = _collect_paths(
        artifact_type="run_summary",
        paths=sorted(
            (report_root / "run_summaries").glob(f"{workflow_id.value}.*.summary.json")
        ) if (report_root / "run_summaries").exists() else [],
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
            "run_summary_count": len(run_summaries),
            "recovery_packet_count": len(recovery_packets),
            "retention_summary_count": len(retention_reports),
            "total_summary_count": len(workflow_summaries)
            + len(run_summaries)
            + len(recovery_packets)
            + len(retention_reports),
        },
        "workflow_summaries": workflow_summaries,
        "run_summaries": run_summaries,
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
            }
        )
    entries.sort(key=lambda item: (str(item["modified_at_utc"]), str(item["filename"])), reverse=True)
    return entries


def _modified_at_utc(path: Path) -> str:
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return utc_timestamp(modified)


def _extract_run_id(path: Path, artifact_type: str) -> str | None:
    if artifact_type != "run_summary":
        return None
    parts = path.name.split(".")
    if len(parts) < 4:
        return None
    return parts[1]
