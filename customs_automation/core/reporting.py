from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from customs_automation.core.contracts import Decision
from customs_automation.core.rule_pack import validate_applied_rule_ids


@dataclass(frozen=True, slots=True)
class MailReport:
    run_id: str
    workflow_id: str
    rule_pack_id: str
    rule_pack_version: str
    mail_id: str
    applied_rule_ids: list[str]
    final_decision: str


@dataclass(frozen=True, slots=True)
class RunReport:
    run_id: str
    workflow_id: str
    rule_pack_id: str
    rule_pack_version: str
    applied_rule_ids: list[str]
    final_decision: str


class ReportWriter:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    def write_run_report(self, report: RunReport) -> Path:
        return self._write("run_report.json", asdict(report))

    def write_mail_report(self, report: MailReport) -> Path:
        return self._write(f"mail_{report.mail_id}.json", asdict(report))

    def _write(self, name: str, payload: dict) -> Path:
        path = self.run_dir / name
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path


def build_run_report(
    *,
    run_id: str,
    workflow_id: str,
    rule_pack_id: str,
    rule_pack_version: str,
    applied_rule_ids: list[str],
    final_decision: Decision,
) -> RunReport:
    validate_applied_rule_ids(applied_rule_ids)
    return RunReport(
        run_id=run_id,
        workflow_id=workflow_id,
        rule_pack_id=rule_pack_id,
        rule_pack_version=rule_pack_version,
        applied_rule_ids=applied_rule_ids,
        final_decision=final_decision.value,
    )


def build_mail_report(
    *,
    run_id: str,
    workflow_id: str,
    rule_pack_id: str,
    rule_pack_version: str,
    mail_id: str,
    applied_rule_ids: list[str],
    final_decision: Decision,
) -> MailReport:
    validate_applied_rule_ids(applied_rule_ids)
    return MailReport(
        run_id=run_id,
        workflow_id=workflow_id,
        rule_pack_id=rule_pack_id,
        rule_pack_version=rule_pack_version,
        mail_id=mail_id,
        applied_rule_ids=applied_rule_ids,
        final_decision=final_decision.value,
    )
