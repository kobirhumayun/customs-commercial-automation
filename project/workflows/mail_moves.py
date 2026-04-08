from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Callable

from project.models import (
    DiscrepancyReport,
    FinalDecision,
    MailMoveOperation,
    MailMovePhaseStatus,
    MailOutcomeRecord,
    MailProcessingStatus,
    PrintPhaseStatus,
    RunReport,
    WritePhaseStatus,
)
from project.outlook import MailMoveProvider, MailMoveSourceLocationError
from project.storage import RunArtifactPaths
from project.storage.artifacts import write_json
from project.utils.ids import build_mail_move_operation_id
from project.utils.json import to_jsonable
from project.utils.time import utc_timestamp


def build_mail_move_operations(
    *,
    run_report: RunReport,
    mail_outcomes: list[MailOutcomeRecord],
) -> tuple[list[MailOutcomeRecord], list[MailMoveOperation]]:
    source_folder = str(run_report.resolved_source_folder_entry_id or "").strip()
    destination_folder = str(run_report.resolved_destination_folder_entry_id or "").strip()
    if not source_folder or not destination_folder:
        raise ValueError("Mail moves require resolved source and destination folder entry ids.")

    order_index = {mail_id: index for index, mail_id in enumerate(run_report.mail_iteration_order)}
    candidate_outcomes = sorted(
        (
            outcome
            for outcome in mail_outcomes
            if (
                outcome.final_decision != FinalDecision.HARD_BLOCK
                and outcome.eligible_for_mail_move
                and _mail_move_policy_allows(outcome)
            )
        ),
        key=lambda outcome: (
            order_index.get(outcome.mail_id, len(order_index)),
            outcome.snapshot_index,
            outcome.mail_id,
        ),
    )

    operations_by_mail_id: dict[str, MailMoveOperation] = {}
    for outcome in candidate_outcomes:
        operations_by_mail_id[outcome.mail_id] = MailMoveOperation(
            mail_move_operation_id=build_mail_move_operation_id(
                run_report.run_id,
                outcome.source_entry_id,
                destination_folder,
            ),
            run_id=run_report.run_id,
            mail_id=outcome.mail_id,
            entry_id=outcome.source_entry_id,
            source_folder=source_folder,
            destination_folder=destination_folder,
            moved_at_utc=None,
            move_status="pending",
        )

    updated_mail_outcomes = [
        replace(
            outcome,
            mail_move_operation_id=(
                operations_by_mail_id[outcome.mail_id].mail_move_operation_id
                if outcome.mail_id in operations_by_mail_id
                else outcome.mail_move_operation_id
            ),
            decision_reasons=_append_reason(
                _append_reason(
                    outcome.decision_reasons,
                    _mail_move_policy_reason(outcome),
                ),
                f"Planned mail move {operations_by_mail_id[outcome.mail_id].mail_move_operation_id}.",
            )
            if outcome.mail_id in operations_by_mail_id
            else (
                _append_reason(
                    outcome.decision_reasons,
                    "Mail move not planned because the mail produced no workbook writes and no duplicate-only handling signal.",
                )
                if outcome.eligible_for_mail_move and not _mail_move_policy_allows(outcome)
                else list(outcome.decision_reasons)
            ),
            eligible_for_mail_move=(
                False
                if outcome.eligible_for_mail_move and not _mail_move_policy_allows(outcome)
                else outcome.eligible_for_mail_move
            ),
        )
        for outcome in mail_outcomes
    ]
    return updated_mail_outcomes, list(operations_by_mail_id.values())


