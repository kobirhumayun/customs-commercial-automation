from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from project.models import (
    FinalDecision,
    MailOutcomeRecord,
    MailMovePhaseStatus,
    MailProcessingStatus,
    PrintBatch,
    PrintPhaseStatus,
    RunReport,
    WorkflowId,
    WriteOperation,
    WritePhaseStatus,
)
from project.utils.hashing import sha256_hex_text
from project.utils.ids import build_print_completion_marker_id, build_print_group_id


@dataclass(slots=True, frozen=True)
class PrintPlanningResult:
    run_report: RunReport
    mail_outcomes: list[MailOutcomeRecord]
    print_batches: list[PrintBatch]


def plan_print_batches(
    *,
    run_report: RunReport,
    mail_outcomes: list[MailOutcomeRecord],
    staged_write_plan: list[WriteOperation],
    recovery_outcome: str | None = None,
) -> PrintPlanningResult:
    if not _print_planning_gate_satisfied(run_report, recovery_outcome):
        raise ValueError(
            "Print planning requires write_phase_status=committed or a recovery outcome of safe_resume."
        )

    mail_order_index = {mail_id: index for index, mail_id in enumerate(run_report.mail_iteration_order)}
    earliest_written_row_by_mail = _build_earliest_written_row_index(staged_write_plan)

    candidates: list[tuple[int, int, MailOutcomeRecord]] = []
    for outcome in mail_outcomes:
        if outcome.final_decision == FinalDecision.HARD_BLOCK:
            continue
        if outcome.mail_id not in earliest_written_row_by_mail:
            continue
        if not _newly_saved_documents(outcome):
            continue
        candidates.append(
            (
                earliest_written_row_by_mail[outcome.mail_id],
                mail_order_index.get(outcome.mail_id, len(run_report.mail_iteration_order)),
                outcome,
            )
        )

    candidates.sort(key=lambda item: (item[0], item[1]))

    print_batches: list[PrintBatch] = []
    print_group_order: list[str] = []
    print_group_by_mail_id: dict[str, str] = {}
    for print_group_index, (_earliest_row, _mail_order, outcome) in enumerate(candidates):
        document_path_hashes = [
            sha256_hex_text(str(saved_document["destination_path"]))
            for saved_document in _newly_saved_documents(outcome)
        ]
        print_group_id = build_print_group_id(run_report.run_id, outcome.mail_id, print_group_index)
        print_batches.append(
            PrintBatch(
                print_group_id=print_group_id,
                run_id=run_report.run_id,
                mail_id=outcome.mail_id,
                print_group_index=print_group_index,
                document_paths=[
                    str(saved_document["destination_path"])
                    for saved_document in _newly_saved_documents(outcome)
                ],
                document_path_hashes=document_path_hashes,
                completion_marker_id=build_print_completion_marker_id(
                    run_report.run_id,
                    outcome.mail_id,
                    print_group_index,
                    document_path_hashes,
                ),
            )
        )
        print_group_order.append(print_group_id)
        print_group_by_mail_id[outcome.mail_id] = print_group_id

    updated_run_report = replace(
        run_report,
        print_group_order=print_group_order,
        print_phase_status=PrintPhaseStatus.PLANNED,
    )
    updated_mail_outcomes = [
        replace(
            outcome,
            print_group_id=print_group_by_mail_id.get(outcome.mail_id),
            eligible_for_print=(
                outcome.mail_id in print_group_by_mail_id and outcome.final_decision != FinalDecision.HARD_BLOCK
            ),
            decision_reasons=(
                list(outcome.decision_reasons)
                + [f"Planned print group {print_group_by_mail_id[outcome.mail_id]}."]
                if outcome.mail_id in print_group_by_mail_id
                else list(outcome.decision_reasons)
            ),
        )
        for outcome in mail_outcomes
    ]
    return PrintPlanningResult(
        run_report=updated_run_report,
        mail_outcomes=updated_mail_outcomes,
        print_batches=print_batches,
    )


