from __future__ import annotations

from pathlib import Path
from typing import Any

from project.models import MailOutcomeRecord, MailProcessingStatus, RunReport, WriteOperation
from project.storage import RunArtifactPaths
from project.workflows.document_verification import summarize_manual_document_verification


def summarize_run_status(
    *,
    run_report: RunReport,
    mail_outcomes: list[MailOutcomeRecord],
    staged_write_plan: list[WriteOperation],
    artifact_paths: RunArtifactPaths,
) -> dict[str, Any]:
    mail_status_counts = _mail_processing_status_counts(mail_outcomes)
    return {
        "run_id": run_report.run_id,
        "workflow_id": run_report.workflow_id.value,
        "started_at_utc": run_report.started_at_utc,
        "completed_at_utc": run_report.completed_at_utc,
        "decision_summary": dict(run_report.summary),
        "mail_processing_status_counts": mail_status_counts,
        "phases": {
            "write": {
                "status": run_report.write_phase_status.value,
                "staged_write_operation_count": len(staged_write_plan),
                "target_probe_count": _count_jsonl_records(artifact_paths.target_probes_path),
                "commit_marker_present": _nonempty_file_exists(artifact_paths.commit_marker_path),
                "successful_mail_count": _count_mails_at_or_beyond(
                    mail_outcomes,
                    minimum_status=MailProcessingStatus.WRITTEN,
                ),
            },
            "print": {
                "status": run_report.print_phase_status.value,
                "planned_group_count": len(run_report.print_group_order),
                "completion_marker_count": _count_json_files(artifact_paths.print_markers_dir),
                "successful_mail_count": _count_mails_at_or_beyond(
                    mail_outcomes,
                    minimum_status=MailProcessingStatus.PRINTED,
                ),
            },
            "mail_moves": {
                "status": run_report.mail_move_phase_status.value,
                "planned_operation_count": len(
                    {
                        str(outcome.mail_move_operation_id).strip()
                        for outcome in mail_outcomes
                        if str(outcome.mail_move_operation_id or "").strip()
                    }
                ),
                "completion_marker_count": _count_json_files(artifact_paths.mail_move_markers_dir),
                "successful_mail_count": sum(
                    1 for outcome in mail_outcomes if outcome.processing_status == MailProcessingStatus.MOVED
                ),
            },
        },
        "manual_verification": summarize_manual_document_verification(
            run_report=run_report,
            mail_outcomes=mail_outcomes,
            artifact_paths=artifact_paths,
        ),
        "artifact_counts": {
            "discrepancy_count": _count_jsonl_records(artifact_paths.discrepancies_path),
        },
    }


def _mail_processing_status_counts(mail_outcomes: list[MailOutcomeRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for outcome in mail_outcomes:
        status = outcome.processing_status.value
        counts[status] = counts.get(status, 0) + 1
    return counts


def _count_mails_at_or_beyond(
    mail_outcomes: list[MailOutcomeRecord],
    *,
    minimum_status: MailProcessingStatus,
) -> int:
    qualifying_statuses: set[MailProcessingStatus]
    if minimum_status == MailProcessingStatus.WRITTEN:
        qualifying_statuses = {
            MailProcessingStatus.WRITTEN,
            MailProcessingStatus.PRINTED,
            MailProcessingStatus.MOVED,
        }
    elif minimum_status == MailProcessingStatus.PRINTED:
        qualifying_statuses = {
            MailProcessingStatus.PRINTED,
            MailProcessingStatus.MOVED,
        }
    else:
        qualifying_statuses = {minimum_status}
    return sum(1 for outcome in mail_outcomes if outcome.processing_status in qualifying_statuses)


def _count_json_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for child in path.iterdir() if child.is_file() and child.suffix.lower() == ".json")


def _count_jsonl_records(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _nonempty_file_exists(path: Path) -> bool:
    return path.exists() and bool(path.read_text(encoding="utf-8").strip())
