from __future__ import annotations

import argparse
import json
from pathlib import Path

from customs_automation.core.console import format_run_summary
from customs_automation.core.contracts import WritePhaseStatus
from customs_automation.core.intake import JsonFileIntakeAdapter, StaticIntakeAdapter
from customs_automation.core.orchestrator import execute_workflow_run
from customs_automation.core.recovery import ProbeClassification, RecoveryOutcome, evaluate_recovery_decision
from customs_automation.core.recovery_gate import find_blocking_prior_run
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

    recovery_parser = subparsers.add_parser("recovery-check")
    recovery_parser.add_argument(
        "--write-phase-status",
        required=True,
        choices=[status.value for status in WritePhaseStatus],
    )
    recovery_parser.add_argument(
        "--probe",
        action="append",
        required=True,
        choices=[probe.value for probe in ProbeClassification],
        help="Repeat per probe classification entry.",
    )
    recovery_parser.add_argument("--artifacts-valid", action=argparse.BooleanOptionalAction, default=True)
    recovery_parser.add_argument("--backup-hash-matches", action=argparse.BooleanOptionalAction, default=True)
    recovery_parser.add_argument("--staged-plan-hash-valid", action=argparse.BooleanOptionalAction, default=True)

    subparsers.add_parser("list-workflows")

    show_run_parser = subparsers.add_parser("show-run")
    show_run_parser.add_argument("--run-id", required=True)
    show_run_parser.add_argument(
        "--artifacts-root",
        type=Path,
        default=Path("artifacts") / "runs",
        help="Directory where run artifacts are stored (default: artifacts/runs).",
    )

    return parser


def _run_recovery_check(args: argparse.Namespace) -> int:
    outcome, reason = evaluate_recovery_decision(
        write_phase_status=WritePhaseStatus(args.write_phase_status),
        probe_classifications=[ProbeClassification(value) for value in args.probe],
        artifacts_valid=args.artifacts_valid,
        backup_hash_matches=args.backup_hash_matches,
        staged_plan_hash_valid=args.staged_plan_hash_valid,
    )
    print(f"recovery_outcome={outcome.value} reason={reason.value}")
    return 0 if outcome != RecoveryOutcome.HARD_BLOCK else 2


def _run_show_run(args: argparse.Namespace) -> int:
    run_dir = args.artifacts_root / args.run_id
    run_state_path = run_dir / "run_state.json"
    run_report_path = run_dir / "run_report.json"
    if not run_state_path.exists() or not run_report_path.exists():
        print(f"Run artifacts not found for run_id={args.run_id} in {args.artifacts_root}")
        return 2

    run_state = json.loads(run_state_path.read_text(encoding="utf-8"))
    run_report = json.loads(run_report_path.read_text(encoding="utf-8"))

    print(
        "Run summary\n"
        f"  run_id: {args.run_id}\n"
        f"  workflow_id: {run_state.get('workflow_id')}\n"
        f"  write_phase_status: {run_state.get('write_phase_status')}\n"
        f"  print_phase_status: {run_state.get('print_phase_status')}\n"
        f"  mail_move_phase_status: {run_state.get('mail_move_phase_status')}\n"
        f"  mail_count: {run_report.get('mail_count')}\n"
        f"  final_decision: {run_report.get('final_decision')}"
    )
    return 0


def _run_list_workflows() -> int:
    for command, workflow_module in WORKFLOW_HANDLERS.items():
        print(
            f"{command} | rule_pack_id={workflow_module.RULE_PACK_ID} "
            f"version={workflow_module.RULE_PACK_VERSION} "
            f"rule_count={len(workflow_module.APPLIED_RULE_IDS)}"
        )
    return 0


def _run_workflow_command(args: argparse.Namespace) -> int:
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

    blocking_run_id = find_blocking_prior_run(args.artifacts_root, run_context.workflow_id)
    if blocking_run_id is not None:
        print(
            f"Recovery gate blocked new run for workflow '{run_context.workflow_id}'. "
            f"Prior run requires recovery: {blocking_run_id}"
        )
        return 2

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
            mail_count=orchestration_result.run_report.mail_count,
            run_state_path=run_state_path,
            run_report_path=run_report_path,
            run_snapshot_path=orchestration_result.run_snapshot_path,
        )
    )
    return orchestration_result.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "recovery-check":
        return _run_recovery_check(args)
    if args.command == "list-workflows":
        return _run_list_workflows()
    if args.command == "show-run":
        return _run_show_run(args)
    return _run_workflow_command(args)
