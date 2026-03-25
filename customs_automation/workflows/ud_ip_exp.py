from __future__ import annotations

from customs_automation.core.run_state import RunContext

RULE_PACK_ID = "ud_ip_exp.default"
RULE_PACK_VERSION = "1.0.0"
APPLIED_RULE_IDS = ["core.cli.bootstrap.v1", "ud_ip_exp.bootstrap.stub.v1"]


def run(context: RunContext) -> int:
    """Workflow stub for UD/IP/EXP orchestration."""
    _ = context
    return 0