def execute_mail_moves(
    *,
    run_report: RunReport,
    mail_outcomes: list[MailOutcomeRecord],
    artifact_paths: RunArtifactPaths,
    provider: MailMoveProvider,
    require_write_committed: bool = True,
    require_print_completed: bool = True,
    run_report_persistor: Callable[[RunReport], None] | None = None,
) -> tuple[RunReport, list[MailOutcomeRecord], list[MailMoveOperation], list[DiscrepancyReport]]:
    if run_report.mail_move_phase_status not in {
        MailMovePhaseStatus.NOT_STARTED,
        MailMovePhaseStatus.MOVING,
    }:
        raise ValueError("Mail-move execution requires mail_move_phase_status=not_started or moving.")

    updated_mail_outcomes, move_operations = build_mail_move_operations(
        run_report=run_report,
        mail_outcomes=mail_outcomes,
    )
    effective_require_write_committed, effective_require_print_completed = _effective_gate_requirements(
        mail_outcomes=updated_mail_outcomes,
        require_write_committed=require_write_committed,
        require_print_completed=require_print_completed,
    )
    gate_discrepancy = _build_gate_discrepancy(
        run_report=run_report,
        require_write_committed=effective_require_write_committed,
        require_print_completed=effective_require_print_completed,
    )
    if gate_discrepancy is not None:
        hard_blocked_report = replace(run_report, mail_move_phase_status=MailMovePhaseStatus.HARD_BLOCKED)
        _persist_run_report(run_report_persistor, hard_blocked_report)
        return (
            hard_blocked_report,
            _block_mail_moves(updated_mail_outcomes),
            move_operations,
            [gate_discrepancy],
        )

    discrepancies: list[DiscrepancyReport] = []
    moving_report = replace(run_report, mail_move_phase_status=MailMovePhaseStatus.MOVING)
    _persist_run_report(run_report_persistor, moving_report)

    try:
        for operation in move_operations:
            marker_path = artifact_paths.mail_move_markers_dir / f"{operation.mail_move_operation_id}.json"
            marker_state = _check_existing_marker(marker_path, operation)
            if marker_state == "matched":
                continue
            if marker_state == "mismatch":
                hard_blocked_report = replace(moving_report, mail_move_phase_status=MailMovePhaseStatus.HARD_BLOCKED)
                _persist_run_report(run_report_persistor, hard_blocked_report)
                discrepancies.append(
                    _build_discrepancy(
                        run_report=run_report,
                        code="mail_move_marker_mismatch",
                        message="An existing mail-move completion marker conflicted with the planned move identity.",
                        details={
                            "mail_move_operation_id": operation.mail_move_operation_id,
                            "marker_path": str(marker_path),
                        },
                        mail_id=operation.mail_id,
                    )
                )
                return hard_blocked_report, _block_mail_moves(updated_mail_outcomes), move_operations, discrepancies
            try:
                move_receipt = provider.move_mail(operation)
            except MailMoveSourceLocationError as exc:
                hard_blocked_report = replace(moving_report, mail_move_phase_status=MailMovePhaseStatus.HARD_BLOCKED)
                _persist_run_report(run_report_persistor, hard_blocked_report)
                discrepancies.append(
                    _build_discrepancy(
                        run_report=run_report,
                        code="mail_source_location_mismatch",
                        message="A planned mail was not in the expected source folder at move execution time.",
                        details={
                            "mail_move_operation_id": operation.mail_move_operation_id,
                            "error": str(exc),
                        },
                        mail_id=operation.mail_id,
                    )
                )
                return hard_blocked_report, _block_mail_moves(updated_mail_outcomes), move_operations, discrepancies
            except Exception as exc:
                uncertain_report = replace(
                    moving_report,
                    mail_move_phase_status=MailMovePhaseStatus.UNCERTAIN_INCOMPLETE,
                )
                _persist_run_report(run_report_persistor, uncertain_report)
                discrepancies.append(
                    _build_discrepancy(
                        run_report=run_report,
                        code="mail_move_runtime_error",
                        message="A runtime error interrupted mail-move execution.",
                        details={
                            "mail_move_operation_id": operation.mail_move_operation_id,
                            "error": str(exc),
                        },
                        mail_id=operation.mail_id,
                    )
                )
                return uncertain_report, _block_mail_moves(updated_mail_outcomes), move_operations, discrepancies

            write_json(
                marker_path,
                {
                    "mail_move_operation_id": operation.mail_move_operation_id,
                    "run_id": operation.run_id,
                    "mail_id": operation.mail_id,
                    "entry_id": operation.entry_id,
                    "source_folder": operation.source_folder,
                    "destination_folder": operation.destination_folder,
                    "move_status": "moved",
                    "manual_verification_summary": dict(
                        _manual_verification_summary_for_mail(updated_mail_outcomes, operation.mail_id)
                    ),
                    "write_disposition": _write_disposition_for_mail(updated_mail_outcomes, operation.mail_id),
                    "mail_move_policy_reason": _mail_move_policy_reason_for_mail(
                        updated_mail_outcomes,
                        operation.mail_id,
                    ),
                    "move_execution_receipt": to_jsonable(move_receipt),
                    "moved_at_utc": utc_timestamp(),
                },
            )

        completed_report = replace(moving_report, mail_move_phase_status=MailMovePhaseStatus.COMPLETED)
        _persist_run_report(run_report_persistor, completed_report)
        return (
            completed_report,
            _mark_moved_mail_outcomes(updated_mail_outcomes, move_operations),
            move_operations,
            discrepancies,
        )
    except Exception as exc:
        uncertain_report = replace(
            moving_report,
            mail_move_phase_status=MailMovePhaseStatus.UNCERTAIN_INCOMPLETE,
        )
        _persist_run_report(run_report_persistor, uncertain_report)
        discrepancies.append(
            _build_discrepancy(
                run_report=run_report,
                code="mail_move_runtime_error",
                message="A runtime error interrupted mail-move execution.",
                details={"error": str(exc)},
                mail_id=None,
            )
        )
        return uncertain_report, _block_mail_moves(updated_mail_outcomes), move_operations, discrepancies


