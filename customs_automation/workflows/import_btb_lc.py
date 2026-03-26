from __future__ import annotations

from customs_automation.core.contracts import Decision, DiscrepancyEntry, EmailMessage, WorkflowMailOutcome, WorkflowRunOutcome
from customs_automation.core.run_state import RunContext

RULE_PACK_ID = "import_btb_lc.default"
RULE_PACK_VERSION = "1.0.0"
APPLIED_RULE_IDS = ["core.cli.bootstrap.v1", "import_btb_lc.bootstrap.stub.v1"]


def run(context: RunContext, messages: list[EmailMessage]) -> WorkflowRunOutcome:
    """Explicit phase-1 placeholder until import BTB rule pack implementation lands."""
    _ = context
    outcomes = [
        WorkflowMailOutcome(
            entry_id=message.entry_id,
            decision=Decision.HARD_BLOCK,
            discrepancies=[
                DiscrepancyEntry(
                    code="workflow_not_implemented",
                    severity=Decision.HARD_BLOCK,
                    message="Import/BTB LC workflow is not yet implemented.",
                )
            ],
        )
        for message in messages
    ]
    return WorkflowRunOutcome(exit_code=2, mail_outcomes=outcomes)