def build_print_plan_payload(print_batches: list[PrintBatch]) -> dict[str, Any]:
    return {
        "print_groups": [
            {
                "print_group_id": batch.print_group_id,
                "run_id": batch.run_id,
                "mail_id": batch.mail_id,
                "print_group_index": batch.print_group_index,
                "document_paths": list(batch.document_paths),
                "document_path_hashes": list(batch.document_path_hashes),
                "completion_marker_id": batch.completion_marker_id,
                "blank_page_after_group": True,
            }
            for batch in print_batches
        ],
        "print_group_order": [batch.print_group_id for batch in print_batches],
        "blank_page_between_groups": 1,
    }


def load_print_planning_bundle(
    *,
    run_artifact_root: Path,
    workflow_id: WorkflowId,
    run_id: str,
) -> tuple[RunReport, list[MailOutcomeRecord], list[WriteOperation]]:
    run_dir = run_artifact_root / workflow_id.value / run_id
    run_report_payload = _load_json(run_dir / "run_metadata.json")
    mail_outcomes_payload = _load_jsonl(run_dir / "mail_outcomes.jsonl")
    staged_write_plan_payload = _load_json(run_dir / "staged_write_plan.json")
    if not isinstance(run_report_payload, dict):
        raise ValueError("Persisted run metadata must be a JSON object")
    if not isinstance(staged_write_plan_payload, list):
        raise ValueError("Persisted staged write plan must be a JSON array")
    return (
        _parse_run_report(run_report_payload),
        [_parse_mail_outcome(item) for item in mail_outcomes_payload],
        [_parse_write_operation(item) for item in staged_write_plan_payload],
    )


def _print_planning_gate_satisfied(run_report: RunReport, recovery_outcome: str | None) -> bool:
    return run_report.write_phase_status == WritePhaseStatus.COMMITTED or recovery_outcome == "safe_resume"


def _build_earliest_written_row_index(staged_write_plan: list[WriteOperation]) -> dict[str, int]:
    earliest_by_mail: dict[str, int] = {}
    for operation in staged_write_plan:
        current = earliest_by_mail.get(operation.mail_id)
        if current is None or operation.row_index < current:
            earliest_by_mail[operation.mail_id] = operation.row_index
    return earliest_by_mail


def _newly_saved_documents(outcome: MailOutcomeRecord) -> list[dict[str, Any]]:
    return [
        document
        for document in outcome.saved_documents
        if str(document.get("save_decision", "")).strip() == "saved_new"
        and str(document.get("destination_path", "")).strip()
    ]


def _load_json(path: Path) -> Any:
    import json

    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_jsonl(path: Path) -> list[Any]:
    import json

    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _parse_run_report(payload: dict[str, Any]) -> RunReport:
    return RunReport(
        run_id=str(payload["run_id"]),
        workflow_id=WorkflowId(str(payload["workflow_id"])),
        tool_version=str(payload["tool_version"]),
        rule_pack_id=str(payload["rule_pack_id"]),
        rule_pack_version=str(payload["rule_pack_version"]),
        started_at_utc=str(payload["started_at_utc"]),
        completed_at_utc=payload.get("completed_at_utc"),
        state_timezone=str(payload["state_timezone"]),
        mail_iteration_order=[str(value) for value in payload.get("mail_iteration_order", [])],
        print_group_order=[str(value) for value in payload.get("print_group_order", [])],
        write_phase_status=WritePhaseStatus(str(payload["write_phase_status"])),
        print_phase_status=PrintPhaseStatus(str(payload["print_phase_status"])),
        mail_move_phase_status=MailMovePhaseStatus(str(payload["mail_move_phase_status"])),
        hash_algorithm=str(payload["hash_algorithm"]),
        run_start_backup_hash=str(payload["run_start_backup_hash"]),
        current_workbook_hash=str(payload["current_workbook_hash"]),
        staged_write_plan_hash=str(payload["staged_write_plan_hash"]),
        summary={str(key): int(value) for key, value in dict(payload.get("summary", {})).items()},
        resolved_source_folder_entry_id=payload.get("resolved_source_folder_entry_id"),
        resolved_destination_folder_entry_id=payload.get("resolved_destination_folder_entry_id"),
        folder_resolution_mode=payload.get("folder_resolution_mode"),
    )


