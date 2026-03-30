from __future__ import annotations

from pathlib import Path
from typing import Any

from project.models import WorkflowId
from project.workflows.summary_catalog import build_summary_catalog


def list_workflow_handoffs(
    *,
    report_root: Path,
    workflow_id: WorkflowId,
) -> dict[str, Any]:
    catalog = build_summary_catalog(
        report_root=report_root,
        workflow_id=workflow_id,
    )
    handoffs = list(catalog.get("workflow_handoffs", []))
    return {
        "generated_at_utc": catalog["generated_at_utc"],
        "workflow_id": workflow_id.value,
        "report_root": str(report_root),
        "handoff_count": len(handoffs),
        "workflow_handoffs": handoffs,
    }
