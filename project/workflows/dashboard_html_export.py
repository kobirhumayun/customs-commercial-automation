from __future__ import annotations

from html import escape
from pathlib import Path

from project.models import WorkflowId
from project.workflows.recovery_packet import build_workflow_recovery_packet
from project.workflows.retention_summary import build_retention_summary
from project.workflows.summary_catalog import build_summary_catalog
from project.workflows.workflow_summary import build_workflow_summary


def build_workflow_dashboard_html(
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

    snapshot_rows = [
        ("Recent runs", workflow_summary["summary_counts"]["recent_run_count"]),
        ("Operator queue", workflow_summary["summary_counts"]["operator_queue_count"]),
        ("Recovery candidates", workflow_summary["summary_counts"]["recovery_candidate_count"]),
        (
            "Manual verification pending",
            workflow_summary["summary_counts"]["manual_verification_pending_count"],
        ),
        (
            "Handled with no action needed",
            workflow_summary["summary_counts"]["handled_no_action_count"],
        ),
        (
            "Duplicate-only handled runs",
            workflow_summary["summary_counts"]["duplicate_only_handled_count"],
        ),
        (
            "No-write/no-op handled runs",
            workflow_summary["summary_counts"]["no_write_noop_handled_count"],
        ),
        ("Retention stale runs", retention_summary["summary_counts"]["stale_run_count"]),
        ("Generated summaries on disk", summary_catalog["summary_counts"]["total_summary_count"]),
    ]

    queue_rows: list[tuple[str, str, str, str, str]] = []
    for run in workflow_summary["operator_queue"]["runs"]:
        queue_rows.append(
            (
                run["run_id"],
                str(run["queue_priority"]),
                str(run.get("write_phase_status")),
                str(run.get("print_phase_status")),
                ", ".join(reason["code"] for reason in run.get("queue_reasons", [])) or "none",
            )
        )
    handled_rows: list[tuple[str, str, str, str, str]] = []
    for run in workflow_summary["operator_queue"]["handled_runs"]:
        handled_rows.append(
            (
                run["run_id"],
                str(run.get("handled_category")),
                str(run.get("write_phase_status")),
                str(run.get("print_phase_status")),
                str(run.get("handled_reason") or ""),
            )
        )

    recovery_rows: list[tuple[str, str, str, str]] = []
    for run in recovery_packet["runs"]:
        if "load_error" in run:
            recovery_rows.append((run["run_id"], "load_error", escape(str(run["load_error"])), "n/a"))
            continue
        precheck = run["recovery_precheck"]
        recovery_rows.append(
            (
                run["run_id"],
                str(precheck["issue_count"]),
                _bool_label(precheck["needs_recovery_gate"]),
                _bool_label(precheck["can_attempt_recovery_assessment"]),
            )
        )

    retention_rows: list[tuple[str, str, str]] = []
    for run in retention_summary["retention_report"]["stale_runs"]:
        retention_rows.append((run["run_id"], str(run["age_days"]), str(run["reason"])))

    generated_rows = [
        ("Workflow summaries", summary_catalog["summary_counts"]["workflow_summary_count"]),
        ("Workflow handoffs", summary_catalog["summary_counts"]["workflow_handoff_count"]),
        ("Run summaries", summary_catalog["summary_counts"]["run_summary_count"]),
        ("Run handoffs", summary_catalog["summary_counts"]["run_handoff_count"]),
        ("Recovery packets", summary_catalog["summary_counts"]["recovery_packet_count"]),
        ("Retention summaries", summary_catalog["summary_counts"]["retention_summary_count"]),
    ]
    workflow_handoff_rows: list[tuple[str, str]] = []
    for handoff in summary_catalog["workflow_handoffs"][:5]:
        metadata = handoff.get("artifact_metadata", {}) or {}
        workflow_handoff_rows.append(
            (
                str(handoff.get("modified_at_utc")),
                str(handoff.get("size_bytes")),
                str(metadata.get("operator_queue_count")),
                str(metadata.get("recovery_candidate_count")),
                str(metadata.get("recent_handoff_count")),
            )
        )
    handoff_rows: list[tuple[str, str, str]] = []
    for handoff in summary_catalog["run_handoffs"][:5]:
        metadata = handoff.get("artifact_metadata", {}) or {}
        handoff_rows.append(
            (
                str(handoff.get("run_id")),
                str(handoff.get("modified_at_utc")),
                str(handoff.get("size_bytes")),
                str(metadata.get("discrepancy_count")),
                str(metadata.get("duplicate_file_skip_count")),
                str(metadata.get("duplicate_only_mail_count")),
                str(metadata.get("mixed_duplicate_and_new_mail_count")),
                str(metadata.get("print_marker_count")),
                str(metadata.get("mail_move_marker_count")),
            )
        )

    body = "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="utf-8">',
            f"  <title>Workflow Dashboard: {escape(workflow_id.value)}</title>",
            "  <style>",
            "    :root { color-scheme: light; }",
            "    body { font-family: 'Segoe UI', Tahoma, sans-serif; margin: 24px; color: #1f2933; background: #f6f8fb; }",
            "    main { max-width: 1120px; margin: 0 auto; }",
            "    h1, h2 { color: #102a43; }",
            "    .meta { color: #52606d; margin-bottom: 24px; }",
            "    .section { background: #ffffff; border: 1px solid #d9e2ec; border-radius: 10px; padding: 18px 20px; margin-bottom: 18px; }",
            "    table { width: 100%; border-collapse: collapse; margin-top: 10px; }",
            "    th, td { border-bottom: 1px solid #e5e7eb; padding: 10px 12px; text-align: left; vertical-align: top; }",
            "    th { background: #f0f4f8; font-weight: 600; }",
            "    code { font-family: Consolas, 'Courier New', monospace; background: #f0f4f8; padding: 1px 4px; border-radius: 4px; }",
            "    .empty { color: #7b8794; font-style: italic; }",
            "  </style>",
            "</head>",
            "<body>",
            "  <main>",
            f"    <h1>Workflow Dashboard: {escape(workflow_id.value)}</h1>",
            f"    <p class=\"meta\">Generated at: {escape(str(workflow_summary['generated_at_utc']))}</p>",
            _render_key_value_section("Snapshot", snapshot_rows),
            _render_table_section(
                "Operator Queue",
                headers=["Run ID", "Priority", "Write", "Print", "Reasons"],
                rows=queue_rows,
                empty_message="No actionable runs in the current operator queue.",
                code_columns={0},
            ),
            _render_table_section(
                "Handled Runs",
                headers=["Run ID", "Category", "Write", "Print", "Reason"],
                rows=handled_rows,
                empty_message="No recently indexed runs were classified as handled with no action needed.",
                code_columns={0},
            ),
            _render_table_section(
                "Recovery Candidates",
                headers=["Run ID", "Issues", "Needs Gate", "Can Assess"],
                rows=recovery_rows,
                empty_message="No current recovery candidates.",
                code_columns={0},
            ),
            _render_table_section(
                f"Retention Candidates Older Than {retention_days} Days",
                headers=["Run ID", "Age (days)", "Reason"],
                rows=retention_rows,
                empty_message="No stale terminal run artifacts matched the current threshold.",
                code_columns={0},
            ),
            _render_key_value_section("Generated Summaries", generated_rows),
            _render_table_section(
                "Workflow Handoffs",
                headers=["Modified", "Size (bytes)", "Queue", "Recovery", "Recent Handoffs"],
                rows=workflow_handoff_rows,
                empty_message="No workflow handoff packets are currently indexed.",
            ),
            _render_table_section(
                "Recent Run Handoffs",
                headers=[
                    "Run ID",
                    "Modified",
                    "Size (bytes)",
                    "Discrepancies",
                    "Duplicate Skips",
                    "Duplicate-Only Mails",
                    "Mixed Duplicate/New Mails",
                    "Print Markers",
                    "Mail Move Markers",
                ],
                rows=handoff_rows,
                empty_message="No run handoff packets are currently indexed.",
                code_columns={0},
            ),
            "  </main>",
            "</body>",
            "</html>",
        ]
    )
    return body + "\n"


