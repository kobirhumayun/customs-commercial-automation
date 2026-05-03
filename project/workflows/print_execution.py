from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any, Callable

from project.models import (
    DiscrepancyReport,
    FinalDecision,
    MailOutcomeRecord,
    MailProcessingStatus,
    PrintBatch,
    PrintPhaseStatus,
    RunReport,
    WorkflowId,
)
from project.printing import PartialPrintExecutionError, PrintAdapterUnavailableError, PrintCommandReceipt, PrintGroupReceipt, PrintProvider
from project.storage import RunArtifactPaths
from project.storage.artifacts import write_json
from project.utils.json import to_jsonable
from project.utils.time import utc_timestamp
from project.workflows.print_annotation import PrintAnnotationChecklistError, validate_print_annotation_checklist


def execute_print_batches(
    *,
    run_report: RunReport,
    mail_outcomes: list[MailOutcomeRecord],
    print_batches: list[PrintBatch],
    artifact_paths: RunArtifactPaths,
    provider: PrintProvider,
    run_report_persistor: Callable[[RunReport], None] | None = None,
) -> tuple[RunReport, list[MailOutcomeRecord], list[DiscrepancyReport]]:
    if run_report.print_phase_status not in {
        PrintPhaseStatus.PLANNED,
        PrintPhaseStatus.PRINTING,
        PrintPhaseStatus.UNCERTAIN_INCOMPLETE,
    }:
        raise ValueError("Print execution requires print_phase_status=planned, printing, or uncertain_incomplete.")

    discrepancies: list[DiscrepancyReport] = []
    if run_report.workflow_id == WorkflowId.UD_IP_EXP:
        try:
            validate_print_annotation_checklist(
                artifact_paths=artifact_paths,
                run_report=run_report,
                print_batches=print_batches,
                mail_outcomes=mail_outcomes,
            )
        except PrintAnnotationChecklistError as exc:
            hard_blocked_report = replace(run_report, print_phase_status=PrintPhaseStatus.HARD_BLOCKED)
            _persist_run_report(run_report_persistor, hard_blocked_report)
            discrepancies.append(
                _build_print_discrepancy(
                    run_report=run_report,
                    code=exc.code,
                    message=str(exc),
                    details=exc.details,
                    mail_id=None,
                )
            )
            return hard_blocked_report, _block_print_mail_moves(mail_outcomes), discrepancies
    printing_report = replace(run_report, print_phase_status=PrintPhaseStatus.PRINTING)
    _persist_run_report(run_report_persistor, printing_report)

    try:
        for batch_index, batch in enumerate(print_batches):
            marker_path = artifact_paths.print_markers_dir / f"{batch.print_group_id}.json"
            marker_state, marker_payload = _check_existing_marker(marker_path, batch)
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
            resumable_batch, prior_partial_payload = _build_resumable_print_batch(
                batch=batch,
                marker_payload=marker_payload,
            )
            try:
                execution_receipt = provider.print_group(
                    resumable_batch,
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
            except PartialPrintExecutionError as exc:
                cumulative_receipt = _merge_group_receipts(
                    prior_partial_payload=prior_partial_payload,
                    new_receipt=PrintGroupReceipt(
                        adapter_name=_infer_adapter_name(exc.completed_command_receipts),
                        acknowledgment_mode=_infer_acknowledgment_mode(exc.completed_command_receipts),
                        executed_command_count=len(exc.completed_command_receipts),
                        blank_separator_printed=exc.blank_separator_printed,
                        command_receipts=list(exc.completed_command_receipts),
                    ),
                )
                printed_document_hashes = _printed_document_hashes_for_receipt(
                    batch=batch,
                    prior_partial_payload=prior_partial_payload,
                    receipt=cumulative_receipt,
                )
                write_json(
                    marker_path,
                    {
                        "print_group_id": batch.print_group_id,
                        "completion_marker_id": batch.completion_marker_id,
                        "run_id": batch.run_id,
                        "mail_id": batch.mail_id,
                        "document_path_hashes": list(batch.document_path_hashes),
                        "printed_document_path_hashes": printed_document_hashes,
                        "print_status": "partial_incomplete",
                        "blank_separator_printed": cumulative_receipt.blank_separator_printed,
                        "manual_verification_summary": dict(batch.manual_verification_summary),
                        "print_execution_receipt": to_jsonable(cumulative_receipt),
                        "printed_at_utc": utc_timestamp(),
                    },
                )
                uncertain_report = replace(printing_report, print_phase_status=PrintPhaseStatus.UNCERTAIN_INCOMPLETE)
                _persist_run_report(run_report_persistor, uncertain_report)
                discrepancies.append(
                    _build_print_discrepancy(
                        run_report=run_report,
                        code="print_group_runtime_error",
                        message="Print execution was interrupted for a planned print group.",
                        details={
                            "print_group_id": batch.print_group_id,
                            "error": str(exc),
                            "printed_document_count": len(printed_document_hashes),
                        },
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

            cumulative_receipt = _merge_group_receipts(
                prior_partial_payload=prior_partial_payload,
                new_receipt=execution_receipt,
            )
            write_json(
                marker_path,
                {
                    "print_group_id": batch.print_group_id,
                    "completion_marker_id": batch.completion_marker_id,
                    "run_id": batch.run_id,
                    "mail_id": batch.mail_id,
                    "document_path_hashes": list(batch.document_path_hashes),
                    "printed_document_path_hashes": list(batch.document_path_hashes),
                    "print_status": "completed",
                    "blank_separator_printed": cumulative_receipt.blank_separator_printed,
                    "manual_verification_summary": dict(batch.manual_verification_summary),
                    "print_execution_receipt": to_jsonable(cumulative_receipt),
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


def _check_existing_marker(path: Path, batch: PrintBatch) -> tuple[str, dict | None]:
    if not path.exists():
        return ("missing", None)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if str(payload.get("completion_marker_id", "")).strip() != batch.completion_marker_id:
        return ("mismatch", payload)
    print_status = str(payload.get("print_status", "")).strip()
    if print_status in {"", "completed"}:
        return ("matched", payload)
    if print_status != "partial_incomplete":
        return ("mismatch", payload)
    printed_hashes = payload.get("printed_document_path_hashes", [])
    if not isinstance(printed_hashes, list):
        return ("mismatch", payload)
    normalized_hashes = [str(item).strip() for item in printed_hashes]
    if normalized_hashes != list(batch.document_path_hashes[: len(normalized_hashes)]):
        return ("mismatch", payload)
    if len(normalized_hashes) > len(batch.document_path_hashes):
        return ("mismatch", payload)
    return ("partial_resumable", payload)


def _build_resumable_print_batch(
    *,
    batch: PrintBatch,
    marker_payload: dict | None,
) -> tuple[PrintBatch, dict | None]:
    if not isinstance(marker_payload, dict):
        return (batch, None)
    if str(marker_payload.get("print_status", "")).strip() != "partial_incomplete":
        return (batch, None)
    printed_hashes = [
        str(item).strip()
        for item in marker_payload.get("printed_document_path_hashes", [])
        if str(item).strip()
    ]
    remaining_index = len(printed_hashes)
    return (
        replace(
            batch,
            document_paths=list(batch.document_paths[remaining_index:]),
            document_path_hashes=list(batch.document_path_hashes[remaining_index:]),
        ),
        marker_payload,
    )


def _merge_group_receipts(
    *,
    prior_partial_payload: dict | None,
    new_receipt: PrintGroupReceipt,
) -> PrintGroupReceipt:
    prior_receipt_payload = (
        prior_partial_payload.get("print_execution_receipt")
        if isinstance(prior_partial_payload, dict)
        else None
    )
    prior_command_receipts = _parse_command_receipts(prior_receipt_payload)
    command_receipts = prior_command_receipts + list(new_receipt.command_receipts)
    return PrintGroupReceipt(
        adapter_name=new_receipt.adapter_name or _infer_adapter_name(prior_command_receipts),
        acknowledgment_mode=new_receipt.acknowledgment_mode or _infer_acknowledgment_mode(prior_command_receipts),
        executed_command_count=len(command_receipts),
        blank_separator_printed=(
            bool(prior_partial_payload.get("blank_separator_printed", False))
            if isinstance(prior_partial_payload, dict)
            else False
        )
        or new_receipt.blank_separator_printed,
        command_receipts=command_receipts,
    )


def _parse_command_receipts(payload: object) -> list[PrintCommandReceipt]:
    if not isinstance(payload, dict):
        return []
    raw_receipts = payload.get("command_receipts", [])
    if not isinstance(raw_receipts, list):
        return []
    receipts: list[PrintCommandReceipt] = []
    for item in raw_receipts:
        if not isinstance(item, dict):
            continue
        receipts.append(
            PrintCommandReceipt(
                adapter_name=str(item.get("adapter_name", "")),
                document_path=str(item.get("document_path", "")),
                command=[str(part) for part in item.get("command", [])] if isinstance(item.get("command", []), list) else [],
                started_at_utc=str(item.get("started_at_utc", "")),
                completed_at_utc=str(item.get("completed_at_utc", "")),
                elapsed_ms=int(item.get("elapsed_ms", 0) or 0),
                returncode=item.get("returncode"),
                stdout_excerpt=item.get("stdout_excerpt"),
                stderr_excerpt=item.get("stderr_excerpt"),
                acknowledgment_mode=str(item.get("acknowledgment_mode", "")),
                blank_separator=bool(item.get("blank_separator", False)),
            )
        )
    return receipts


def _printed_document_hashes_for_receipt(
    *,
    batch: PrintBatch,
    prior_partial_payload: dict | None,
    receipt: PrintGroupReceipt,
) -> list[str]:
    printed_hashes = [
        str(item).strip()
        for item in (
            prior_partial_payload.get("printed_document_path_hashes", [])
            if isinstance(prior_partial_payload, dict)
            else []
        )
        if str(item).strip()
    ]
    for command_receipt in receipt.command_receipts:
        if command_receipt.blank_separator:
            continue
        try:
            index = batch.document_paths.index(command_receipt.document_path)
        except ValueError:
            continue
        document_hash = batch.document_path_hashes[index]
        if document_hash not in printed_hashes:
            printed_hashes.append(document_hash)
    return printed_hashes


def _infer_adapter_name(receipts: list[PrintCommandReceipt]) -> str:
    for receipt in receipts:
        if receipt.adapter_name:
            return receipt.adapter_name
    return ""


def _infer_acknowledgment_mode(receipts: list[PrintCommandReceipt]) -> str:
    for receipt in receipts:
        if receipt.acknowledgment_mode:
            return receipt.acknowledgment_mode
    return ""


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
                eligible_for_mail_move=True,
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


def acknowledge_partial_print_progress(
    *,
    artifact_paths: RunArtifactPaths,
    print_batches: list[PrintBatch],
    print_group_id: str | None,
    printed_count: int,
) -> dict[str, Any]:
    if printed_count < 1:
        raise ValueError("Printed document count must be at least 1.")

    partial_batches: list[tuple[PrintBatch, Path, dict[str, Any]]] = []
    requested_batch: PrintBatch | None = None
    requested_marker_path: Path | None = None
    requested_payload: dict[str, Any] | None = None

    for batch in print_batches:
        marker_path = artifact_paths.print_markers_dir / f"{batch.print_group_id}.json"
        marker_state, marker_payload = _check_existing_marker(marker_path, batch)
        if batch.print_group_id == print_group_id:
            requested_batch = batch
            requested_marker_path = marker_path
            if isinstance(marker_payload, dict):
                requested_payload = marker_payload
            requested_state = marker_state
        if marker_state == "partial_resumable" and isinstance(marker_payload, dict):
            partial_batches.append((batch, marker_path, marker_payload))

    if print_group_id is not None:
        if requested_batch is None or requested_marker_path is None:
            raise ValueError(f"Unknown print group id: {print_group_id}")
        if requested_state != "partial_resumable" or requested_payload is None:
            raise ValueError(
                f"Print group {print_group_id} is not in a resumable partial-print state."
            )
        batch = requested_batch
        marker_path = requested_marker_path
        payload = requested_payload
    else:
        if not partial_batches:
            raise ValueError("No resumable partial-print markers were found for this run.")
        if len(partial_batches) > 1:
            raise ValueError(
                "Multiple resumable partial-print groups were found; provide --print-group-id."
            )
        batch, marker_path, payload = partial_batches[0]

    current_count = len(
        [
            str(item).strip()
            for item in payload.get("printed_document_path_hashes", [])
            if str(item).strip()
        ]
    )
    total_count = len(batch.document_path_hashes)
    if printed_count <= current_count:
        raise ValueError(
            f"Printed document count must be greater than the recorded partial progress ({current_count})."
        )
    updated_payload = dict(payload)
    updated_payload["printed_document_path_hashes"] = list(batch.document_path_hashes[:printed_count])
    updated_payload["operator_acknowledged_partial_print"] = True
    updated_payload["operator_acknowledged_at_utc"] = utc_timestamp()
    updated_payload["operator_acknowledged_printed_document_count"] = printed_count
    updated_payload["print_status"] = "completed" if printed_count == total_count else "partial_incomplete"
    updated_payload["printed_at_utc"] = utc_timestamp()
    write_json(marker_path, updated_payload)

    return {
        "print_group_id": batch.print_group_id,
        "mail_id": batch.mail_id,
        "marker_path": str(marker_path),
        "print_status": updated_payload["print_status"],
        "acknowledged_printed_document_count": printed_count,
        "previous_recorded_printed_document_count": current_count,
        "remaining_document_count": total_count - printed_count,
        "acknowledged_document_paths": list(batch.document_paths[:printed_count]),
        "remaining_document_paths": list(batch.document_paths[printed_count:]),
    }