def _build_gate_discrepancy(
    *,
    run_report: RunReport,
    require_write_committed: bool,
    require_print_completed: bool,
) -> DiscrepancyReport | None:
    write_gate_failed = require_write_committed and run_report.write_phase_status != WritePhaseStatus.COMMITTED
    print_gate_failed = require_print_completed and run_report.print_phase_status != PrintPhaseStatus.COMPLETED
    if not write_gate_failed and not print_gate_failed:
        return None
    return _build_discrepancy(
        run_report=run_report,
        code="mail_move_gate_unsatisfied",
        message="Mail moves are blocked until prior run phases reach terminal success.",
        details={
            "write_phase_status": run_report.write_phase_status.value,
            "print_phase_status": run_report.print_phase_status.value,
            "require_write_committed": require_write_committed,
            "require_print_completed": require_print_completed,
        },
        mail_id=None,
    )


def _effective_gate_requirements(
    *,
    mail_outcomes: list[MailOutcomeRecord],
    require_write_committed: bool,
    require_print_completed: bool,
) -> tuple[bool, bool]:
    candidate_outcomes = [
        outcome
        for outcome in mail_outcomes
        if outcome.eligible_for_mail_move and _mail_move_policy_allows(outcome)
    ]
    if candidate_outcomes and all(
        str(outcome.write_disposition or "").strip() == "duplicate_only_noop"
        for outcome in candidate_outcomes
    ):
        return False, False
    return require_write_committed, require_print_completed


def _check_existing_marker(path: Path, operation: MailMoveOperation) -> str:
    if not path.exists():
        return "missing"
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "mail_move_operation_id": operation.mail_move_operation_id,
        "entry_id": operation.entry_id,
        "source_folder": operation.source_folder,
        "destination_folder": operation.destination_folder,
        "mail_id": operation.mail_id,
    }
    for key, value in expected.items():
        if str(payload.get(key, "")).strip() != str(value):
            return "mismatch"
    return "matched"


def _mark_moved_mail_outcomes(
    mail_outcomes: list[MailOutcomeRecord],
    move_operations: list[MailMoveOperation],
) -> list[MailOutcomeRecord]:
    moved_by_mail_id = {operation.mail_id: operation for operation in move_operations}
    updated: list[MailOutcomeRecord] = []
    for outcome in mail_outcomes:
        operation = moved_by_mail_id.get(outcome.mail_id)
        if operation is None:
            updated.append(outcome)
            continue
        updated.append(
            replace(
                outcome,
                processing_status=MailProcessingStatus.MOVED,
                eligible_for_mail_move=False,
                mail_move_operation_id=operation.mail_move_operation_id,
                decision_reasons=_append_reason(
                    _append_manual_verification_reason(
                        outcome.decision_reasons,
                        outcome.manual_document_verification_summary,
                    ),
                    "Planned mail move completed successfully.",
                ),
            )
        )
    return updated


def _block_mail_moves(mail_outcomes: list[MailOutcomeRecord]) -> list[MailOutcomeRecord]:
    return [
        replace(
            outcome,
            eligible_for_mail_move=False,
            decision_reasons=_append_reason(
                outcome.decision_reasons,
                "Mail-move phase is blocked or incomplete; no downstream movement should occur.",
            ),
        )
        if outcome.eligible_for_mail_move
        else outcome
        for outcome in mail_outcomes
    ]


def _append_reason(reasons: list[str], reason: str) -> list[str]:
    if reason in reasons:
        return list(reasons)
    return list(reasons) + [reason]