def _render_key_value_section(title: str, rows: list[tuple[str, object]]) -> str:
    items = "\n".join(
        f"        <tr><th>{escape(str(label))}</th><td>{escape(str(value))}</td></tr>" for label, value in rows
    )
    return "\n".join(
        [
            '    <section class="section">',
            f"      <h2>{escape(title)}</h2>",
            "      <table>",
            "        <tbody>",
            items,
            "        </tbody>",
            "      </table>",
            "    </section>",
        ]
    )


def _render_table_section(
    title: str,
    *,
    headers: list[str],
    rows: list[tuple[str, ...]],
    empty_message: str,
    code_columns: set[int] | None = None,
) -> str:
    if not rows:
        return "\n".join(
            [
                '    <section class="section">',
                f"      <h2>{escape(title)}</h2>",
                f"      <p class=\"empty\">{escape(empty_message)}</p>",
                "    </section>",
            ]
        )

    code_columns = code_columns or set()
    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    row_html = "\n".join(
        "        <tr>"
        + "".join(_render_cell(value, column_index in code_columns) for column_index, value in enumerate(row))
        + "</tr>"
        for row in rows
    )
    return "\n".join(
        [
            '    <section class="section">',
            f"      <h2>{escape(title)}</h2>",
            "      <table>",
            f"        <thead><tr>{header_html}</tr></thead>",
            "        <tbody>",
            row_html,
            "        </tbody>",
            "      </table>",
            "    </section>",
        ]
    )


def _render_cell(value: str, as_code: bool) -> str:
    escaped = escape(str(value))
    if as_code:
        return f"<td><code>{escaped}</code></td>"
    return f"<td>{escaped}</td>"


def _bool_label(value: bool) -> str:
    return "yes" if value else "no"
