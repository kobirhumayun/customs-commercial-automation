from __future__ import annotations

from pathlib import Path
from typing import Any

from project.models import WorkflowId
from project.workflows.summary_catalog import build_summary_catalog


def list_run_handoffs(
    *,
    report_root: Path,
    workflow_id: WorkflowId,
    limit: int = 10,
) -> dict[str, Any]:
    if limit <= 0:
        raise ValueError("Run handoff limit must be greater than zero.")

    catalog = build_summary_catalog(
        report_root=report_root,
        workflow_id=workflow_id,
    )
    handoffs = list(catalog.get("run_handoffs", []))[:limit]
    return {
        "generated_at_utc": catalog["generated_at_utc"],
        "workflow_id": workflow_id.value,
        "report_root": str(report_root),
        "handoff_count": len(handoffs),
        "total_handoff_count": int(catalog["summary_counts"]["run_handoff_count"]),
        "run_handoffs": handoffs,
    }