def _parse_mail_outcome(payload: dict[str, Any]) -> MailOutcomeRecord:
    return MailOutcomeRecord(
        run_id=str(payload["run_id"]),
        mail_id=str(payload["mail_id"]),
        workflow_id=WorkflowId(str(payload["workflow_id"])),
        snapshot_index=int(payload["snapshot_index"]),
        processing_status=MailProcessingStatus(str(payload["processing_status"])),
        final_decision=(
            None
            if payload.get("final_decision") is None
            else FinalDecision(str(payload["final_decision"]))
        ),
        decision_reasons=[str(value) for value in payload.get("decision_reasons", [])],
        eligible_for_write=bool(payload.get("eligible_for_write", False)),
        eligible_for_print=bool(payload.get("eligible_for_print", False)),
        eligible_for_mail_move=bool(payload.get("eligible_for_mail_move", False)),
        source_entry_id=str(payload["source_entry_id"]),
        subject_raw=str(payload["subject_raw"]),
        sender_address=str(payload["sender_address"]),
        rule_pack_id=payload.get("rule_pack_id"),
        rule_pack_version=payload.get("rule_pack_version"),
        applied_rule_ids=[str(value) for value in payload.get("applied_rule_ids", [])],
        discrepancies=list(payload.get("discrepancies", [])),
        file_numbers_extracted=[str(value) for value in payload.get("file_numbers_extracted", [])],
        saved_documents=list(payload.get("saved_documents", [])),
        staged_write_operations=list(payload.get("staged_write_operations", [])),
        import_keyword_revision=payload.get("import_keyword_revision"),
        print_group_id=payload.get("print_group_id"),
        mail_move_operation_id=payload.get("mail_move_operation_id"),
    )


def _parse_write_operation(payload: dict[str, Any]) -> WriteOperation:
    return WriteOperation(
        write_operation_id=str(payload["write_operation_id"]),
        run_id=str(payload["run_id"]),
        mail_id=str(payload["mail_id"]),
        operation_index_within_mail=int(payload["operation_index_within_mail"]),
        sheet_name=str(payload["sheet_name"]),
        row_index=int(payload["row_index"]),
        column_key=str(payload["column_key"]),
        expected_pre_write_value=payload.get("expected_pre_write_value"),
        expected_post_write_value=payload.get("expected_post_write_value"),
        row_eligibility_checks=[str(value) for value in payload.get("row_eligibility_checks", [])],
    )


def load_print_batches(
    *,
    run_artifact_root: Path,
    workflow_id: WorkflowId,
    run_id: str,
) -> list[PrintBatch]:
    run_dir = run_artifact_root / workflow_id.value / run_id
    payload = _load_json(run_dir / "print_plan.json")
    if not isinstance(payload, dict):
        raise ValueError("Persisted print plan must be a JSON object")
    groups_payload = payload.get("print_groups", [])
    if not isinstance(groups_payload, list):
        raise ValueError("Persisted print plan groups must be a JSON array")
    return [_parse_print_batch(item) for item in groups_payload]


def _parse_print_batch(payload: dict[str, Any]) -> PrintBatch:
    return PrintBatch(
        print_group_id=str(payload["print_group_id"]),
        run_id=str(payload["run_id"]),
        mail_id=str(payload["mail_id"]),
        print_group_index=int(payload["print_group_index"]),
        document_paths=[str(value) for value in payload.get("document_paths", [])],
        document_path_hashes=[str(value) for value in payload.get("document_path_hashes", [])],
        completion_marker_id=str(payload["completion_marker_id"]),
    )
