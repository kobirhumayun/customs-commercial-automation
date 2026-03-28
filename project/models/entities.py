from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from project.models.enums import (
    FinalDecision,
    MailMovePhaseStatus,
    PrintPhaseStatus,
    WorkflowId,
    WritePhaseStatus,
)
from project.reporting.schemas import (
    DISCREPANCY_REPORT_SCHEMA_ID,
    MAIL_REPORT_SCHEMA_ID,
    REPORT_SCHEMA_VERSION,
    RUN_REPORT_SCHEMA_ID,
)


@dataclass(slots=True, frozen=True)
class ProcessingJob:
    run_id: str
    workflow_id: WorkflowId
    started_at_utc: str
    operator_id: str
    mail_iteration_order: list[str]
    hash_algorithm: str
    run_start_backup_hash: str
    staged_write_plan_hash: str | None
    write_phase_status: WritePhaseStatus
    print_phase_status: PrintPhaseStatus
    mail_move_phase_status: MailMovePhaseStatus


@dataclass(slots=True, frozen=True)
class EmailMessage:
    mail_id: str
    entry_id: str
    received_time_utc: str
    received_time_workflow_tz: str
    subject_raw: str
    sender_address: str
    snapshot_index: int


@dataclass(slots=True, frozen=True)
class SavedDocument:
    saved_document_id: str
    mail_id: str
    attachment_name: str
    normalized_filename: str
    destination_path: str
    file_sha256: str
    save_decision: str


@dataclass(slots=True, frozen=True)
class WriteOperation:
    write_operation_id: str
    run_id: str
    mail_id: str
    operation_index_within_mail: int
    sheet_name: str
    row_index: int
    column_key: str
    expected_pre_write_value: str | int | float | None
    expected_post_write_value: str | int | float | None
    row_eligibility_checks: list[str]


@dataclass(slots=True, frozen=True)
class PrintBatch:
    print_group_id: str
    run_id: str
    mail_id: str
    print_group_index: int
    document_path_hashes: list[str]
    completion_marker_id: str


@dataclass(slots=True, frozen=True)
class MailMoveOperation:
    mail_move_operation_id: str
    run_id: str
    mail_id: str
    entry_id: str
    source_folder: str
    destination_folder: str
    moved_at_utc: str | None
    move_status: str


@dataclass(slots=True, frozen=True)
class DiscrepancyReport:
    run_id: str
    workflow_id: WorkflowId
    severity: FinalDecision
    code: str
    message: str
    created_at_utc: str
    mail_id: str | None = None
    rule_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    schema_id: str = DISCREPANCY_REPORT_SCHEMA_ID
    schema_version: str = REPORT_SCHEMA_VERSION
    report_schema_version: str = REPORT_SCHEMA_VERSION


@dataclass(slots=True, frozen=True)
class MailReport:
    run_id: str
    mail_id: str
    workflow_id: WorkflowId
    rule_pack_id: str
    rule_pack_version: str
    applied_rule_ids: list[str]
    final_decision: FinalDecision
    decision_reasons: list[str]
    file_numbers_extracted: list[str]
    saved_documents: list[dict[str, Any]]
    staged_write_operations: list[dict[str, Any]]
    discrepancies: list[dict[str, Any]]
    import_keyword_revision: str | None = None
    print_group_id: str | None = None
    mail_move_operation_id: str | None = None
    schema_id: str = MAIL_REPORT_SCHEMA_ID
    schema_version: str = REPORT_SCHEMA_VERSION
    report_schema_version: str = REPORT_SCHEMA_VERSION


@dataclass(slots=True, frozen=True)
class RunReport:
    run_id: str
    workflow_id: WorkflowId
    tool_version: str
    rule_pack_id: str
    rule_pack_version: str
    started_at_utc: str
    completed_at_utc: str | None
    state_timezone: str
    mail_iteration_order: list[str]
    print_group_order: list[str]
    write_phase_status: WritePhaseStatus
    print_phase_status: PrintPhaseStatus
    mail_move_phase_status: MailMovePhaseStatus
    hash_algorithm: str
    run_start_backup_hash: str
    current_workbook_hash: str
    staged_write_plan_hash: str
    summary: dict[str, int]
    schema_id: str = RUN_REPORT_SCHEMA_ID
    schema_version: str = REPORT_SCHEMA_VERSION
    report_schema_version: str = REPORT_SCHEMA_VERSION
