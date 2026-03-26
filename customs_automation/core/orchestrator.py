from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from customs_automation.core.contracts import Decision, WorkflowMailOutcome, WritePhaseStatus
from customs_automation.core.intake import IntakeAdapter
from customs_automation.core.phases import transition_write_phase
from customs_automation.core.reporting import MailReport, RunReport, build_mail_report, build_run_report
from customs_automation.core.run_snapshot import build_run_snapshot, write_snapshot
from customs_automation.core.write_plan import compute_staged_write_plan_hash
from customs_automation.core.run_state import (
    RunContext,
    RunStateRecord,
    RunStateStore,
    with_mail_iteration_order,
    with_hash_metadata,
    with_print_group_order,
    with_write_phase_status,
)


@dataclass(frozen=True, slots=True)
class OrchestrationResult:
    exit_code: int
    run_state: RunStateRecord
    run_report: RunReport
    mail_reports: list[MailReport]
    run_snapshot_path: Path



def execute_workflow_run(
    *,
    context: RunContext,
    run_state_store: RunStateStore,
    intake_adapter: IntakeAdapter,
    applied_rule_ids: list[str],
    workflow_mail_outcomes: list[WorkflowMailOutcome],
) -> OrchestrationResult:
    messages = intake_adapter.list_working_messages()
    snapshot = build_run_snapshot(context.run_id, context.workflow_id, messages)
    run_dir = run_state_store.run_dir(context.run_id)
    snapshot_path = write_snapshot(snapshot, run_dir)

    state = run_state_store.read_state(context.run_id)
    state = with_mail_iteration_order(state, snapshot.ordered_mail_ids)
    state = with_print_group_order(state, snapshot.ordered_mail_ids)
    state = with_hash_metadata(
        state,
        run_start_backup_hash=state.run_start_backup_hash,
        current_workbook_hash=state.current_workbook_hash,
        staged_write_plan_hash=compute_staged_write_plan_hash([]),
    )

    write_phase = WritePhaseStatus(state.write_phase_status)
    write_phase = transition_write_phase(write_phase, WritePhaseStatus.PREVALIDATING_TARGETS)
    write_phase = transition_write_phase(write_phase, WritePhaseStatus.PREVALIDATED)
    state = with_write_phase_status(state, write_phase)
    run_state_store.update_state(state)

    outcome_by_entry_id = {outcome.entry_id: outcome for outcome in workflow_mail_outcomes}
    mail_reports: list[MailReport] = []
    for order_record in snapshot.mail_order_records:
        mail_outcome = outcome_by_entry_id.get(order_record.entry_id)
        if mail_outcome is None:
            mail_decision = Decision.HARD_BLOCK
            mail_discrepancies = []
        else:
            mail_decision = mail_outcome.decision
            mail_discrepancies = mail_outcome.discrepancies
        mail_reports.append(
            build_mail_report(
                run_id=context.run_id,
                workflow_id=context.workflow_id,
                rule_pack_id=context.rule_pack_id,
                rule_pack_version=context.rule_pack_version,
                mail_id=order_record.entry_id,
                applied_rule_ids=applied_rule_ids,
                final_decision=mail_decision,
                discrepancies=mail_discrepancies,
            )
        )

    decision = (
        Decision.HARD_BLOCK
        if any(report.final_decision == Decision.HARD_BLOCK.value for report in mail_reports)
        else Decision.PASS
    )
    run_report = build_run_report(
        run_id=context.run_id,
        workflow_id=context.workflow_id,
        rule_pack_id=context.rule_pack_id,
        rule_pack_version=context.rule_pack_version,
        applied_rule_ids=applied_rule_ids,
        final_decision=decision,
        mail_count=len(snapshot.mail_order_records),
    )
    return OrchestrationResult(
        exit_code=0 if decision == Decision.PASS else 2,
        run_state=state,
        run_report=run_report,
        mail_reports=mail_reports,
        run_snapshot_path=snapshot_path,
    )
