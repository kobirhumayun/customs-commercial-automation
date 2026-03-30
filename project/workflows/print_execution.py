from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable

from project.models import (
    DiscrepancyReport,
    FinalDecision,
    MailOutcomeRecord,
    MailProcessingStatus,
    PrintBatch,
    PrintPhaseStatus,
    RunReport,
)
from project.printing import PrintAdapterUnavailableError, PrintProvider
from project.storage import RunArtifactPaths
from project.storage.artifacts import write_json
from project.utils.time import utc_timestamp


def execute_print_batches(
    *,
    run_report: RunReport,
    mail_outcomes: list[MailOutcomeRecord],
    print_batches: list[PrintBatch],
    artifact_paths: RunArtifactPaths,
    provider: PrintProvider,
    run_report_persistor: Callable[[RunReport], None] | None = None,
) -> tuple[RunReport, list[MailOutcomeRecord], list[DiscrepancyReport]]:
    if run_report.print_phase_status not in {PrintPhaseStatus.PLANNED, PrintPhaseStatus.PRINTING}:
        raise ValueError("Print execution requires print_phase_status=planned or printing.")

    discrepancies: list[DiscrepancyReport] = []
    printing_report = replace(run_report, print_phase_status=PrintPhaseStatus.PRINTING)
    _persist_run_report(run_report_persistor, printing_report)

    try:
        for batch_index, batch in enumerate(print_batches):
            marker_path = artifact_paths.print_markers_dir / f"{batch.print_group_id}.json"
            marker_state = _check_existing_marker(marker_path, batch.completion_marker_id)
            if marker_state == "matched":
                continue
            if marker_state == "mismatch":
                hard_blocked_report = replace(printing_report, print_phase_status=PrintPhaseStatus.HARD_BLOCKED)
                _persist_run_report(run_report_persistor, hard_blocked_report)
                discrepancies.append(
                    _build_print_discrepancy(
                        run_report=run_report,
                        code="print_marker_mismatch",
                        message="An existing print completion marker conflicted with the planned print group identity.",
                        details={"print_group_id": batch.print_group_id, "marker_path": str(marker_path)},
                        mail_id=batch.mail_id,
                    )
                )
                return hard_blocked_report, _block_print_mail_moves(mail_outcomes), discrepancies
            try:
                provider.print_group(
                    batch,
                    blank_page_after_group=batch_index < (len(print_batches) - 1),
                )
            except PrintAdapterUnavailableError as exc:
                hard_blocked_report = replace(printing_report, print_phase_status=PrintPhaseStatus.HARD_BLOCKED)
                _persist_run_report(run_report_persistor, hard_blocked_report)
                discrepancies.append(
                    _build_print_discrepancy(
                        run_report=run_report,
                        code="print_adapter_unavailable",
                        message="The configured live print adapter was unavailable.",
                        details={"print_group_id": batch.print_group_id, "error": str(exc)},
                        mail_id=batch.mail_id,
                    )
                )
                return hard_blocked_report, _block_print_mail_moves(mail_outcomes), discrepancies
            except FileNotFoundError as exc:
                uncertain_report = replace(printing_report, print_phase_status=PrintPhaseStatus.UNCERTAIN_INCOMPLETE)
                _persist_run_report(run_report_persistor, uncertain_report)
                discrepancies.append(
                    _build_print_discrepancy(
                        run_report=run_report,
                        code="print_source_document_missing",
                        message="A planned print document was missing at execution time.",
                        details={"print_group_id": batch.print_group_id, "missing_path": str(exc)},
                        mail_id=batch.mail_id,
                    )
                )
                return uncertain_report, _block_print_mail_moves(mail_outcomes), discrepancies
            except Exception as exc:
                uncertain_report = replace(printing_report, print_phase_status=PrintPhaseStatus.UNCERTAIN_INCOMPLETE)
                _persist_run_report(run_report_persistor, uncertain_report)
                discrepancies.append(
                    _build_print_discrepancy(
                        run_report=run_report,
                        code="print_group_runtime_error",
                        message="Print execution was interrupted for a planned print group.",
                        details={"print_group_id": batch.print_group_id, "error": str(exc)},
                        mail_id=batch.mail_id,
                    )
                )
                return uncertain_report, _block_print_mail_moves(mail_outcomes), discrepancies

            write_json(
                marker_path,
                {
                    "print_group_id": batch.print_group_id,
                    "completion_marker_id": batch.completion_marker_id,
                    "run_id": batch.run_id,
                    "mail_id": batch.mail_id,
                    "document_path_hashes": list(batch.document_path_hashes),
                    "manual_verification_summary": dict(batch.manual_verification_summary),
                    "printed_at_utc": utc_timestamp(),
                },
            )

        completed_report = replace(printing_report, print_phase_status=PrintPhaseStatus.COMPLETED)
        _persist_run_report(run_report_persistor, completed_report)
        return completed_report, _mark_printed_mail_outcomes(mail_outcomes, print_batches), discrepancies
    except Exception as exc:
        uncertain_report = replace(printing_report, print_phase_status=PrintPhaseStatus.UNCERTAIN_INCOMPLETE)
        _persist_run_report(run_report_persistor, uncertain_report)
        discrepancies.append(
            _build_print_discrepancy(
                run_report=run_report,
                code="print_group_runtime_error",
                message="A runtime error interrupted print execution.",
                details={"error": str(exc)},
                mail_id=None,
            )
        )
        return uncertain_report, _block_print_mail_moves(mail_outcomes), discrepancies


