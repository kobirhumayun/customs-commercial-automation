from __future__ import annotations

from customs_automation.core.run_state import RunContext

RULE_PACK_ID = "export_lc_sc.default"
RULE_PACK_VERSION = "1.0.0"
APPLIED_RULE_IDS = ["core.cli.bootstrap.v1", "export_lc_sc.bootstrap.stub.v1"]


def run(context: RunContext) -> int:
    """Workflow stub for export LC/SC intake orchestration."""
    _ = context
    return 0
