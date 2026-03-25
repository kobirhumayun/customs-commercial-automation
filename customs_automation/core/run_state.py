from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from customs_automation.core.contracts import (
    MailMovePhaseStatus,
    PrintPhaseStatus,
    WritePhaseStatus,
)
from customs_automation.core.hashing import SHA256_ALGORITHM


@dataclass(frozen=True, slots=True)
class RunContext:
    run_id: str
    workflow_id: str
    rule_pack_id: str
    rule_pack_version: str


@dataclass(frozen=True, slots=True)
class RunStateRecord:
    run_id: str
    workflow_id: str
    rule_pack_id: str
    rule_pack_version: str
    created_at_utc: str
    write_phase_status: str
    print_phase_status: str
    mail_move_phase_status: str
    mail_iteration_order: list[str]
    print_group_order: list[str]
    hash_algorithm: str
    run_start_backup_hash: str | None
    current_workbook_hash: str | None
    staged_write_plan_hash: str | None


class RunStateStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path("artifacts") / "runs"

    def run_dir(self, run_id: str) -> Path:
        path = self.base_dir / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_state(self, record: RunStateRecord) -> Path:
        target = self.run_dir(record.run_id) / "run_state.json"
        target.write_text(json.dumps(asdict(record), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return target

    def read_state(self, run_id: str) -> RunStateRecord:
        payload = json.loads((self.run_dir(run_id) / "run_state.json").read_text(encoding="utf-8"))
        return RunStateRecord(**payload)

    def update_state(self, record: RunStateRecord) -> Path:
        return self.write_state(record)


def generate_run_id(now: datetime | None = None) -> str:
    now_utc = (now or datetime.now(tz=UTC)).astimezone(UTC)
    return f"run-{now_utc.strftime('%Y%m%dT%H%M%SZ')}"


def new_run_state_record(context: RunContext) -> RunStateRecord:
    return RunStateRecord(
        run_id=context.run_id,
        workflow_id=context.workflow_id,
        rule_pack_id=context.rule_pack_id,
        rule_pack_version=context.rule_pack_version,
        created_at_utc=datetime.now(tz=UTC).isoformat(),
        write_phase_status=WritePhaseStatus.NOT_STARTED.value,
        print_phase_status=PrintPhaseStatus.NOT_STARTED.value,
        mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED.value,
        mail_iteration_order=[],
        print_group_order=[],
        hash_algorithm=SHA256_ALGORITHM,
        run_start_backup_hash=None,
        current_workbook_hash=None,
        staged_write_plan_hash=None,
    )


def with_mail_iteration_order(record: RunStateRecord, ordered_mail_ids: list[str]) -> RunStateRecord:
    return RunStateRecord(
        run_id=record.run_id,
        workflow_id=record.workflow_id,
        rule_pack_id=record.rule_pack_id,
        rule_pack_version=record.rule_pack_version,
        created_at_utc=record.created_at_utc,
        write_phase_status=record.write_phase_status,
        print_phase_status=record.print_phase_status,
        mail_move_phase_status=record.mail_move_phase_status,
        mail_iteration_order=ordered_mail_ids,
        print_group_order=record.print_group_order,
        hash_algorithm=record.hash_algorithm,
        run_start_backup_hash=record.run_start_backup_hash,
        current_workbook_hash=record.current_workbook_hash,
        staged_write_plan_hash=record.staged_write_plan_hash,
    )


def with_write_phase_status(record: RunStateRecord, write_phase_status: WritePhaseStatus) -> RunStateRecord:
    return RunStateRecord(
        run_id=record.run_id,
        workflow_id=record.workflow_id,
        rule_pack_id=record.rule_pack_id,
        rule_pack_version=record.rule_pack_version,
        created_at_utc=record.created_at_utc,
        write_phase_status=write_phase_status.value,
        print_phase_status=record.print_phase_status,
        mail_move_phase_status=record.mail_move_phase_status,
        mail_iteration_order=record.mail_iteration_order,
        print_group_order=record.print_group_order,
        hash_algorithm=record.hash_algorithm,
        run_start_backup_hash=record.run_start_backup_hash,
        current_workbook_hash=record.current_workbook_hash,
        staged_write_plan_hash=record.staged_write_plan_hash,
    )


def with_hash_metadata(
    record: RunStateRecord,
    *,
    run_start_backup_hash: str | None,
    current_workbook_hash: str | None,
    staged_write_plan_hash: str | None,
) -> RunStateRecord:
    return RunStateRecord(
        run_id=record.run_id,
        workflow_id=record.workflow_id,
        rule_pack_id=record.rule_pack_id,
        rule_pack_version=record.rule_pack_version,
        created_at_utc=record.created_at_utc,
        write_phase_status=record.write_phase_status,
        print_phase_status=record.print_phase_status,
        mail_move_phase_status=record.mail_move_phase_status,
        mail_iteration_order=record.mail_iteration_order,
        print_group_order=record.print_group_order,
        hash_algorithm=record.hash_algorithm,
        run_start_backup_hash=run_start_backup_hash,
        current_workbook_hash=current_workbook_hash,
        staged_write_plan_hash=staged_write_plan_hash,
    )


def with_print_group_order(record: RunStateRecord, print_group_order: list[str]) -> RunStateRecord:
    return RunStateRecord(
        run_id=record.run_id,
        workflow_id=record.workflow_id,
        rule_pack_id=record.rule_pack_id,
        rule_pack_version=record.rule_pack_version,
        created_at_utc=record.created_at_utc,
        write_phase_status=record.write_phase_status,
        print_phase_status=record.print_phase_status,
        mail_move_phase_status=record.mail_move_phase_status,
        mail_iteration_order=record.mail_iteration_order,
        print_group_order=print_group_order,
        hash_algorithm=record.hash_algorithm,
        run_start_backup_hash=record.run_start_backup_hash,
        current_workbook_hash=record.current_workbook_hash,
        staged_write_plan_hash=record.staged_write_plan_hash,
    )
