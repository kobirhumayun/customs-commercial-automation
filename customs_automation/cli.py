from __future__ import annotations

import argparse
from pathlib import Path

from customs_automation.core.console import format_run_summary
from customs_automation.core.intake import JsonFileIntakeAdapter, StaticIntakeAdapter
from customs_automation.core.orchestrator import execute_workflow_run
from customs_automation.core.reporting import ReportWriter
from customs_automation.core.rule_pack import validate_rule_pack_version
from customs_automation.core.rule_registry import load_rule_registry, validate_rule_ids_registered
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
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument(
            "--snapshot-input",
            type=Path,
            default=None,
            help="Optional JSON file containing source mail snapshot rows for deterministic local runs.",
        )
        command_parser.add_argument(
            "--artifacts-root",
            type=Path,
            default=Path("artifacts") / "runs",
            help="Directory where run artifacts are written (default: artifacts/runs).",
        )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    workflow_module = WORKFLOW_HANDLERS[args.command]
    validate_rule_pack_version(workflow_module.RULE_PACK_VERSION)
    registry = load_rule_registry()
    validate_rule_ids_registered(workflow_module.APPLIED_RULE_IDS, registry)

    run_context = RunContext(
        run_id=generate_run_id(),
        workflow_id=args.command.replace("-", "_"),
        rule_pack_id=workflow_module.RULE_PACK_ID,
        rule_pack_version=workflow_module.RULE_PACK_VERSION,
    )

    run_state_store = RunStateStore(base_dir=args.artifacts_root)
    run_state_store.write_state(new_run_state_record(run_context))

    workflow_exit_code = workflow_module.run(run_context)
    intake_adapter = (
        JsonFileIntakeAdapter(snapshot_path=args.snapshot_input)
        if args.snapshot_input is not None
        else StaticIntakeAdapter(messages=[])
    )

    orchestration_result = execute_workflow_run(
        context=run_context,
        run_state_store=run_state_store,
        intake_adapter=intake_adapter,
        applied_rule_ids=workflow_module.APPLIED_RULE_IDS,
        workflow_exit_code=workflow_exit_code,
    )

    run_dir = run_state_store.run_dir(run_context.run_id)
    report_writer = ReportWriter(run_dir)
    run_report_path = report_writer.write_run_report(orchestration_result.run_report)
    for mail_report in orchestration_result.mail_reports:
        report_writer.write_mail_report(mail_report)

    run_state_path = run_dir / "run_state.json"

    print(
        format_run_summary(
            run_id=run_context.run_id,
            workflow_id=run_context.workflow_id,
            decision=orchestration_result.run_report.final_decision,
            run_state_path=run_state_path,
            run_report_path=run_report_path,
            run_snapshot_path=orchestration_result.run_snapshot_path,
        )
    )
    return orchestration_result.exit_code
