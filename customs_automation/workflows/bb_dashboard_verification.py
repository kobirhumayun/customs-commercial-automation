from __future__ import annotations

from customs_automation.core.run_state import RunContext

RULE_PACK_ID = "bb_dashboard_verification.default"
RULE_PACK_VERSION = "1.0.0"
APPLIED_RULE_IDS = ["core.cli.bootstrap.v1", "bb_dashboard_verification.bootstrap.stub.v1"]


def run(context: RunContext) -> int:
    """Workflow stub for Bangladesh Bank dashboard verification orchestration."""
    _ = context
    return 0
