from __future__ import annotations

import re

from customs_automation.core.contracts import Decision, DiscrepancyEntry, EmailMessage, WorkflowMailOutcome, WorkflowRunOutcome
from customs_automation.core.run_state import RunContext

RULE_PACK_ID = "export_lc_sc.default"
RULE_PACK_VERSION = "1.0.0"
APPLIED_RULE_IDS = ["core.cli.bootstrap.v1", "export_lc_sc.bootstrap.stub.v1"]
LC_SC_SUBJECT_PATTERN = re.compile(r"\b(lc|sc)\b", re.IGNORECASE)


def run(context: RunContext, messages: list[EmailMessage]) -> WorkflowRunOutcome:
    """Minimal deterministic validation for export LC/SC intake workflow."""
    _ = context
    mail_outcomes: list[WorkflowMailOutcome] = []
    for message in messages:
        if LC_SC_SUBJECT_PATTERN.search(message.subject):
            mail_outcomes.append(
                WorkflowMailOutcome(
                    entry_id=message.entry_id,
                    decision=Decision.PASS,
                    discrepancies=[],
                )
            )
            continue

        mail_outcomes.append(
            WorkflowMailOutcome(
                entry_id=message.entry_id,
                decision=Decision.HARD_BLOCK,
                discrepancies=[
                    DiscrepancyEntry(
                        code="export_subject_missing_lc_sc",
                        severity=Decision.HARD_BLOCK,
                        message="Subject must contain LC or SC token for export workflow.",
                    )
                ],
            )
        )

    exit_code = 0 if all(outcome.decision != Decision.HARD_BLOCK for outcome in mail_outcomes) else 2
    return WorkflowRunOutcome(exit_code=exit_code, mail_outcomes=mail_outcomes)
