from __future__ import annotations

from typing import Any

from project.models import MailOutcomeRecord, RunReport, WriteOperation
from project.storage import RunArtifactPaths
from project.utils.time import utc_timestamp
from project.workflows.mail_move_marker_reporting import summarize_mail_move_markers
from project.workflows.print_marker_reporting import summarize_print_markers
from project.workflows.run_summary_export import build_run_summary_export
from project.workflows.transport_execution_reporting import build_transport_execution_report


def build_run_handoff_export(
    *,
    run_report: RunReport,
    mail_outcomes: list[MailOutcomeRecord],
    staged_write_plan: list[WriteOperation],
    artifact_paths: RunArtifactPaths,
) -> dict[str, Any]:
    run_summary = build_run_summary_export(
        run_report=run_report,
        mail_outcomes=mail_outcomes,
        staged_write_plan=staged_write_plan,
        artifact_paths=artifact_paths,
    )
    transport_execution = build_transport_execution_report(
        print_marker_summary=summarize_print_markers(
            print_markers_dir=artifact_paths.print_markers_dir,
        ),
        mail_move_marker_summary=summarize_mail_move_markers(
            mail_move_markers_dir=artifact_paths.mail_move_markers_dir,
        ),
    )
    return {
        "generated_at_utc": utc_timestamp(),
        "run_id": run_report.run_id,
        "workflow_id": run_report.workflow_id.value,
        "run_summary": run_summary,
        "transport_execution": transport_execution,
        "handoff_counts": {
            "mail_count": run_summary["summary_counts"]["mail_count"],
            "discrepancy_count": run_summary["summary_counts"]["discrepancy_count"],
            "manual_verification_pending_count": run_summary["summary_counts"][
                "manual_verification_pending_count"
            ],
            "print_marker_count": transport_execution["summary_counts"]["print_marker_count"],
            "mail_move_marker_count": transport_execution["summary_counts"]["mail_move_marker_count"],
        },
    }
