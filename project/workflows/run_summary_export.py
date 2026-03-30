from __future__ import annotations

from typing import Any

from project.models import MailOutcomeRecord, RunReport, WriteOperation
from project.storage import RunArtifactPaths
from project.utils.time import utc_timestamp
from project.workflows.run_artifact_reporting import summarize_run_artifacts
from project.workflows.run_recovery_precheck import build_recovery_precheck
from project.workflows.run_reporting import summarize_run_status


def build_run_summary_export(
    *,
    run_report: RunReport,
    mail_outcomes: list[MailOutcomeRecord],
    staged_write_plan: list[WriteOperation],
    artifact_paths: RunArtifactPaths,
) -> dict[str, Any]:
    run_status = summarize_run_status(
        run_report=run_report,
        mail_outcomes=mail_outcomes,
        staged_write_plan=staged_write_plan,
        artifact_paths=artifact_paths,
    )
    artifact_inventory = summarize_run_artifacts(artifact_paths=artifact_paths)
    recovery_precheck = build_recovery_precheck(
        run_status=run_status,
        artifact_inventory=artifact_inventory,
    )
    return {
        "generated_at_utc": utc_timestamp(),
        "run_id": run_report.run_id,
        "workflow_id": run_report.workflow_id.value,
        "run_status": run_status,
        "artifacts": artifact_inventory,
        "recovery_precheck": recovery_precheck,
        "summary_counts": {
            "mail_count": len(mail_outcomes),
            "staged_write_operation_count": len(staged_write_plan),
            "recovery_issue_count": recovery_precheck["issue_count"],
            "manual_verification_pending_count": (
                run_status["manual_verification"]["bundle"]["pending_document_count"]
            ),
            "discrepancy_count": run_status["artifact_counts"]["discrepancy_count"],
        },
    }
