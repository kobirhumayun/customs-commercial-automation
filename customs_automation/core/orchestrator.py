from __future__ import annotations

from dataclasses import dataclass

from customs_automation.core.contracts import Decision, WritePhaseStatus
from customs_automation.core.intake import IntakeAdapter
from customs_automation.core.phases import transition_write_phase
from customs_automation.core.reporting import RunReport, build_run_report
from customs_automation.core.run_snapshot import build_run_snapshot
from customs_automation.core.run_state import (
    RunContext,
    RunStateRecord,
    RunStateStore,
    with_mail_iteration_order,
    with_write_phase_status,
)


@dataclass(frozen=True, slots=True)
class OrchestrationResult:
    exit_code: int
    run_state: RunStateRecord
    run_report: RunReport



def execute_workflow_run(
    *,
    context: RunContext,
    run_state_store: RunStateStore,
    intake_adapter: IntakeAdapter,
    applied_rule_ids: list[str],
    workflow_exit_code: int,
) -> OrchestrationResult:
    messages = intake_adapter.list_working_messages()
    snapshot = build_run_snapshot(context.run_id, context.workflow_id, messages)

    state = run_state_store.read_state(context.run_id)
    state = with_mail_iteration_order(state, snapshot.ordered_mail_ids)

    write_phase = WritePhaseStatus(state.write_phase_status)
    write_phase = transition_write_phase(write_phase, WritePhaseStatus.PREVALIDATING_TARGETS)
    write_phase = transition_write_phase(write_phase, WritePhaseStatus.PREVALIDATED)
    state = with_write_phase_status(state, write_phase)
    run_state_store.update_state(state)

    run_report = build_run_report(
        run_id=context.run_id,
        workflow_id=context.workflow_id,
        rule_pack_id=context.rule_pack_id,
        rule_pack_version=context.rule_pack_version,
        applied_rule_ids=applied_rule_ids,
        final_decision=Decision.PASS if workflow_exit_code == 0 else Decision.HARD_BLOCK,
    )
    return OrchestrationResult(exit_code=workflow_exit_code, run_state=state, run_report=run_report)
