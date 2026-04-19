from __future__ import annotations

from pathlib import Path

from project.models import WorkflowId
from project.workflows.recovery_packet import build_workflow_recovery_packet
from project.workflows.retention_summary import build_retention_summary
from project.workflows.summary_catalog import build_summary_catalog
from project.workflows.workflow_summary import build_workflow_summary


def build_workflow_dashboard_markdown(
    *,
    run_artifact_root: Path,
    backup_root: Path,
    report_root: Path,
    workflow_id: WorkflowId,
    recent_limit: int = 10,
    queue_limit: int = 10,
    recovery_limit: int = 10,
    retention_days: int = 30,
) -> str:
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
    retention_summary = build_retention_summary(
        run_artifact_root=run_artifact_root,
        backup_root=backup_root,
        report_root=report_root,
        workflow_id=workflow_id,
        older_than_days=retention_days,
    )
    summary_catalog = build_summary_catalog(
        report_root=report_root,
        workflow_id=workflow_id,
    )

    lines: list[str] = [
        f"# Workflow Dashboard: {workflow_id.value}",
        "",
        f"Generated at: {workflow_summary['generated_at_utc']}",
        "",
        "## Snapshot",
        "",
        f"- Recent runs: {workflow_summary['summary_counts']['recent_run_count']}",
        f"- Operator queue: {workflow_summary['summary_counts']['operator_queue_count']}",
        f"- Recovery candidates: {workflow_summary['summary_counts']['recovery_candidate_count']}",
        f"- Manual verification pending: {workflow_summary['summary_counts']['manual_verification_pending_count']}",
        f"- Handled with no action needed: {workflow_summary['summary_counts']['handled_no_action_count']}",
        f"- Duplicate-only handled runs: {workflow_summary['summary_counts']['duplicate_only_handled_count']}",
        f"- No-write/no-op handled runs: {workflow_summary['summary_counts']['no_write_noop_handled_count']}",
        f"- Retention stale runs: {retention_summary['summary_counts']['stale_run_count']}",
        f"- Generated summaries on disk: {summary_catalog['summary_counts']['total_summary_count']}",
        "",
        "## Operator Queue",
        "",
    ]
    queue_runs = workflow_summary["operator_queue"]["runs"]
    if queue_runs:
        for run in queue_runs:
            reason_labels = ", ".join(reason["code"] for reason in run.get("queue_reasons", []))
            lines.append(
                f"- `{run['run_id']}` [{run['queue_priority']}] "
                f"write={run.get('write_phase_status')}, print={run.get('print_phase_status')}, "
                f"move={run.get('mail_move_phase_status')} | reasons: {reason_labels}"
            )
    else:
        lines.append("- No actionable runs in the current operator queue.")

    lines.extend(
        [
            "",
            "## Handled Runs",
            "",
        ]
    )
    handled_runs = workflow_summary["operator_queue"]["handled_runs"]
    if handled_runs:
        for run in handled_runs:
            lines.append(
                f"- `{run['run_id']}` [{run['handled_category']}] "
                f"write={run.get('write_phase_status')}, print={run.get('print_phase_status')}, "
                f"move={run.get('mail_move_phase_status')} | {run.get('handled_reason')}"
            )
    else:
        lines.append("- No recently indexed runs were classified as handled with no action needed.")

    lines.extend(
        [
            "",
            "## Recovery Candidates",
            "",
        ]
    )
    recovery_runs = recovery_packet["runs"]
    if recovery_runs:
        for run in recovery_runs:
            if "load_error" in run:
                lines.append(f"- `{run['run_id']}` load_error: {run['load_error']}")
                continue
            precheck = run["recovery_precheck"]
            lines.append(
                f"- `{run['run_id']}` issues={precheck['issue_count']} "
                f"needs_gate={precheck['needs_recovery_gate']} "
                f"can_assess={precheck['can_attempt_recovery_assessment']}"
            )
    else:
        lines.append("- No current recovery candidates.")

    lines.extend(
        [
            "",
            f"## Retention Candidates Older Than {retention_days} Days",
            "",
        ]
    )
    stale_runs = retention_summary["retention_report"]["stale_runs"]
    if stale_runs:
        for run in stale_runs:
            lines.append(
                f"- `{run['run_id']}` age_days={run['age_days']} reason={run['reason']}"
            )
    else:
        lines.append("- No stale terminal run artifacts matched the current threshold.")

    lines.extend(
        [
            "",
            "## Generated Summaries",
            "",
            f"- Workflow summaries: {summary_catalog['summary_counts']['workflow_summary_count']}",
            f"- Workflow handoffs: {summary_catalog['summary_counts']['workflow_handoff_count']}",
            f"- Run summaries: {summary_catalog['summary_counts']['run_summary_count']}",
            f"- Run handoffs: {summary_catalog['summary_counts']['run_handoff_count']}",
            f"- Recovery packets: {summary_catalog['summary_counts']['recovery_packet_count']}",
            f"- Retention summaries: {summary_catalog['summary_counts']['retention_summary_count']}",
            "",
        ]
    )
    lines.extend(
        [
            "## Workflow Handoffs",
            "",
        ]
    )
    workflow_handoffs = summary_catalog["workflow_handoffs"][:5]
    if workflow_handoffs:
        for handoff in workflow_handoffs:
            metadata = handoff.get("artifact_metadata", {}) or {}
            lines.append(
                f"- modified={handoff.get('modified_at_utc')} size_bytes={handoff.get('size_bytes')} "
                f"queue={metadata.get('operator_queue_count')} recovery={metadata.get('recovery_candidate_count')} "
                f"recent_handoffs={metadata.get('recent_handoff_count')}"
            )
    else:
        lines.append("- No workflow handoff packets are currently indexed.")
    lines.extend(
        [
            "",
            "## Recent Run Handoffs",
            "",
        ]
    )
    recent_run_handoffs = summary_catalog["run_handoffs"][:5]
    if recent_run_handoffs:
        for handoff in recent_run_handoffs:
            metadata = handoff.get("artifact_metadata", {}) or {}
            lines.append(
                f"- `{handoff.get('run_id')}` modified={handoff.get('modified_at_utc')} "
                f"size_bytes={handoff.get('size_bytes')} discrepancies={metadata.get('discrepancy_count')} "
                f"duplicate_skips={metadata.get('duplicate_file_skip_count')} "
                f"duplicate_only_mails={metadata.get('duplicate_only_mail_count')} "
                f"mixed_duplicate_new_mails={metadata.get('mixed_duplicate_and_new_mail_count')} "
                f"print_markers={metadata.get('print_marker_count')} "
                f"mail_move_markers={metadata.get('mail_move_marker_count')}"
            )
    else:
        lines.append("- No run handoff packets are currently indexed.")
    lines.append("")
    return "\n".join(lines) + "\n"
