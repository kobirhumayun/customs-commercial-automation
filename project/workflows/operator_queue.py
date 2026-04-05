from __future__ import annotations

from typing import Any

from project.models import WorkflowId
from project.workflows.run_index import list_workflow_runs


def build_operator_queue(
    *,
    run_artifact_root,
    workflow_id: WorkflowId,
    limit: int = 10,
) -> dict[str, Any]:
    if limit <= 0:
        raise ValueError("Operator queue limit must be greater than zero.")

    indexed = list_workflow_runs(
        run_artifact_root=run_artifact_root,
        workflow_id=workflow_id,
        limit=max(limit, 1_000_000),
    )
    queued_runs: list[dict[str, Any]] = []
    handled_runs: list[dict[str, Any]] = []
    recovery_candidate_count = 0
    manual_verification_pending_count = 0
    for run in indexed["runs"]:
        queue_reasons = _build_queue_reasons(run)
        if not queue_reasons:
            handled_reason = _build_handled_reason(run)
            if handled_reason is not None:
                handled_runs.append(
                    {
                        **run,
                        "handled_category": handled_reason["code"],
                        "handled_reason": handled_reason["message"],
                    }
                )
            continue
        if any(reason["code"] == "recovery_attention_needed" for reason in queue_reasons):
            recovery_candidate_count += 1
        if any(reason["code"] == "manual_verification_pending" for reason in queue_reasons):
            manual_verification_pending_count += 1
        queued_runs.append(
            {
                **run,
                "queue_priority": _queue_priority(queue_reasons),
                "queue_reasons": queue_reasons,
            }
        )

    queued_runs.sort(
        key=lambda item: (
            str(item.get("started_at_utc") or ""),
            str(item.get("run_id") or ""),
        ),
        reverse=True,
    )
    queued_runs.sort(key=_queue_sort_key)
    queued_runs = queued_runs[:limit]
    handled_runs.sort(
        key=lambda item: (
            str(item.get("started_at_utc") or ""),
            str(item.get("run_id") or ""),
        ),
        reverse=True,
    )
    handled_runs = handled_runs[:limit]
    duplicate_only_handled_count = sum(
        1 for run in handled_runs if run.get("handled_category") == "duplicate_only_handled"
    )
    no_write_noop_handled_count = sum(
        1 for run in handled_runs if run.get("handled_category") == "no_write_noop_handled"
    )
    return {
        "workflow_id": workflow_id.value,
        "run_artifact_root": indexed["run_artifact_root"],
        "workflow_run_root": indexed["workflow_run_root"],
        "limit": limit,
        "queue_count": len(queued_runs),
        "recovery_candidate_count": recovery_candidate_count,
        "manual_verification_pending_count": manual_verification_pending_count,
        "handled_no_action_count": len(handled_runs),
        "duplicate_only_handled_count": duplicate_only_handled_count,
        "no_write_noop_handled_count": no_write_noop_handled_count,
        "runs": queued_runs,
        "handled_runs": handled_runs,
    }


def _build_queue_reasons(run: dict[str, Any]) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    if _is_recovery_candidate(run):
        reasons.append(
            {
                "code": "recovery_attention_needed",
                "message": (
                    "This run has interrupted or uncertain phase state and should be reviewed with the "
                    "recovery precheck before further action."
                ),
            }
        )
    pending_count = run.get("manual_verification_pending_count")
    if (
        run.get("manual_verification_present")
        and run.get("manual_verification_complete") is not True
        and isinstance(pending_count, int)
        and pending_count > 0
    ):
        reasons.append(
            {
                "code": "manual_verification_pending",
                "message": (
                    f"Manual PDF verification still has {pending_count} pending document(s) for this run."
                ),
            }
        )
    return reasons


def _is_recovery_candidate(run: dict[str, Any]) -> bool:
    write_phase_status = str(run.get("write_phase_status") or "")
    print_phase_status = str(run.get("print_phase_status") or "")
    mail_move_phase_status = str(run.get("mail_move_phase_status") or "")
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


def _queue_priority(queue_reasons: list[dict[str, str]]) -> str:
    if any(reason["code"] == "recovery_attention_needed" for reason in queue_reasons):
        return "recovery"
    return "manual_verification"


def _build_handled_reason(run: dict[str, Any]) -> dict[str, str] | None:
    dispositions = (
        dict(run.get("write_disposition_counts", {}))
        if isinstance(run.get("write_disposition_counts"), dict)
        else {}
    )
    duplicate_only_count = int(dispositions.get("duplicate_only_noop", 0) or 0)
    no_write_noop_count = int(dispositions.get("no_write_noop", 0) or 0)
    new_writes_count = int(dispositions.get("new_writes_staged", 0) or 0)
    mixed_count = int(dispositions.get("mixed_duplicate_and_new_writes", 0) or 0)
    if duplicate_only_count > 0 and new_writes_count == 0 and mixed_count == 0:
        return {
            "code": "duplicate_only_handled",
            "message": (
                "This run only encountered duplicate file numbers that were intentionally suppressed, "
                "so no operator action is required."
            ),
        }
    if no_write_noop_count > 0 and duplicate_only_count == 0 and new_writes_count == 0 and mixed_count == 0:
        return {
            "code": "no_write_noop_handled",
            "message": (
                "This run completed without new workbook writes or duplicate-handling actions, "
                "so no operator action is required."
            ),
        }
    return None


def _queue_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    priority_rank = 0 if item.get("queue_priority") == "recovery" else 1
    return priority_rank, "", ""
