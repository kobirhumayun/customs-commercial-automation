from __future__ import annotations

from typing import Any


def build_recovery_precheck(
    *,
    run_status: dict[str, Any],
    artifact_inventory: dict[str, Any],
) -> dict[str, Any]:
    phases = dict(run_status.get("phases", {}))
    artifacts = dict(artifact_inventory)
    core_files = dict(artifacts.get("core_files", {}))
    backup_artifacts = dict(artifacts.get("backup_artifacts", {}))
    directories = dict(artifacts.get("directories", {}))

    write_status = str(phases.get("write", {}).get("status", ""))
    print_status = str(phases.get("print", {}).get("status", ""))
    mail_move_status = str(phases.get("mail_moves", {}).get("status", ""))

    needs_recovery_gate = (
        write_status == "uncertain_not_committed"
        or print_status == "uncertain_incomplete"
        or mail_move_status == "uncertain_incomplete"
    )
    can_attempt_recovery_assessment = all(
        (
            _is_present(core_files.get("run_metadata")),
            _is_present(core_files.get("staged_write_plan")),
            _is_present(backup_artifacts.get("backup_workbook")),
            _is_present(backup_artifacts.get("backup_hash")),
        )
    )

    missing_prerequisites: list[dict[str, str]] = []
    if not _is_present(core_files.get("run_metadata")):
        missing_prerequisites.append(
            _issue("missing_run_metadata", "Run metadata is missing or empty; recovery cannot be assessed safely.")
        )
    if not _is_present(core_files.get("staged_write_plan")):
        missing_prerequisites.append(
            _issue(
                "missing_staged_write_plan",
                "Staged write plan is missing or empty; recovery cannot compare expected workbook targets.",
            )
        )
    if not _is_present(backup_artifacts.get("backup_workbook")):
        missing_prerequisites.append(
            _issue(
                "missing_backup_workbook",
                "Backup workbook artifact is missing; recovery cannot verify the original run-start baseline.",
            )
        )
    if not _is_present(backup_artifacts.get("backup_hash")):
        missing_prerequisites.append(
            _issue(
                "missing_backup_hash",
                "Backup hash artifact is missing or empty; recovery cannot validate backup integrity.",
            )
        )

    contradictions: list[dict[str, str]] = []
    if write_status == "committed" and not _is_present(core_files.get("commit_marker")):
        contradictions.append(
            _issue(
                "committed_without_commit_marker",
                "Write phase is marked committed, but the commit marker artifact is missing or empty.",
            )
        )
    if print_status == "completed" and _directory_file_count(directories.get("print_markers")) == 0:
        contradictions.append(
            _issue(
                "print_completed_without_markers",
                "Print phase is marked completed, but no print markers were found.",
            )
        )
    if mail_move_status == "completed" and _directory_file_count(directories.get("mail_move_markers")) == 0:
        contradictions.append(
            _issue(
                "mail_moves_completed_without_markers",
                "Mail-move phase is marked completed, but no mail-move markers were found.",
            )
        )

    advisories: list[dict[str, str]] = []
    if write_status == "uncertain_not_committed" and _is_present(core_files.get("commit_marker")):
        advisories.append(
            _issue(
                "uncertain_write_with_commit_marker",
                "Write phase is uncertain, but a commit marker exists. Review this run carefully before recovery.",
            )
        )
    if run_status.get("manual_verification", {}).get("bundle", {}).get("audit_error_count", 0):
        advisories.append(
            _issue(
                "manual_verification_audit_errors_present",
                "Manual document-verification audit files include extraction errors; operator review may be needed.",
            )
        )

    return {
        "run_id": run_status.get("run_id"),
        "workflow_id": run_status.get("workflow_id"),
        "needs_recovery_gate": needs_recovery_gate,
        "can_attempt_recovery_assessment": can_attempt_recovery_assessment,
        "phase_statuses": {
            "write_phase_status": write_status,
            "print_phase_status": print_status,
            "mail_move_phase_status": mail_move_status,
        },
        "missing_prerequisites": missing_prerequisites,
        "contradictions": contradictions,
        "advisories": advisories,
        "issue_count": len(missing_prerequisites) + len(contradictions) + len(advisories),
    }


def _is_present(summary: object) -> bool:
    if not isinstance(summary, dict):
        return False
    return bool(summary.get("exists")) and bool(summary.get("nonempty"))


def _directory_file_count(summary: object) -> int:
    if not isinstance(summary, dict):
        return 0
    value = summary.get("file_count")
    return value if isinstance(value, int) else 0


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}
