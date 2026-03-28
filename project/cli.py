from __future__ import annotations

import argparse
import sys
from pathlib import Path

from project.config import load_workflow_config
from project.exceptions import ArtifactError, ConfigError, RulePackError
from project.intake import EmptyMailSnapshotProvider, JsonManifestMailSnapshotProvider
from project.reporting.persistence import write_discrepancies, write_mail_outcomes, write_run_metadata
from project.rules import load_rule_pack
from project.utils.json import pretty_json_dumps, to_jsonable
from project.workflows.bootstrap import initialize_workflow_run
from project.workflows.registry import WORKFLOW_REGISTRY, WorkflowDescriptor
from project.workflows.validation import validate_run_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate-config":
        return _handle_validate_config(args)
    if args.command == "init-run":
        return _handle_init_run(args)
    if args.command == "validate-run":
        return _handle_validate_run(args)

    parser.error(f"Unsupported command: {args.command}")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="customs-automation",
        description="Core CLI dispatcher for customs/commercial automation workflows.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate-config",
        help="Validate workflow configuration without creating run artifacts.",
    )
    _add_common_workflow_args(validate_parser)

    init_parser = subparsers.add_parser(
        "init-run",
        help="Validate startup contracts and create the initial run artifact layout.",
    )
    _add_common_workflow_args(init_parser)

    validate_run_parser = subparsers.add_parser(
        "validate-run",
        help="Initialize a run and evaluate the snapshotted mails through the current rule pack.",
    )
    _add_common_workflow_args(validate_run_parser)

    return parser


def _add_common_workflow_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("workflow_id", choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY])
    parser.add_argument("--config", type=Path, required=True, help="Path to the local TOML config.")
    parser.add_argument(
        "--snapshot-json",
        type=Path,
        help="Optional JSON manifest of source emails to bind into the run snapshot.",
    )
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )


def _handle_validate_config(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        snapshot = _load_snapshot_if_supplied(args.snapshot_json, config.state_timezone)
    except (ConfigError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = {
        "workflow_id": descriptor.workflow_id.value,
        "config": to_jsonable(config.values),
        "snapshot_count": len(snapshot),
    }
    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_init_run(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        snapshot = _load_snapshot_if_supplied(args.snapshot_json, config.state_timezone)
        rule_pack = load_rule_pack(descriptor.workflow_id)
        initialized = initialize_workflow_run(
            descriptor=descriptor,
            config=config,
            rule_pack=rule_pack,
            mail_snapshot=snapshot,
        )
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = {
        "run_id": initialized.run_report.run_id,
        "workflow_id": initialized.descriptor.workflow_id.value,
        "rule_pack_id": initialized.rule_pack.rule_pack_id,
        "rule_pack_version": initialized.rule_pack.rule_pack_version,
        "artifact_root": str(initialized.artifact_paths.run_root),
        "backup_root": str(initialized.artifact_paths.backup_root),
        "master_workbook_path": initialized.master_workbook_path,
        "snapshot_count": len(initialized.run_report.mail_snapshot),
        "mail_iteration_order": initialized.run_report.mail_iteration_order,
    }
    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_validate_run(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        snapshot = _load_snapshot_if_supplied(args.snapshot_json, config.state_timezone)
        rule_pack = load_rule_pack(descriptor.workflow_id)
        initialized = initialize_workflow_run(
            descriptor=descriptor,
            config=config,
            rule_pack=rule_pack,
            mail_snapshot=snapshot,
        )
        validation_result = validate_run_snapshot(
            descriptor=descriptor,
            run_report=initialized.run_report,
            rule_pack=rule_pack,
        )
        write_run_metadata(initialized.artifact_paths, to_jsonable(validation_result.run_report))
        write_mail_outcomes(initialized.artifact_paths, to_jsonable(validation_result.mail_outcomes))
        write_discrepancies(
            initialized.artifact_paths,
            to_jsonable(validation_result.discrepancy_reports),
        )
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = {
        "run_id": validation_result.run_report.run_id,
        "workflow_id": validation_result.run_report.workflow_id.value,
        "rule_pack_id": validation_result.run_report.rule_pack_id,
        "rule_pack_version": validation_result.run_report.rule_pack_version,
        "artifact_root": str(initialized.artifact_paths.run_root),
        "summary": validation_result.run_report.summary,
        "mail_iteration_order": validation_result.run_report.mail_iteration_order,
    }
    print(pretty_json_dumps(payload), end="")
    return 0


def _descriptor_from_args(workflow_id: str) -> WorkflowDescriptor:
    for descriptor in WORKFLOW_REGISTRY.values():
        if descriptor.workflow_id.value == workflow_id:
            return descriptor
    raise ValueError(f"Unknown workflow id: {workflow_id}")


def _parse_overrides(items: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Override must use KEY=VALUE syntax: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Override key cannot be empty: {item}")
        overrides[key] = value
    return overrides


def _load_snapshot_if_supplied(snapshot_json: Path | None, state_timezone: str):
    provider = (
        JsonManifestMailSnapshotProvider(snapshot_json)
        if snapshot_json is not None
        else EmptyMailSnapshotProvider()
    )
    return provider.load_snapshot(state_timezone=state_timezone)


if __name__ == "__main__":
    raise SystemExit(main())
