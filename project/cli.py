from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from project.config import load_workflow_config
from project.documents import (
    extract_saved_document_raw_report,
    JsonManifestSavedDocumentAnalysisProvider,
    LayeredSavedDocumentAnalysisProvider,
    NullSavedDocumentAnalysisProvider,
)
from project.erp import EmptyERPRowProvider, JsonManifestERPRowProvider
from project.exceptions import ArtifactError, ConfigError, RulePackError
from project.intake import EmptyMailSnapshotProvider, JsonManifestMailSnapshotProvider, Win32ComMailSnapshotProvider
from project.outlook import SimulatedMailMoveProvider, Win32ComMailMoveProvider
from project.printing import SimulatedPrintProvider
from project.models import SavedDocument
from project.reporting.persistence import (
    append_discrepancy,
    write_commit_marker,
    write_discrepancies,
    write_mail_outcomes,
    write_manual_document_verification,
    write_print_plan,
    write_run_metadata,
    write_staged_write_plan,
    write_target_probes,
)
from project.rules import load_rule_pack
from project.storage import Win32ComAttachmentContentProvider, write_json
from project.utils.ids import build_saved_document_id
from project.utils.json import pretty_json_dumps, to_jsonable
from project.utils.time import validate_timezone
from project.workbook import (
    EmptyWorkbookSnapshotProvider,
    JsonManifestWorkbookSnapshotProvider,
    XLWingsWorkbookSnapshotProvider,
)
from project.workflows.bootstrap import initialize_workflow_run
from project.workflows.mail_moves import execute_mail_moves, summarize_mail_move_manual_verification
from project.workflows.document_verification import (
    acknowledge_document_manual_verification,
    build_document_manual_verification_bundle,
    load_document_manual_verification_bundle,
    summarize_manual_document_verification,
)
from project.workflows.print_execution import execute_print_batches, summarize_print_batch_manual_verification
from project.workflows.print_planning import (
    build_print_plan_payload,
    load_print_batches,
    load_print_planning_bundle,
    plan_print_batches,
)
from project.workflows.run_artifact_reporting import summarize_run_artifacts
from project.workflows.run_index import list_recovery_candidates, list_workflow_runs
from project.workflows.operator_queue import build_operator_queue
from project.workflows.run_recovery_precheck import build_recovery_precheck
from project.workflows.recovery import assess_recovery
from project.workflows.recovery_packet import build_workflow_recovery_packet
from project.workflows.run_reporting import summarize_run_status
from project.workflows.run_summary_export import build_run_summary_export
from project.workflows.retention_reporting import build_retention_report
from project.workflows.retention_summary import build_retention_summary
from project.workflows.summary_catalog import build_summary_catalog
from project.workflows.workflow_summary import build_workflow_summary
from project.workflows.write_execution import execute_live_write_batch
from project.workflows.registry import WORKFLOW_REGISTRY, WorkflowDescriptor
from project.workflows.validation import validate_run_snapshot
from project.workflows.write_preparation import prepare_live_write_batch


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate-config":
        return _handle_validate_config(args)
    if args.command == "init-run":
        return _handle_init_run(args)
    if args.command == "validate-run":
        return _handle_validate_run(args)
    if args.command == "inspect-document-analysis":
        return _handle_inspect_document_analysis(args)
    if args.command == "inspect-document-text":
        return _handle_inspect_document_text(args)
    if args.command == "inspect-workbook":
        return _handle_inspect_workbook(args)
    if args.command == "prepare-document-verification":
        return _handle_prepare_document_verification(args)
    if args.command == "acknowledge-document-verification":
        return _handle_acknowledge_document_verification(args)
    if args.command == "report-manual-verification":
        return _handle_report_manual_verification(args)
    if args.command == "report-run-status":
        return _handle_report_run_status(args)
    if args.command == "list-runs":
        return _handle_list_runs(args)
    if args.command == "report-run-artifacts":
        return _handle_report_run_artifacts(args)
    if args.command == "report-recovery-precheck":
        return _handle_report_recovery_precheck(args)
    if args.command == "list-recovery-candidates":
        return _handle_list_recovery_candidates(args)
    if args.command == "report-operator-queue":
        return _handle_report_operator_queue(args)
    if args.command == "export-workflow-summary":
        return _handle_export_workflow_summary(args)
    if args.command == "export-run-summary":
        return _handle_export_run_summary(args)
    if args.command == "export-recovery-packet":
        return _handle_export_recovery_packet(args)
    if args.command == "report-retention-candidates":
        return _handle_report_retention_candidates(args)
    if args.command == "export-retention-summary":
        return _handle_export_retention_summary(args)
    if args.command == "export-summary-catalog":
        return _handle_export_summary_catalog(args)
    if args.command == "recover-run":
        return _handle_recover_run(args)
    if args.command == "plan-print":
        return _handle_plan_print(args)
    if args.command == "execute-print":
        return _handle_execute_print(args)
    if args.command == "execute-mail-moves":
        return _handle_execute_mail_moves(args)

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

    inspect_document_parser = subparsers.add_parser(
        "inspect-document-analysis",
        help="Analyze one saved PDF through the document-extraction provider seam without running a workflow.",
    )
    inspect_document_parser.add_argument(
        "--document-path",
        type=Path,
        required=True,
        help="Path to the saved PDF to inspect.",
    )
    inspect_document_parser.add_argument(
        "--document-analysis-json",
        type=Path,
        help="Optional JSON manifest to inspect deterministic analysis output instead of live PDF extraction.",
    )

    inspect_document_text_parser = subparsers.add_parser(
        "inspect-document-text",
        help="Write a page-level extraction audit JSON for one saved PDF.",
    )
    inspect_document_text_parser.add_argument(
        "--document-path",
        type=Path,
        required=True,
        help="Path to the saved PDF to inspect.",
    )
    inspect_document_text_parser.add_argument(
        "--mode",
        choices=["text", "table", "img2table", "ocr", "layered"],
        default="layered",
        help="Extraction mode to run. Defaults to layered.",
    )
    inspect_document_text_parser.add_argument(
        "--output-json",
        type=Path,
        help="Optional destination for the extraction audit JSON. Defaults to <document>.extraction.<mode>.json.",
    )
    inspect_document_text_parser.add_argument(
        "--search-text",
        help="Optional text to search for inside the extracted per-page audit.",
    )
    inspect_document_text_parser.add_argument(
        "--page-from",
        type=int,
        help="Optional 1-based first page for bounded search.",
    )
    inspect_document_text_parser.add_argument(
        "--page-to",
        type=int,
        help="Optional 1-based last page for bounded search.",
    )

    inspect_workbook_parser = subparsers.add_parser(
        "inspect-workbook",
        help="Load a read-only workbook snapshot from JSON or the live workbook path.",
    )
    inspect_workbook_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    inspect_workbook_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the local TOML config.",
    )
    inspect_workbook_parser.add_argument(
        "--workbook-json",
        type=Path,
        help="Optional JSON workbook snapshot manifest.",
    )
    inspect_workbook_parser.add_argument(
        "--live-workbook",
        action="store_true",
        help="Inspect the configured yearly workbook path via xlwings.",
    )
    inspect_workbook_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    prepare_document_verification_parser = subparsers.add_parser(
        "prepare-document-verification",
        help="Write manual PDF verification artifacts for saved documents in an existing run.",
    )
    prepare_document_verification_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    prepare_document_verification_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the local TOML config.",
    )
    prepare_document_verification_parser.add_argument(
        "--run-id",
        required=True,
        help="Run id whose saved documents should be prepared for manual verification.",
    )
    prepare_document_verification_parser.add_argument(
        "--mode",
        choices=["text", "table", "img2table", "ocr", "layered"],
        default="layered",
        help="Extraction mode to use when generating per-document audit JSONs.",
    )
    prepare_document_verification_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    acknowledge_document_verification_parser = subparsers.add_parser(
        "acknowledge-document-verification",
        help="Record operator acknowledgment for one or more manually verified PDF documents.",
    )
    acknowledge_document_verification_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    acknowledge_document_verification_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the local TOML config.",
    )
    acknowledge_document_verification_parser.add_argument(
        "--run-id",
        required=True,
        help="Run id whose manual verification bundle should be updated.",
    )
    acknowledge_document_verification_parser.add_argument(
        "--saved-document-id",
        dest="saved_document_ids",
        action="append",
        default=[],
        help="Optional saved document id to acknowledge. May be repeated. If omitted, all pending documents are acknowledged.",
    )
    acknowledge_document_verification_parser.add_argument(
        "--notes",
        help="Optional operator note to attach to the acknowledged documents.",
    )
    acknowledge_document_verification_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    report_manual_verification_parser = subparsers.add_parser(
        "report-manual-verification",
        help="Summarize the manual PDF-verification state for an existing run without changing artifacts.",
    )
    report_manual_verification_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    report_manual_verification_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the local TOML config.",
    )
    report_manual_verification_parser.add_argument(
        "--run-id",
        required=True,
        help="Run id whose manual PDF-verification state should be summarized.",
    )
    report_manual_verification_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    report_run_status_parser = subparsers.add_parser(
        "report-run-status",
        help="Print one compact read-only snapshot of write, print, mail-move, and manual-verification state.",
    )
    report_run_status_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    report_run_status_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the local TOML config.",
    )
    report_run_status_parser.add_argument(
        "--run-id",
        required=True,
        help="Run id whose current status should be summarized.",
    )
    report_run_status_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    list_runs_parser = subparsers.add_parser(
        "list-runs",
        help="List recent persisted runs for one workflow from the run artifact root.",
    )
    list_runs_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    list_runs_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the local TOML config.",
    )
    list_runs_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of runs to return. Defaults to 10.",
    )
    list_runs_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    report_run_artifacts_parser = subparsers.add_parser(
        "report-run-artifacts",
        help="Inventory the persisted files and directories for one run without changing artifacts.",
    )
    report_run_artifacts_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    report_run_artifacts_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the local TOML config.",
    )
    report_run_artifacts_parser.add_argument(
        "--run-id",
        required=True,
        help="Run id whose persisted artifact layout should be summarized.",
    )
    report_run_artifacts_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    report_recovery_precheck_parser = subparsers.add_parser(
        "report-recovery-precheck",
        help="Combine run status and artifact presence into a read-only recovery readiness precheck.",
    )
    report_recovery_precheck_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    report_recovery_precheck_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the local TOML config.",
    )
    report_recovery_precheck_parser.add_argument(
        "--run-id",
        required=True,
        help="Run id whose recovery prerequisites should be summarized.",
    )
    report_recovery_precheck_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    list_recovery_candidates_parser = subparsers.add_parser(
        "list-recovery-candidates",
        help="List recent runs whose persisted phase states suggest recovery attention is needed.",
    )
    list_recovery_candidates_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    list_recovery_candidates_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the local TOML config.",
    )
    list_recovery_candidates_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of recovery candidates to return. Defaults to 10.",
    )
    list_recovery_candidates_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    report_operator_queue_parser = subparsers.add_parser(
        "report-operator-queue",
        help="Show a compact workflow-level work queue for operator follow-up.",
    )
    report_operator_queue_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    report_operator_queue_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the local TOML config.",
    )
    report_operator_queue_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of queued runs to return. Defaults to 10.",
    )
    report_operator_queue_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    export_workflow_summary_parser = subparsers.add_parser(
        "export-workflow-summary",
        help="Write one workflow-level JSON snapshot containing recent runs and the operator queue.",
    )
    export_workflow_summary_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    export_workflow_summary_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the local TOML config.",
    )
    export_workflow_summary_parser.add_argument(
        "--output-json",
        type=Path,
        help="Optional destination JSON path. Defaults to <report_root>/workflow_summaries/<workflow_id>.summary.json.",
    )
    export_workflow_summary_parser.add_argument(
        "--recent-limit",
        type=int,
        default=10,
        help="Maximum number of recent runs to include. Defaults to 10.",
    )
    export_workflow_summary_parser.add_argument(
        "--queue-limit",
        type=int,
        default=10,
        help="Maximum number of queued runs to include. Defaults to 10.",
    )
    export_workflow_summary_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    export_run_summary_parser = subparsers.add_parser(
        "export-run-summary",
        help="Write one run-level JSON handoff snapshot containing status, artifacts, and recovery precheck.",
    )
    export_run_summary_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    export_run_summary_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the local TOML config.",
    )
    export_run_summary_parser.add_argument(
        "--run-id",
        required=True,
        help="Run id whose summary snapshot should be exported.",
    )
    export_run_summary_parser.add_argument(
        "--output-json",
        type=Path,
        help="Optional destination JSON path. Defaults to <report_root>/run_summaries/<workflow_id>.<run_id>.summary.json.",
    )
    export_run_summary_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    export_recovery_packet_parser = subparsers.add_parser(
        "export-recovery-packet",
        help="Write one workflow-level JSON packet for runs that currently need recovery attention.",
    )
    export_recovery_packet_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    export_recovery_packet_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the local TOML config.",
    )
    export_recovery_packet_parser.add_argument(
        "--output-json",
        type=Path,
        help="Optional destination JSON path. Defaults to <report_root>/recovery_packets/<workflow_id>.recovery.json.",
    )
    export_recovery_packet_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of recovery candidates to include. Defaults to 10.",
    )
    export_recovery_packet_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    report_retention_candidates_parser = subparsers.add_parser(
        "report-retention-candidates",
        help="Report stale workflow artifacts by age without deleting anything.",
    )
    report_retention_candidates_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    report_retention_candidates_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the local TOML config.",
    )
    report_retention_candidates_parser.add_argument(
        "--older-than-days",
        type=int,
        default=30,
        help="Minimum age in whole days before an artifact is reported as stale. Defaults to 30.",
    )
    report_retention_candidates_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    export_retention_summary_parser = subparsers.add_parser(
        "export-retention-summary",
        help="Write one workflow-level JSON snapshot of retention candidates without deleting anything.",
    )
    export_retention_summary_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    export_retention_summary_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the local TOML config.",
    )
    export_retention_summary_parser.add_argument(
        "--output-json",
        type=Path,
        help="Optional destination JSON path. Defaults to <report_root>/retention_reports/<workflow_id>.retention.json.",
    )
    export_retention_summary_parser.add_argument(
        "--older-than-days",
        type=int,
        default=30,
        help="Minimum age in whole days before an artifact is reported as stale. Defaults to 30.",
    )
    export_retention_summary_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    export_summary_catalog_parser = subparsers.add_parser(
        "export-summary-catalog",
        help="Write one workflow-level index of generated summary snapshots already present under report_root.",
    )
    export_summary_catalog_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    export_summary_catalog_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the local TOML config.",
    )
    export_summary_catalog_parser.add_argument(
        "--output-json",
        type=Path,
        help="Optional destination JSON path. Defaults to <report_root>/summary_catalogs/<workflow_id>.catalog.json.",
    )
    export_summary_catalog_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    recover_run_parser = subparsers.add_parser(
        "recover-run",
        help="Assess whether a prior write-capable run can be safely resumed or reapplied.",
    )
    recover_run_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    recover_run_parser.add_argument("--config", type=Path, required=True, help="Path to the local TOML config.")
    recover_run_parser.add_argument("--run-id", required=True, help="Prior run id to assess for recovery.")
    recover_run_parser.add_argument(
        "--workbook-json",
        type=Path,
        help="Optional JSON workbook snapshot manifest for deterministic recovery probing.",
    )
    recover_run_parser.add_argument(
        "--live-workbook",
        action="store_true",
        help="Probe the configured yearly workbook path via xlwings for recovery.",
    )
    recover_run_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    plan_print_parser = subparsers.add_parser(
        "plan-print",
        help="Build deterministic print-group planning for a committed or safely resumable run.",
    )
    plan_print_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    plan_print_parser.add_argument("--config", type=Path, required=True, help="Path to the local TOML config.")
    plan_print_parser.add_argument("--run-id", required=True, help="Run id whose print phase should be planned.")
    plan_print_parser.add_argument(
        "--workbook-json",
        type=Path,
        help="Optional JSON workbook snapshot manifest for recovery-gated print planning.",
    )
    plan_print_parser.add_argument(
        "--live-workbook",
        action="store_true",
        help="Use the configured live workbook for recovery-gated print planning.",
    )
    plan_print_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    execute_print_parser = subparsers.add_parser(
        "execute-print",
        help="Execute a previously planned print batch and persist completion markers.",
    )
    execute_print_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    execute_print_parser.add_argument("--config", type=Path, required=True, help="Path to the local TOML config.")
    execute_print_parser.add_argument("--run-id", required=True, help="Run id whose print plan should be executed.")
    execute_print_parser.add_argument(
        "--simulate",
        action="store_true",
        help="Use the simulated print provider instead of a live desktop print adapter.",
    )
    execute_print_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

    execute_mail_moves_parser = subparsers.add_parser(
        "execute-mail-moves",
        help="Execute deterministic post-print Outlook mail moves and persist completion markers.",
    )
    execute_mail_moves_parser.add_argument(
        "workflow_id",
        choices=[workflow_id.value for workflow_id in WORKFLOW_REGISTRY],
    )
    execute_mail_moves_parser.add_argument("--config", type=Path, required=True, help="Path to the local TOML config.")
    execute_mail_moves_parser.add_argument("--run-id", required=True, help="Run id whose mail moves should be executed.")
    execute_mail_moves_parser.add_argument(
        "--simulate",
        action="store_true",
        help="Use the simulated mail-move provider instead of a live Outlook adapter.",
    )
    execute_mail_moves_parser.add_argument(
        "--live-outlook",
        action="store_true",
        help="Use the configured Outlook desktop profile via pywin32 for real mail moves.",
    )
    execute_mail_moves_parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override a config value with KEY=VALUE syntax. May be repeated.",
    )

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
        "--live-outlook-snapshot",
        action="store_true",
        help="Load the source-mail snapshot from the configured Outlook working folder via pywin32.",
    )
    parser.add_argument(
        "--erp-json",
        type=Path,
        help="Optional JSON manifest of canonical ERP rows for workflow validation.",
    )
    parser.add_argument(
        "--document-root",
        type=Path,
        help="Optional root directory for live attachment saving before validation.",
    )
    parser.add_argument(
        "--document-analysis-json",
        type=Path,
        help="Optional JSON manifest of saved-document analysis outputs for deterministic attachment classification.",
    )
    parser.add_argument(
        "--workbook-json",
        type=Path,
        help="Optional JSON workbook snapshot manifest for deterministic write staging.",
    )
    parser.add_argument(
        "--live-workbook",
        action="store_true",
        help="Use a read-only live workbook snapshot instead of a JSON workbook manifest.",
    )
    parser.add_argument(
        "--apply-live-writes",
        action="store_true",
        help="Apply the staged workbook write batch against the live workbook after validation succeeds.",
    )
    parser.add_argument(
        "--recovery-run-id",
        help="Optional prior run id to assess before applying live writes.",
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
        snapshot = _load_snapshot_if_supplied(
            snapshot_json=args.snapshot_json,
            live_outlook_snapshot=args.live_outlook_snapshot,
            config=config,
        )
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
        snapshot = _load_snapshot_if_supplied(
            snapshot_json=args.snapshot_json,
            live_outlook_snapshot=args.live_outlook_snapshot,
            config=config,
        )
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
        snapshot = _load_snapshot_if_supplied(
            snapshot_json=args.snapshot_json,
            live_outlook_snapshot=args.live_outlook_snapshot,
            config=config,
        )
        rule_pack = load_rule_pack(descriptor.workflow_id)
        initialized = initialize_workflow_run(
            descriptor=descriptor,
            config=config,
            rule_pack=rule_pack,
            mail_snapshot=snapshot,
        )
        if args.document_root is not None and not args.live_outlook_snapshot:
            raise ValueError("--document-root currently requires --live-outlook-snapshot")
        validation_result = validate_run_snapshot(
            descriptor=descriptor,
            run_report=initialized.run_report,
            rule_pack=rule_pack,
            erp_row_provider=_load_erp_provider(args.erp_json),
            workbook_snapshot=_load_workbook_snapshot(
                workbook_json=args.workbook_json,
                live_workbook=args.live_workbook,
                config=config,
            ),
            attachment_content_provider=(
                Win32ComAttachmentContentProvider(
                    outlook_profile=str(config.values.get("outlook_profile", "")).strip() or None
                )
                if args.document_root is not None
                else None
            ),
            document_root=args.document_root,
            document_analysis_provider=(
                JsonManifestSavedDocumentAnalysisProvider(args.document_analysis_json)
                if args.document_analysis_json is not None
                else (
                    LayeredSavedDocumentAnalysisProvider()
                    if args.document_root is not None
                    else NullSavedDocumentAnalysisProvider()
                )
            ),
        )
        if args.apply_live_writes and not args.live_workbook:
            raise ValueError("--apply-live-writes requires --live-workbook")
        if args.apply_live_writes and not descriptor.write_capable:
            raise ValueError("--apply-live-writes is supported only for write-capable workflows")
        if args.recovery_run_id and not args.live_workbook:
            raise ValueError("--recovery-run-id requires --live-workbook")
        if args.recovery_run_id:
            recovery_result = assess_recovery(
                workflow_id=descriptor.workflow_id,
                run_artifact_root=config.run_artifact_root,
                backup_root=config.backup_root,
                run_id=args.recovery_run_id,
                workbook_snapshot=_load_workbook_snapshot(
                    workbook_json=args.workbook_json,
                    live_workbook=args.live_workbook,
                    config=config,
                ),
                current_workbook_path=_resolve_live_workbook_path(config),
            )
            if args.apply_live_writes and recovery_result.outcome != "safe_reapply_staged_writes":
                raise ValueError(
                    f"Recovery gate for {args.recovery_run_id} returned {recovery_result.outcome}; live writes are blocked."
                )
        if args.apply_live_writes:
            validation_result = execute_live_write_batch(
                validation_result=validation_result,
                workbook_path=_resolve_live_workbook_path(config),
                operator_context=initialized.run_report.operator_context,
                run_report_persistor=lambda report: write_run_metadata(
                    initialized.artifact_paths, to_jsonable(report)
                ),
                target_probe_persistor=lambda probes: write_target_probes(
                    initialized.artifact_paths, to_jsonable(probes)
                ),
            )
        elif args.live_workbook and descriptor.write_capable:
            validation_result = prepare_live_write_batch(
                validation_result=validation_result,
                workbook_path=_resolve_live_workbook_path(config),
                operator_context=initialized.run_report.operator_context,
            )
        write_run_metadata(initialized.artifact_paths, to_jsonable(validation_result.run_report))
        write_mail_outcomes(initialized.artifact_paths, to_jsonable(validation_result.mail_outcomes))
        write_discrepancies(
            initialized.artifact_paths,
            to_jsonable(validation_result.discrepancy_reports),
        )
        write_staged_write_plan(
            initialized.artifact_paths,
            to_jsonable(validation_result.staged_write_plan),
        )
        write_target_probes(
            initialized.artifact_paths,
            to_jsonable(validation_result.target_probes),
        )
        write_commit_marker(
            initialized.artifact_paths,
            to_jsonable(validation_result.commit_marker),
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
        "staged_write_operation_count": len(validation_result.staged_write_plan),
        "target_probe_count": len(validation_result.target_probes),
        "write_phase_status": validation_result.run_report.write_phase_status.value,
        "committed_write_operations": (
            validation_result.commit_marker.operation_count
            if validation_result.commit_marker is not None
            else 0
        ),
    }
    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_recover_run(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        workbook_snapshot = _load_workbook_snapshot(
            workbook_json=args.workbook_json,
            live_workbook=args.live_workbook,
            config=config,
        )
        if workbook_snapshot is None:
            raise ValueError("Recovery assessment requires --workbook-json or --live-workbook")
        recovery_result = assess_recovery(
            workflow_id=descriptor.workflow_id,
            run_artifact_root=config.run_artifact_root,
            backup_root=config.backup_root,
            run_id=args.run_id,
            workbook_snapshot=workbook_snapshot,
            current_workbook_path=_resolve_live_workbook_path(config),
        )
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = {
        "run_id": recovery_result.run_id,
        "workflow_id": recovery_result.workflow_id.value,
        "outcome": recovery_result.outcome,
        "current_workbook_hash": recovery_result.current_workbook_hash,
        "backup_hash": recovery_result.backup_hash,
        "staged_write_plan_hash": recovery_result.staged_write_plan_hash,
        "target_probe_count": len(recovery_result.target_probes),
        "discrepancy_count": len(recovery_result.discrepancies),
        "details": recovery_result.details,
    }
    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_plan_print(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        if not descriptor.supports_print:
            raise ValueError("Print planning is not supported for this workflow")
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        run_report, mail_outcomes, staged_write_plan = load_print_planning_bundle(
            run_artifact_root=config.run_artifact_root,
            workflow_id=descriptor.workflow_id,
            run_id=args.run_id,
        )
        artifact_paths = _resolve_run_artifact_paths(
            run_artifact_root=config.run_artifact_root,
            backup_root=config.backup_root,
            workflow_id=descriptor.workflow_id.value,
            run_id=args.run_id,
        )
        recovery_outcome = None
        if run_report.write_phase_status.value != "committed":
            workbook_snapshot = _load_workbook_snapshot(
                workbook_json=args.workbook_json,
                live_workbook=args.live_workbook,
                config=config,
            )
            if workbook_snapshot is None:
                raise ValueError(
                    "Print planning for non-committed runs requires --workbook-json or --live-workbook."
                )
            recovery_result = assess_recovery(
                workflow_id=descriptor.workflow_id,
                run_artifact_root=config.run_artifact_root,
                backup_root=config.backup_root,
                run_id=args.run_id,
                workbook_snapshot=workbook_snapshot,
                current_workbook_path=_resolve_live_workbook_path(config),
            )
            recovery_outcome = recovery_result.outcome
            if recovery_outcome != "safe_resume":
                raise ValueError(
                    f"Recovery gate for {args.run_id} returned {recovery_outcome}; print planning is blocked."
                )
        planning_result = plan_print_batches(
            run_report=run_report,
            mail_outcomes=mail_outcomes,
            staged_write_plan=staged_write_plan,
            recovery_outcome=recovery_outcome,
            manual_verification_bundle=load_document_manual_verification_bundle(
                artifact_paths=artifact_paths,
                allow_missing=True,
            ),
        )
        write_run_metadata(artifact_paths, to_jsonable(planning_result.run_report))
        write_mail_outcomes(artifact_paths, to_jsonable(planning_result.mail_outcomes))
        write_print_plan(
            artifact_paths,
            build_print_plan_payload(planning_result.print_batches),
        )
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = {
        "run_id": planning_result.run_report.run_id,
        "workflow_id": planning_result.run_report.workflow_id.value,
        "print_phase_status": planning_result.run_report.print_phase_status.value,
        "print_group_order": planning_result.run_report.print_group_order,
        "print_group_count": len(planning_result.print_batches),
    }
    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_execute_print(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        if not descriptor.supports_print:
            raise ValueError("Print execution is not supported for this workflow")
        if not args.simulate:
            raise ValueError(
                "A live desktop print adapter is not implemented yet; rerun with --simulate to exercise the print phase safely."
            )
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        run_report, mail_outcomes, _staged_write_plan = load_print_planning_bundle(
            run_artifact_root=config.run_artifact_root,
            workflow_id=descriptor.workflow_id,
            run_id=args.run_id,
        )
        print_batches = load_print_batches(
            run_artifact_root=config.run_artifact_root,
            workflow_id=descriptor.workflow_id,
            run_id=args.run_id,
        )
        artifact_paths = _resolve_run_artifact_paths(
            run_artifact_root=config.run_artifact_root,
            backup_root=config.backup_root,
            workflow_id=descriptor.workflow_id.value,
            run_id=args.run_id,
        )
        updated_run_report, updated_mail_outcomes, discrepancies = execute_print_batches(
            run_report=run_report,
            mail_outcomes=mail_outcomes,
            print_batches=print_batches,
            artifact_paths=artifact_paths,
            provider=SimulatedPrintProvider(),
            run_report_persistor=lambda report: write_run_metadata(artifact_paths, to_jsonable(report)),
        )
        write_run_metadata(artifact_paths, to_jsonable(updated_run_report))
        write_mail_outcomes(artifact_paths, to_jsonable(updated_mail_outcomes))
        for discrepancy in discrepancies:
            append_discrepancy(artifact_paths, to_jsonable(discrepancy))
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = {
        "run_id": updated_run_report.run_id,
        "workflow_id": updated_run_report.workflow_id.value,
        "print_phase_status": updated_run_report.print_phase_status.value,
        "executed_group_count": len(print_batches),
        "manual_verification_summary": summarize_print_batch_manual_verification(print_batches),
        "discrepancy_count": len(discrepancies),
    }
    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_execute_mail_moves(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        if not descriptor.requires_mail_folders:
            raise ValueError("Mail moves are not supported for this workflow")
        if args.simulate and args.live_outlook:
            raise ValueError("Choose either --simulate or --live-outlook, not both")
        if not args.simulate and not args.live_outlook:
            raise ValueError("Choose one mail-move adapter mode: --simulate or --live-outlook")
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        run_report, mail_outcomes, _staged_write_plan = load_print_planning_bundle(
            run_artifact_root=config.run_artifact_root,
            workflow_id=descriptor.workflow_id,
            run_id=args.run_id,
        )
        artifact_paths = _resolve_run_artifact_paths(
            run_artifact_root=config.run_artifact_root,
            backup_root=config.backup_root,
            workflow_id=descriptor.workflow_id.value,
            run_id=args.run_id,
        )
        provider = (
            SimulatedMailMoveProvider()
            if args.simulate
            else Win32ComMailMoveProvider(
                outlook_profile=str(config.values.get("outlook_profile", "")).strip() or None
            )
        )
        updated_run_report, updated_mail_outcomes, move_operations, discrepancies = execute_mail_moves(
            run_report=run_report,
            mail_outcomes=mail_outcomes,
            artifact_paths=artifact_paths,
            provider=provider,
            require_write_committed=descriptor.write_capable,
            require_print_completed=descriptor.supports_print,
            run_report_persistor=lambda report: write_run_metadata(artifact_paths, to_jsonable(report)),
        )
        write_run_metadata(artifact_paths, to_jsonable(updated_run_report))
        write_mail_outcomes(artifact_paths, to_jsonable(updated_mail_outcomes))
        for discrepancy in discrepancies:
            append_discrepancy(artifact_paths, to_jsonable(discrepancy))
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = {
        "run_id": updated_run_report.run_id,
        "workflow_id": updated_run_report.workflow_id.value,
        "mail_move_phase_status": updated_run_report.mail_move_phase_status.value,
        "mail_move_operation_count": len(move_operations),
        "manual_verification_summary": summarize_mail_move_manual_verification(updated_mail_outcomes),
        "discrepancy_count": len(discrepancies),
    }
    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_inspect_document_analysis(args: argparse.Namespace) -> int:
    try:
        document_path = args.document_path
        if args.document_analysis_json is None and not document_path.exists():
            raise ValueError(f"Document path does not exist: {document_path}")
        normalized_filename = document_path.name.strip()
        if not normalized_filename:
            raise ValueError(f"Document path must resolve to a filename: {document_path}")
        saved_document = SavedDocument(
            saved_document_id=build_saved_document_id(
                "inspect-document",
                normalized_filename,
                str(document_path),
            ),
            mail_id="inspect-document",
            attachment_name=normalized_filename,
            normalized_filename=normalized_filename,
            destination_path=str(document_path),
            file_sha256="",
            save_decision="saved_new",
        )
        provider = (
            JsonManifestSavedDocumentAnalysisProvider(args.document_analysis_json)
            if args.document_analysis_json is not None
            else LayeredSavedDocumentAnalysisProvider()
        )
        analysis = provider.analyze(saved_document=saved_document)
    except (ConfigError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = {
        "document_path": str(document_path),
        "normalized_filename": normalized_filename,
        "analysis": to_jsonable(analysis),
    }
    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_inspect_document_text(args: argparse.Namespace) -> int:
    try:
        document_path = args.document_path
        if not document_path.exists():
            raise ValueError(f"Document path does not exist: {document_path}")
        normalized_filename = document_path.name.strip()
        if not normalized_filename:
            raise ValueError(f"Document path must resolve to a filename: {document_path}")
        saved_document = SavedDocument(
            saved_document_id=build_saved_document_id(
                "inspect-document",
                normalized_filename,
                str(document_path),
            ),
            mail_id="inspect-document",
            attachment_name=normalized_filename,
            normalized_filename=normalized_filename,
            destination_path=str(document_path),
            file_sha256="",
            save_decision="saved_new",
        )
        extraction_report = extract_saved_document_raw_report(
            saved_document=saved_document,
            mode=args.mode,
            search_text=args.search_text,
            page_from=args.page_from,
            page_to=args.page_to,
        )
        output_path = args.output_json or _default_extraction_output_path(document_path, args.mode)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(pretty_json_dumps(extraction_report), encoding="utf-8")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = {
        "document_path": str(document_path),
        "mode": args.mode,
        "output_json": str(output_path),
    }
    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_inspect_workbook(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        snapshot = _load_workbook_snapshot(
            workbook_json=args.workbook_json,
            live_workbook=args.live_workbook,
            config=config,
        )
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if snapshot is None:
        payload = {"workbook_snapshot": None}
    else:
        payload = {
            "sheet_name": snapshot.sheet_name,
            "header_count": len(snapshot.headers),
            "row_count": len(snapshot.rows),
            "headers": to_jsonable(snapshot.headers),
        }
    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_prepare_document_verification(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        run_report, mail_outcomes, _staged_write_plan = load_print_planning_bundle(
            run_artifact_root=config.run_artifact_root,
            workflow_id=descriptor.workflow_id,
            run_id=args.run_id,
        )
        artifact_paths = _resolve_run_artifact_paths(
            run_artifact_root=config.run_artifact_root,
            backup_root=config.backup_root,
            workflow_id=descriptor.workflow_id.value,
            run_id=args.run_id,
        )
        verification_result = build_document_manual_verification_bundle(
            run_report=run_report,
            mail_outcomes=mail_outcomes,
            artifact_paths=artifact_paths,
            extraction_mode=args.mode,
        )
        write_manual_document_verification(artifact_paths, verification_result.payload)
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = {
        "run_id": run_report.run_id,
        "workflow_id": run_report.workflow_id.value,
        "bundle_path": verification_result.bundle_path,
        "audit_directory": verification_result.audit_directory,
        "document_count": verification_result.document_count,
        "audit_ready_count": verification_result.audit_ready_count,
        "audit_error_count": verification_result.audit_error_count,
        "manual_verification_required": True,
    }
    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_acknowledge_document_verification(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        artifact_paths = _resolve_run_artifact_paths(
            run_artifact_root=config.run_artifact_root,
            backup_root=config.backup_root,
            workflow_id=descriptor.workflow_id.value,
            run_id=args.run_id,
        )
        acknowledgement = acknowledge_document_manual_verification(
            artifact_paths=artifact_paths,
            saved_document_ids=list(args.saved_document_ids),
            operator_notes=args.notes,
        )
        write_manual_document_verification(artifact_paths, acknowledgement.payload)
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = {
        "run_id": args.run_id,
        "workflow_id": descriptor.workflow_id.value,
        "bundle_path": acknowledgement.bundle_path,
        "acknowledged_document_count": acknowledgement.acknowledged_document_count,
        "verified_document_count": acknowledgement.verified_document_count,
        "pending_document_count": acknowledgement.pending_document_count,
        "manual_verification_complete": acknowledgement.manual_verification_complete,
    }
    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_report_manual_verification(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        run_report, mail_outcomes, _staged_write_plan = load_print_planning_bundle(
            run_artifact_root=config.run_artifact_root,
            workflow_id=descriptor.workflow_id,
            run_id=args.run_id,
        )
        artifact_paths = _resolve_run_artifact_paths(
            run_artifact_root=config.run_artifact_root,
            backup_root=config.backup_root,
            workflow_id=descriptor.workflow_id.value,
            run_id=args.run_id,
        )
        payload = summarize_manual_document_verification(
            run_report=run_report,
            mail_outcomes=mail_outcomes,
            artifact_paths=artifact_paths,
        )
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_report_run_status(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        run_report, mail_outcomes, staged_write_plan = load_print_planning_bundle(
            run_artifact_root=config.run_artifact_root,
            workflow_id=descriptor.workflow_id,
            run_id=args.run_id,
        )
        artifact_paths = _resolve_run_artifact_paths(
            run_artifact_root=config.run_artifact_root,
            backup_root=config.backup_root,
            workflow_id=descriptor.workflow_id.value,
            run_id=args.run_id,
        )
        payload = summarize_run_status(
            run_report=run_report,
            mail_outcomes=mail_outcomes,
            staged_write_plan=staged_write_plan,
            artifact_paths=artifact_paths,
        )
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_list_runs(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        payload = list_workflow_runs(
            run_artifact_root=config.run_artifact_root,
            workflow_id=descriptor.workflow_id,
            limit=args.limit,
        )
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_report_run_artifacts(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        artifact_paths = _resolve_run_artifact_paths(
            run_artifact_root=config.run_artifact_root,
            backup_root=config.backup_root,
            workflow_id=descriptor.workflow_id.value,
            run_id=args.run_id,
        )
        payload = {
            "run_id": args.run_id,
            "workflow_id": descriptor.workflow_id.value,
            "artifacts": summarize_run_artifacts(artifact_paths=artifact_paths),
        }
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_report_recovery_precheck(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        run_report, mail_outcomes, staged_write_plan = load_print_planning_bundle(
            run_artifact_root=config.run_artifact_root,
            workflow_id=descriptor.workflow_id,
            run_id=args.run_id,
        )
        artifact_paths = _resolve_run_artifact_paths(
            run_artifact_root=config.run_artifact_root,
            backup_root=config.backup_root,
            workflow_id=descriptor.workflow_id.value,
            run_id=args.run_id,
        )
        run_status = summarize_run_status(
            run_report=run_report,
            mail_outcomes=mail_outcomes,
            staged_write_plan=staged_write_plan,
            artifact_paths=artifact_paths,
        )
        artifact_inventory = summarize_run_artifacts(artifact_paths=artifact_paths)
        payload = {
            "run_id": args.run_id,
            "workflow_id": descriptor.workflow_id.value,
            "precheck": build_recovery_precheck(
                run_status=run_status,
                artifact_inventory=artifact_inventory,
            ),
            "run_status": run_status,
            "artifacts": artifact_inventory,
        }
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_list_recovery_candidates(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        payload = list_recovery_candidates(
            run_artifact_root=config.run_artifact_root,
            workflow_id=descriptor.workflow_id,
            limit=args.limit,
        )
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_report_operator_queue(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        payload = build_operator_queue(
            run_artifact_root=config.run_artifact_root,
            workflow_id=descriptor.workflow_id,
            limit=args.limit,
        )
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_export_workflow_summary(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        payload = build_workflow_summary(
            run_artifact_root=config.run_artifact_root,
            workflow_id=descriptor.workflow_id,
            recent_limit=args.recent_limit,
            queue_limit=args.queue_limit,
        )
        output_path = args.output_json or _default_workflow_summary_output_path(
            report_root=config.report_root,
            workflow_id=descriptor.workflow_id.value,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(output_path, payload)
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        pretty_json_dumps(
            {
                "workflow_id": descriptor.workflow_id.value,
                "output_json": str(output_path),
                "recent_run_count": payload["summary_counts"]["recent_run_count"],
                "operator_queue_count": payload["summary_counts"]["operator_queue_count"],
            }
        ),
        end="",
    )
    return 0


def _handle_export_run_summary(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        run_report, mail_outcomes, staged_write_plan = load_print_planning_bundle(
            run_artifact_root=config.run_artifact_root,
            workflow_id=descriptor.workflow_id,
            run_id=args.run_id,
        )
        artifact_paths = _resolve_run_artifact_paths(
            run_artifact_root=config.run_artifact_root,
            backup_root=config.backup_root,
            workflow_id=descriptor.workflow_id.value,
            run_id=args.run_id,
        )
        payload = build_run_summary_export(
            run_report=run_report,
            mail_outcomes=mail_outcomes,
            staged_write_plan=staged_write_plan,
            artifact_paths=artifact_paths,
        )
        output_path = args.output_json or _default_run_summary_output_path(
            report_root=config.report_root,
            workflow_id=descriptor.workflow_id.value,
            run_id=args.run_id,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(output_path, payload)
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        pretty_json_dumps(
            {
                "workflow_id": descriptor.workflow_id.value,
                "run_id": args.run_id,
                "output_json": str(output_path),
                "recovery_issue_count": payload["summary_counts"]["recovery_issue_count"],
                "discrepancy_count": payload["summary_counts"]["discrepancy_count"],
            }
        ),
        end="",
    )
    return 0


def _handle_export_recovery_packet(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        payload = build_workflow_recovery_packet(
            run_artifact_root=config.run_artifact_root,
            backup_root=config.backup_root,
            workflow_id=descriptor.workflow_id,
            limit=args.limit,
        )
        output_path = args.output_json or _default_recovery_packet_output_path(
            report_root=config.report_root,
            workflow_id=descriptor.workflow_id.value,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(output_path, payload)
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        pretty_json_dumps(
            {
                "workflow_id": descriptor.workflow_id.value,
                "output_json": str(output_path),
                "candidate_count": payload["candidate_count"],
                "load_error_count": payload["load_error_count"],
            }
        ),
        end="",
    )
    return 0


def _handle_report_retention_candidates(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        payload = build_retention_report(
            run_artifact_root=config.run_artifact_root,
            backup_root=config.backup_root,
            report_root=config.report_root,
            workflow_id=descriptor.workflow_id,
            older_than_days=args.older_than_days,
        )
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(pretty_json_dumps(payload), end="")
    return 0


def _handle_export_retention_summary(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        payload = build_retention_summary(
            run_artifact_root=config.run_artifact_root,
            backup_root=config.backup_root,
            report_root=config.report_root,
            workflow_id=descriptor.workflow_id,
            older_than_days=args.older_than_days,
        )
        output_path = args.output_json or _default_retention_summary_output_path(
            report_root=config.report_root,
            workflow_id=descriptor.workflow_id.value,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(output_path, payload)
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        pretty_json_dumps(
            {
                "workflow_id": descriptor.workflow_id.value,
                "output_json": str(output_path),
                "stale_run_count": payload["summary_counts"]["stale_run_count"],
                "stale_backup_count": payload["summary_counts"]["stale_backup_count"],
                "stale_report_count": payload["summary_counts"]["stale_report_count"],
            }
        ),
        end="",
    )
    return 0


def _handle_export_summary_catalog(args: argparse.Namespace) -> int:
    try:
        descriptor = _descriptor_from_args(args.workflow_id)
        config = load_workflow_config(
            descriptor=descriptor,
            config_path=args.config,
            overrides=_parse_overrides(args.overrides),
        )
        payload = build_summary_catalog(
            report_root=config.report_root,
            workflow_id=descriptor.workflow_id,
        )
        output_path = args.output_json or _default_summary_catalog_output_path(
            report_root=config.report_root,
            workflow_id=descriptor.workflow_id.value,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(output_path, payload)
    except (ArtifactError, ConfigError, RulePackError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        pretty_json_dumps(
            {
                "workflow_id": descriptor.workflow_id.value,
                "output_json": str(output_path),
                "total_summary_count": payload["summary_counts"]["total_summary_count"],
                "run_summary_count": payload["summary_counts"]["run_summary_count"],
            }
        ),
        end="",
    )
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


def _load_snapshot_if_supplied(
    *,
    snapshot_json: Path | None,
    live_outlook_snapshot: bool,
    config,
):
    if snapshot_json is not None and live_outlook_snapshot:
        raise ValueError("Choose either --snapshot-json or --live-outlook-snapshot, not both")
    if snapshot_json is not None:
        provider = JsonManifestMailSnapshotProvider(snapshot_json)
    elif live_outlook_snapshot:
        provider = Win32ComMailSnapshotProvider(
            source_folder_entry_id=str(config.values.get("source_working_folder_entry_id", "")).strip(),
            outlook_profile=str(config.values.get("outlook_profile", "")).strip() or None,
        )
    else:
        provider = EmptyMailSnapshotProvider()
    return provider.load_snapshot(state_timezone=config.state_timezone)


def _load_erp_provider(erp_json: Path | None):
    if erp_json is None:
        return EmptyERPRowProvider()
    return JsonManifestERPRowProvider(erp_json)


def _load_workbook_snapshot(
    *,
    workbook_json: Path | None,
    live_workbook: bool,
    config,
):
    if workbook_json is not None and live_workbook:
        raise ValueError("Choose either --workbook-json or --live-workbook, not both")
    if workbook_json is not None:
        provider = JsonManifestWorkbookSnapshotProvider(workbook_json)
    elif live_workbook:
        workflow_year = datetime.now(tz=validate_timezone(config.state_timezone)).year
        provider = XLWingsWorkbookSnapshotProvider(config.resolve_master_workbook_path(workflow_year))
    else:
        provider = EmptyWorkbookSnapshotProvider()
    return provider.load_snapshot()


def _resolve_live_workbook_path(config):
    workflow_year = datetime.now(tz=validate_timezone(config.state_timezone)).year
    return config.resolve_master_workbook_path(workflow_year)


def _resolve_run_artifact_paths(*, run_artifact_root: Path, backup_root: Path, workflow_id: str, run_id: str):
    from project.storage import create_run_artifact_layout

    return create_run_artifact_layout(
        run_artifact_root=run_artifact_root,
        backup_root=backup_root,
        workflow_id=workflow_id,
        run_id=run_id,
    )


def _default_extraction_output_path(document_path: Path, mode: str) -> Path:
    return document_path.with_suffix(f"{document_path.suffix}.extraction.{mode}.json")


def _default_workflow_summary_output_path(*, report_root: Path, workflow_id: str) -> Path:
    return report_root / "workflow_summaries" / f"{workflow_id}.summary.json"


def _default_run_summary_output_path(*, report_root: Path, workflow_id: str, run_id: str) -> Path:
    return report_root / "run_summaries" / f"{workflow_id}.{run_id}.summary.json"


def _default_recovery_packet_output_path(*, report_root: Path, workflow_id: str) -> Path:
    return report_root / "recovery_packets" / f"{workflow_id}.recovery.json"


def _default_retention_summary_output_path(*, report_root: Path, workflow_id: str) -> Path:
    return report_root / "retention_reports" / f"{workflow_id}.retention.json"


def _default_summary_catalog_output_path(*, report_root: Path, workflow_id: str) -> Path:
    return report_root / "summary_catalogs" / f"{workflow_id}.catalog.json"


if __name__ == "__main__":
    raise SystemExit(main())