def summarize_mail_move_manual_verification(mail_outcomes: list[MailOutcomeRecord]) -> dict[str, int]:
    moved_outcomes = [
        outcome
        for outcome in mail_outcomes
        if outcome.processing_status == MailProcessingStatus.MOVED
        and isinstance(outcome.manual_document_verification_summary, dict)
    ]
    document_count = 0
    verified_count = 0
    pending_count = 0
    untracked_count = 0
    for outcome in moved_outcomes:
        summary = outcome.manual_document_verification_summary or {}
        document_count += int(summary.get("document_count", 0))
        verified_count += int(summary.get("verified_count", 0))
        pending_count += int(summary.get("pending_count", 0))
        untracked_count += int(summary.get("untracked_count", 0))
    return {
        "document_count": document_count,
        "verified_count": verified_count,
        "pending_count": pending_count,
        "untracked_count": untracked_count,
    }


def summarize_mail_move_policy(mail_outcomes: list[MailOutcomeRecord]) -> dict[str, int]:
    summary = {
        "eligible_mail_count": 0,
        "duplicate_only_move_eligible_count": 0,
        "mixed_duplicate_and_new_move_eligible_count": 0,
        "new_writes_move_eligible_count": 0,
        "other_move_eligible_count": 0,
    }
    for outcome in mail_outcomes:
        if not outcome.eligible_for_mail_move:
            continue
        summary["eligible_mail_count"] += 1
        disposition = str(outcome.write_disposition or "").strip()
        if disposition == "duplicate_only_noop":
            summary["duplicate_only_move_eligible_count"] += 1
        elif disposition == "mixed_duplicate_and_new_writes":
            summary["mixed_duplicate_and_new_move_eligible_count"] += 1
        elif disposition == "new_writes_staged":
            summary["new_writes_move_eligible_count"] += 1
        else:
            summary["other_move_eligible_count"] += 1
    return summary


def _manual_verification_summary_for_mail(
    mail_outcomes: list[MailOutcomeRecord],
    mail_id: str,
) -> dict[str, object]:
    outcome = next((item for item in mail_outcomes if item.mail_id == mail_id), None)
    if outcome is None or not isinstance(outcome.manual_document_verification_summary, dict):
        return {}
    return dict(outcome.manual_document_verification_summary)


def _append_manual_verification_reason(
    reasons: list[str],
    summary: dict[str, object] | None,
) -> list[str]:
    if not isinstance(summary, dict) or not summary:
        return list(reasons)
    reason = (
        "Manual PDF verification status at mail-move time: "
        f"{summary.get('verified_count', 0)}/{summary.get('document_count', 0)} verified, "
        f"{summary.get('pending_count', 0)} pending, {summary.get('untracked_count', 0)} untracked."
    )
    return _append_reason(reasons, reason)


def _mail_move_policy_allows(outcome: MailOutcomeRecord) -> bool:
    disposition = str(outcome.write_disposition or "").strip()
    if disposition == "no_write_noop":
        return False
    return True


def _mail_move_policy_reason(outcome: MailOutcomeRecord) -> str:
    disposition = str(outcome.write_disposition or "").strip()
    if disposition == "duplicate_only_noop":
        return "Mail move remains eligible because this mail was handled as duplicate-only and requires no print output."
    if disposition == "mixed_duplicate_and_new_writes":
        return "Mail move remains eligible because this mail contained both duplicate files and newly written files."
    if disposition == "new_writes_staged":
        return "Mail move remains eligible because this mail produced newly written workbook rows."
    return "Mail move remains eligible because validation completed without a hard block."


def _write_disposition_for_mail(
    mail_outcomes: list[MailOutcomeRecord],
    mail_id: str,
) -> str | None:
    outcome = next((item for item in mail_outcomes if item.mail_id == mail_id), None)
    if outcome is None or outcome.write_disposition is None:
        return None
    return str(outcome.write_disposition)


def _mail_move_policy_reason_for_mail(
    mail_outcomes: list[MailOutcomeRecord],
    mail_id: str,
) -> str | None:
    outcome = next((item for item in mail_outcomes if item.mail_id == mail_id), None)
    if outcome is None:
        return None
    return _mail_move_policy_reason(outcome)


def _build_discrepancy(
    *,
    run_report: RunReport,
    code: str,
    message: str,
    details: dict[str, object],
    mail_id: str | None,
) -> DiscrepancyReport:
    return DiscrepancyReport(
        run_id=run_report.run_id,
        mail_id=mail_id,
        workflow_id=run_report.workflow_id,
        severity=FinalDecision.HARD_BLOCK,
        code=code,
        message=message,
        created_at_utc=utc_timestamp(),
        details={"non_rule_source": "mail_move_execution", **details},
    )


def _persist_run_report(
    persistor: Callable[[RunReport], None] | None,
    run_report: RunReport,
) -> None:
    if persistor is not None:
        persistor(run_report)
