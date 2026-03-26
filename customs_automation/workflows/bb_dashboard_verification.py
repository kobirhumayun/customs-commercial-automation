from __future__ import annotations

from customs_automation.core.contracts import Decision, DiscrepancyEntry, EmailMessage, WorkflowMailOutcome, WorkflowRunOutcome
from customs_automation.core.run_state import RunContext

RULE_PACK_ID = "bb_dashboard_verification.default"
RULE_PACK_VERSION = "1.0.0"
APPLIED_RULE_IDS = ["core.cli.bootstrap.v1", "bb_dashboard_verification.bootstrap.stub.v1"]


def run(context: RunContext, messages: list[EmailMessage]) -> WorkflowRunOutcome:
    """Explicit placeholder until Bangladesh Bank dashboard flow is implemented."""
    _ = context
    outcomes = [
        WorkflowMailOutcome(
            entry_id=message.entry_id,
            decision=Decision.HARD_BLOCK,
            discrepancies=[
                DiscrepancyEntry(
                    code="workflow_not_implemented",
                    severity=Decision.HARD_BLOCK,
                    message="Bangladesh Bank dashboard verification is not yet implemented.",
                )
            ],
        )
        for message in messages
    ]
    return WorkflowRunOutcome(exit_code=2, mail_outcomes=outcomes)
