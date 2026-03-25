from __future__ import annotations

from customs_automation.core.run_state import RunContext

RULE_PACK_ID = "import_btb_lc.default"
RULE_PACK_VERSION = "1.0.0"
APPLIED_RULE_IDS = ["core.cli.bootstrap.v1", "import_btb_lc.bootstrap.stub.v1"]


def run(context: RunContext) -> int:
    """Workflow stub for import BTB LC orchestration."""
    _ = context
    return 0