def _check_existing_marker(path: Path, completion_marker_id: str) -> str:
    if not path.exists():
        return "missing"
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    return (
        "matched"
        if str(payload.get("completion_marker_id", "")).strip() == completion_marker_id
        else "mismatch"
    )


def _mark_printed_mail_outcomes(
    mail_outcomes: list[MailOutcomeRecord],
    print_batches: list[PrintBatch],
) -> list[MailOutcomeRecord]:
    printed_batch_by_mail_id = {batch.mail_id: batch for batch in print_batches}
    updated: list[MailOutcomeRecord] = []
    for outcome in mail_outcomes:
        batch = printed_batch_by_mail_id.get(outcome.mail_id)
        if batch is None:
            updated.append(outcome)
            continue
        updated.append(
            replace(
                outcome,
                processing_status=MailProcessingStatus.PRINTED,
                eligible_for_print=False,
                decision_reasons=list(outcome.decision_reasons)
                + ["Planned print group completed successfully."]
                + _manual_verification_execution_reasons(batch),
            )
        )
    return updated


def _block_print_mail_moves(mail_outcomes: list[MailOutcomeRecord]) -> list[MailOutcomeRecord]:
    return [
        replace(
            outcome,
            eligible_for_print=False,
            eligible_for_mail_move=False,
            decision_reasons=list(outcome.decision_reasons)
            + ["Print phase is incomplete or uncertain; downstream mail moves are blocked."],
        )
        if (outcome.eligible_for_print or outcome.eligible_for_mail_move)
        else outcome
        for outcome in mail_outcomes
    ]


def _build_print_discrepancy(
    *,
    run_report: RunReport,
    code: str,
    message: str,
    details: dict,
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
        details={"non_rule_source": "print_execution", **details},
    )


def _persist_run_report(
    persistor: Callable[[RunReport], None] | None,
    run_report: RunReport,
) -> None:
    if persistor is not None:
        persistor(run_report)


def summarize_print_batch_manual_verification(print_batches: list[PrintBatch]) -> dict[str, int]:
    verified_count = 0
    pending_count = 0
    untracked_count = 0
    document_count = 0
    for batch in print_batches:
        summary = batch.manual_verification_summary
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


def _manual_verification_execution_reasons(batch: PrintBatch) -> list[str]:
    summary = batch.manual_verification_summary
    if not summary:
        return []
    return [
        "Manual PDF verification status at print time: "
        f"{summary.get('verified_count', 0)}/{summary.get('document_count', 0)} verified, "
        f"{summary.get('pending_count', 0)} pending, {summary.get('untracked_count', 0)} untracked."
    ]
