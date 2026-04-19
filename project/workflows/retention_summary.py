from __future__ import annotations

from pathlib import Path
from typing import Any

from project.models import WorkflowId
from project.workflows.retention_reporting import build_retention_report


def build_retention_summary(
    *,
    run_artifact_root: Path,
    backup_root: Path,
    report_root: Path,
    workflow_id: WorkflowId,
    older_than_days: int = 30,
) -> dict[str, Any]:
    retention_report = build_retention_report(
        run_artifact_root=run_artifact_root,
        backup_root=backup_root,
        report_root=report_root,
        workflow_id=workflow_id,
        older_than_days=older_than_days,
    )
    return {
        "workflow_id": workflow_id.value,
        "older_than_days": older_than_days,
        "retention_report": retention_report,
        "summary_counts": dict(retention_report["summary_counts"]),
    }
