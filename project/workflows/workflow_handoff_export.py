from __future__ import annotations

from pathlib import Path
from typing import Any

from project.models import WorkflowId
from project.utils.time import utc_timestamp
from project.workflows.recovery_packet import build_workflow_recovery_packet
from project.workflows.run_handoff_index import list_run_handoffs
from project.workflows.workflow_summary import build_workflow_summary


def build_workflow_handoff_export(
    *,
    run_artifact_root: Path,
    backup_root: Path,
    report_root: Path,
    workflow_id: WorkflowId,
    recent_limit: int = 10,
    queue_limit: int = 10,
    recovery_limit: int = 10,
    handoff_limit: int = 10,
) -> dict[str, Any]:
    workflow_summary = build_workflow_summary(
        run_artifact_root=run_artifact_root,
        workflow_id=workflow_id,
        recent_limit=recent_limit,
        queue_limit=queue_limit,
    )
    recovery_packet = build_workflow_recovery_packet(
        run_artifact_root=run_artifact_root,
        backup_root=backup_root,
        workflow_id=workflow_id,
        limit=recovery_limit,
    )
    recent_handoffs = list_run_handoffs(
        report_root=report_root,
        workflow_id=workflow_id,
        limit=handoff_limit,
    )
    return {
        "generated_at_utc": utc_timestamp(),
        "workflow_id": workflow_id.value,
        "workflow_handoff": {
            "workflow_summary": workflow_summary,
            "recovery_packet": recovery_packet,
            "recent_handoffs": recent_handoffs,
        },
        "summary_counts": {
            "recent_run_count": workflow_summary["summary_counts"]["recent_run_count"],
            "operator_queue_count": workflow_summary["summary_counts"]["operator_queue_count"],
            "recovery_candidate_count": workflow_summary["summary_counts"]["recovery_candidate_count"],
            "manual_verification_pending_count": workflow_summary["summary_counts"][
                "manual_verification_pending_count"
            ],
            "handled_no_action_count": workflow_summary["summary_counts"]["handled_no_action_count"],
            "duplicate_only_handled_count": workflow_summary["summary_counts"][
                "duplicate_only_handled_count"
            ],
            "no_write_noop_handled_count": workflow_summary["summary_counts"][
                "no_write_noop_handled_count"
            ],
            "recent_handoff_count": recent_handoffs["handoff_count"],
            "total_handoff_count": recent_handoffs["total_handoff_count"],
        },
    }
