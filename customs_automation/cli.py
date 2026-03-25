from __future__ import annotations

import argparse

from customs_automation.core.contracts import Decision
from customs_automation.core.reporting import ReportWriter, build_run_report
from customs_automation.core.rule_pack import validate_rule_pack_version
from customs_automation.core.run_state import (
    RunContext,
    RunStateStore,
    generate_run_id,
    new_run_state_record,
)
from customs_automation.workflows import (
    bb_dashboard_verification,
    export_lc_sc,
    import_btb_lc,
    ud_ip_exp,
)

WORKFLOW_HANDLERS = {
    "export-lc-sc": export_lc_sc,
    "ud-ip-exp": ud_ip_exp,
    "import-btb-lc": import_btb_lc,
    "bb-dashboard-verification": bb_dashboard_verification,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="customs-commercial-automation",
        description="Manually triggered CLI tools for customs/commercial workflows.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in WORKFLOW_HANDLERS:
        subparsers.add_parser(command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    workflow_module = WORKFLOW_HANDLERS[args.command]
    validate_rule_pack_version(workflow_module.RULE_PACK_VERSION)

    run_context = RunContext(
        run_id=generate_run_id(),
        workflow_id=args.command.replace("-", "_"),
        rule_pack_id=workflow_module.RULE_PACK_ID,
        rule_pack_version=workflow_module.RULE_PACK_VERSION,
    )

    run_state_store = RunStateStore()
    run_record = new_run_state_record(run_context)
    run_dir = run_state_store.run_dir(run_context.run_id)
    run_state_store.write_state(run_record)

    exit_code = workflow_module.run(run_context)

    run_report = build_run_report(
        run_id=run_context.run_id,
        workflow_id=run_context.workflow_id,
        rule_pack_id=run_context.rule_pack_id,
        rule_pack_version=run_context.rule_pack_version,
        applied_rule_ids=["core.cli.bootstrap.v1"],
        final_decision=Decision.PASS if exit_code == 0 else Decision.HARD_BLOCK,
    )
    ReportWriter(run_dir).write_run_report(run_report)

    return exit_code
