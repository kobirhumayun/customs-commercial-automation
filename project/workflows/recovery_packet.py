from __future__ import annotations

from pathlib import Path
from typing import Any

from project.models import WorkflowId
from project.storage import create_run_artifact_layout
from project.utils.time import utc_timestamp
from project.workflows.print_planning import load_print_planning_bundle
from project.workflows.run_artifact_reporting import summarize_run_artifacts
from project.workflows.run_index import list_recovery_candidates
from project.workflows.run_recovery_precheck import build_recovery_precheck
from project.workflows.run_reporting import summarize_run_status


def build_workflow_recovery_packet(
    *,
    run_artifact_root: Path,
    backup_root: Path,
    workflow_id: WorkflowId,
    limit: int = 10,
) -> dict[str, Any]:
    if limit <= 0:
        raise ValueError("Recovery packet limit must be greater than zero.")

    candidates = list_recovery_candidates(
        run_artifact_root=run_artifact_root,
        workflow_id=workflow_id,
        limit=limit,
    )
    packet_runs: list[dict[str, Any]] = []
    load_error_count = 0
    for candidate in candidates["runs"]:
        run_id = str(candidate.get("run_id") or "").strip()
        if not run_id:
            continue
        artifact_paths = create_run_artifact_layout(
            run_artifact_root=run_artifact_root,
            backup_root=backup_root,
            workflow_id=workflow_id.value,
            run_id=run_id,
        )
        artifact_inventory = summarize_run_artifacts(artifact_paths=artifact_paths)
        try:
            run_report, mail_outcomes, staged_write_plan = load_print_planning_bundle(
                run_artifact_root=run_artifact_root,
                workflow_id=workflow_id,
                run_id=run_id,
            )
        except (OSError, ValueError) as exc:
            load_error_count += 1
            packet_runs.append(
                {
                    "run_id": run_id,
                    "workflow_id": workflow_id.value,
                    "candidate": dict(candidate),
                    "artifacts": artifact_inventory,
                    "load_error": str(exc),
                }
            )
            continue

        run_status = summarize_run_status(
            run_report=run_report,
            mail_outcomes=mail_outcomes,
            staged_write_plan=staged_write_plan,
            artifact_paths=artifact_paths,
        )
        recovery_precheck = build_recovery_precheck(
            run_status=run_status,
            artifact_inventory=artifact_inventory,
        )
        packet_runs.append(
            {
                "run_id": run_id,
                "workflow_id": workflow_id.value,
                "candidate": dict(candidate),
                "run_status": run_status,
                "artifacts": artifact_inventory,
                "recovery_precheck": recovery_precheck,
            }
        )

    return {
        "generated_at_utc": utc_timestamp(),
        "workflow_id": workflow_id.value,
        "run_artifact_root": str(run_artifact_root),
        "backup_root": str(backup_root),
        "limit": limit,
        "candidate_count": candidates["run_count"],
        "load_error_count": load_error_count,
        "runs": packet_runs,
    }
