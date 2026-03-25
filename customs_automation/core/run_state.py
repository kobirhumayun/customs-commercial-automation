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
    )
