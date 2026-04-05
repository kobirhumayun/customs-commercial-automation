from __future__ import annotations

from pathlib import Path
from typing import Any

from project.models import WorkflowId
from project.utils.time import utc_timestamp
from project.workflows.operator_queue import build_operator_queue
from project.workflows.run_index import list_workflow_runs


def build_workflow_summary(
    *,
    run_artifact_root: Path,
    workflow_id: WorkflowId,
    recent_limit: int = 10,
    queue_limit: int = 10,
) -> dict[str, Any]:
    if recent_limit <= 0:
        raise ValueError("Workflow summary recent run limit must be greater than zero.")
    if queue_limit <= 0:
        raise ValueError("Workflow summary queue limit must be greater than zero.")

    recent_runs = list_workflow_runs(
        run_artifact_root=run_artifact_root,
        workflow_id=workflow_id,
        limit=recent_limit,
    )
    operator_queue = build_operator_queue(
        run_artifact_root=run_artifact_root,
        workflow_id=workflow_id,
        limit=queue_limit,
    )
    return {
        "generated_at_utc": utc_timestamp(),
        "workflow_id": workflow_id.value,
        "run_artifact_root": str(run_artifact_root),
        "recent_runs": recent_runs,
        "operator_queue": operator_queue,
        "summary_counts": {
            "recent_run_count": recent_runs["run_count"],
            "operator_queue_count": operator_queue["queue_count"],
            "recovery_candidate_count": operator_queue["recovery_candidate_count"],
            "manual_verification_pending_count": operator_queue["manual_verification_pending_count"],
            "recent_duplicate_file_skip_count": sum(
                int(run.get("duplicate_summary", {}).get("duplicate_file_skip_count", 0))
                for run in recent_runs["runs"]
            ),
            "recent_duplicate_only_mail_count": sum(
                int(run.get("duplicate_summary", {}).get("duplicate_only_mail_count", 0))
                for run in recent_runs["runs"]
            ),
            "recent_mixed_duplicate_and_new_mail_count": sum(
                int(run.get("duplicate_summary", {}).get("mixed_duplicate_and_new_mail_count", 0))
                for run in recent_runs["runs"]
            ),
        },
    }
