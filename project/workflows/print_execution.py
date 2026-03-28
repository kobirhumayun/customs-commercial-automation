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
from project.printing import PrintProvider
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
    printed_mail_ids = {batch.mail_id for batch in print_batches}
    updated: list[MailOutcomeRecord] = []
    for outcome in mail_outcomes:
        if outcome.mail_id not in printed_mail_ids:
            updated.append(outcome)
            continue
        updated.append(
            replace(
                outcome,
                processing_status=MailProcessingStatus.PRINTED,
                eligible_for_print=False,
                decision_reasons=list(outcome.decision_reasons) + ["Planned print group completed successfully."],
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
