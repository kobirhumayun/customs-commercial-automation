from __future__ import annotations

import json
from html import escape
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from project.models import WorkbookSessionPreflight, WorkflowId, WriteOperation
from project.storage.artifacts import atomic_write_text
from project.utils.hashing import canonical_json_hash
from project.utils.ids import build_write_operation_id
from project.utils.json import pretty_json_dumps, to_jsonable
from project.utils.time import utc_timestamp
from project.workbook import (
    WorkbookHeader,
    WorkbookMutationSessionProvider,
    WorkbookRow,
    WorkbookSnapshot,
    XLWingsWorkbookMutationProvider,
    XLWingsWorkbookSnapshotProvider,
)
from project.workflows.import_btb_lc.extraction import (
    IMPORT_BTB_LC_EXTRACTION_SCHEMA_VERSION,
    IMPORT_BTB_LC_REPORT_SCHEMA_VERSION,
    extract_import_btb_lc_pdf,
)
from project.workflows.import_btb_lc.extraction import (
    _canonicalize_related_export_lc as _canonicalize_extracted_related_lc,
)


IMPORT_BTB_LC_WORKFLOW_SCHEMA_ID = "import_btb_lc_workflow"
IMPORT_BTB_LC_WORKFLOW_SCHEMA_VERSION = "1.1.0"
IMPORT_BTB_LC_DATE_NUMBER_FORMAT = "dd/mm/yyyy"
IMPORT_BTB_LC_AMOUNT_NUMBER_FORMAT = "#,##0.00"


@dataclass(slots=True, frozen=True)
class ImportBTBLCHeaderMapping:
    lc_sc_no: int
    up_no: int
    export_amount: int
    btb_lc_no: int
    btb_lc_issue_date: int
    import_amount: int

    def as_dict(self) -> dict[str, int]:
        return {
            "lc_sc_no": self.lc_sc_no,
            "up_no": self.up_no,
            "export_amount": self.export_amount,
            "btb_lc_no": self.btb_lc_no,
            "btb_lc_issue_date": self.btb_lc_issue_date,
            "import_amount": self.import_amount,
        }


@dataclass(slots=True, frozen=True)
class ImportBTBLCDocument:
    document_id: str
    source_path: str
    filename: str
    file_sha256: str
    snapshot_index: int
    attachment_index: int | None
    extraction_artifact: dict[str, object]
    btb_lc_number: str | None
    btb_lc_date: str | None
    btb_lc_value: Decimal | None
    btb_lc_value_text: str | None
    currency: str | None
    seller_pi_numbers: tuple[str, ...]
    related_export_lc_number: str | None


@dataclass(slots=True, frozen=True)
class ImportBTBLCAllocationResult:
    workflow_report: dict[str, object]
    staged_write_plan: list[WriteOperation]


def run_import_btb_lc_file_picker(
    *,
    input_path: Path,
    output_directory: Path,
    workbook_snapshot: WorkbookSnapshot,
    run_id: str,
    apply_live_writes: bool = False,
    workbook_path: Path | None = None,
    mutation_session_provider: WorkbookMutationSessionProvider | None = None,
) -> dict[str, object]:
    """Run the file-picker import path over local PDFs or extraction JSON artifacts."""

    output_directory.mkdir(parents=True, exist_ok=True)
    extraction_directory = output_directory / "extraction"
    extraction_directory.mkdir(parents=True, exist_ok=True)

    documents: list[ImportBTBLCDocument] = []
    for index, source in enumerate(_resolve_import_inputs(input_path)):
        artifact = _load_or_extract_artifact(source, extraction_directory=extraction_directory)
        documents.append(
            _document_from_artifact(
                artifact=artifact,
                snapshot_index=index,
                attachment_index=None,
            )
        )

    allocation = allocate_import_btb_lc_documents(
        documents=documents,
        workbook_snapshot=workbook_snapshot,
        run_id=run_id,
    )
    report = dict(allocation.workflow_report)
    write_execution = {
        "requested": bool(apply_live_writes),
        "status": "not_requested",
        "target_probes": [],
        "commit_marker": None,
        "discrepancies": [],
    }
    if apply_live_writes:
        if workbook_path is None:
            raise ValueError("--apply-live-writes requires --workbook")
        write_execution = execute_import_btb_lc_writes(
            run_id=run_id,
            workbook_snapshot=workbook_snapshot,
            staged_write_plan=allocation.staged_write_plan,
            workbook_path=workbook_path,
            mutation_session_provider=mutation_session_provider,
        )
    report["write_execution"] = write_execution
    report["overall_decision"] = _overall_decision_from_document_outcomes(
        report["document_outcomes"],
        write_execution=write_execution,
    )
    report["completed_at_utc"] = utc_timestamp()
    report["staged_write_plan_hash"] = canonical_json_hash(
        to_jsonable(allocation.staged_write_plan)
    )

    output_path = output_directory / f"{run_id}.import-btb-lc.workflow.json"
    atomic_write_text(output_path, pretty_json_dumps(report))
    html_path = output_directory / f"{run_id}.import-btb-lc.workflow.html"
    atomic_write_text(html_path, render_import_btb_lc_html_report(report))
    summary = {
        "schema_id": IMPORT_BTB_LC_WORKFLOW_SCHEMA_ID,
        "schema_version": IMPORT_BTB_LC_WORKFLOW_SCHEMA_VERSION,
        "report_schema_version": IMPORT_BTB_LC_REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "input_path": str(input_path.resolve()),
        "output_path": str(output_path.resolve()),
        "html_output_path": str(html_path.resolve()),
        "overall_decision": report["overall_decision"],
        "document_count": len(documents),
        "staged_write_operation_count": len(allocation.staged_write_plan),
        "write_execution_status": write_execution["status"],
    }
    return summary


def allocate_import_btb_lc_documents(
    *,
    documents: list[ImportBTBLCDocument],
    workbook_snapshot: WorkbookSnapshot,
    run_id: str,
) -> ImportBTBLCAllocationResult:
    mapping = resolve_import_btb_lc_header_mapping(workbook_snapshot)
    report: dict[str, object] = {
        "schema_id": IMPORT_BTB_LC_WORKFLOW_SCHEMA_ID,
        "schema_version": IMPORT_BTB_LC_WORKFLOW_SCHEMA_VERSION,
        "report_schema_version": IMPORT_BTB_LC_REPORT_SCHEMA_VERSION,
        "workflow_id": WorkflowId.IMPORT_BTB_LC.value,
        "run_id": run_id,
        "started_at_utc": utc_timestamp(),
        "completed_at_utc": None,
        "extraction_schema_version": IMPORT_BTB_LC_EXTRACTION_SCHEMA_VERSION,
        "workbook": {
            "sheet_name": workbook_snapshot.sheet_name,
            "header_mapping": mapping.as_dict() if mapping is not None else None,
        },
        "allocation_order": [],
        "document_outcomes": [],
        "staged_write_plan": [],
        "summary": {},
        "overall_decision": "pass",
    }
    if mapping is None:
        outcomes = [
            _blocked_document_outcome(
                document=document,
                code="workbook_header_mapping_invalid",
                message="Required import workbook headers could not be resolved deterministically.",
                details={"sheet_name": workbook_snapshot.sheet_name},
            )
            for document in documents
        ]
        report["document_outcomes"] = outcomes
        report["summary"] = _summarize_outcomes(outcomes)
        report["overall_decision"] = "hard_block" if outcomes else "pass"
        return ImportBTBLCAllocationResult(workflow_report=report, staged_write_plan=[])

    outcomes_by_document_id: dict[str, dict[str, object]] = {}
    staged_write_plan: list[WriteOperation] = []
    reserved_rows: set[int] = set()
    accepted_signatures_by_btb: dict[str, tuple[object, ...]] = {}
    ordered_documents = sorted(documents, key=_allocation_sort_key)
    report["allocation_order"] = [document.document_id for document in ordered_documents]

    for document in ordered_documents:
        outcome, operations = _allocate_one_document(
            document=document,
            workbook_snapshot=workbook_snapshot,
            mapping=mapping,
            run_id=run_id,
            reserved_rows=reserved_rows,
            accepted_signatures_by_btb=accepted_signatures_by_btb,
        )
        outcomes_by_document_id[document.document_id] = outcome
        staged_write_plan.extend(operations)

    outcomes = [
        outcomes_by_document_id[document.document_id]
        for document in sorted(documents, key=lambda item: (item.snapshot_index, item.filename.casefold()))
    ]
    report["document_outcomes"] = outcomes
    report["staged_write_plan"] = to_jsonable(staged_write_plan)
    report["summary"] = _summarize_outcomes(outcomes)
    report["overall_decision"] = _overall_decision_from_document_outcomes(outcomes)
    return ImportBTBLCAllocationResult(
        workflow_report=report,
        staged_write_plan=staged_write_plan,
    )


def resolve_import_btb_lc_header_mapping(
    snapshot: WorkbookSnapshot,
) -> ImportBTBLCHeaderMapping | None:
    lc_sc_no = _resolve_header(snapshot.headers, "L/C & S/C No.", ("L/C No.", "LC/SC No.", "LC No."))
    up_no = _resolve_header(snapshot.headers, "UP No.", ("UP",))
    btb_lc_no = _resolve_header(
        snapshot.headers,
        "BTB L/C No.",
        (
            "BTB L/C No",
            "BTB LC No.",
            "BTB LC No",
            "Back To Back L/C No.",
            "Back-to-Back L/C No.",
        ),
    )
    btb_lc_issue_date = _resolve_header(
        snapshot.headers,
        "BTB LC Issue Date",
        (
            "BTB L/C Issue Date",
            "BTB LC Issue Dt.",
            "BTB L/C Issue Dt.",
            "BTB LC Date",
            "BTB L/C Date",
        ),
    )
    export_amount_headers = _resolve_header_candidates(snapshot.headers, "Amount", required_column_index=6)
    import_amount_headers = _resolve_header_candidates(snapshot.headers, "Amount", required_column_index=22)
    if (
        lc_sc_no is None
        or up_no is None
        or btb_lc_no is None
        or btb_lc_issue_date is None
        or len(export_amount_headers) != 1
        or len(import_amount_headers) != 1
    ):
        return None
    return ImportBTBLCHeaderMapping(
        lc_sc_no=lc_sc_no,
        up_no=up_no,
        export_amount=export_amount_headers[0],
        btb_lc_no=btb_lc_no,
        btb_lc_issue_date=btb_lc_issue_date,
        import_amount=import_amount_headers[0],
    )


def execute_import_btb_lc_writes(
    *,
    run_id: str,
    workbook_snapshot: WorkbookSnapshot,
    staged_write_plan: list[WriteOperation],
    workbook_path: Path,
    mutation_session_provider: WorkbookMutationSessionProvider | None = None,
) -> dict[str, object]:
    if not staged_write_plan:
        return {
            "requested": True,
            "status": "no_writes",
            "target_probes": [],
            "commit_marker": None,
            "discrepancies": [],
        }
    mapping = resolve_import_btb_lc_header_mapping(workbook_snapshot)
    if mapping is None:
        return {
            "requested": True,
            "status": "hard_blocked_no_write",
            "target_probes": [],
            "commit_marker": None,
            "discrepancies": [
                {
                    "code": "workbook_header_mapping_invalid",
                    "severity": "hard_block",
                    "message": "Required import workbook headers could not be resolved before writing.",
                    "details": {"sheet_name": workbook_snapshot.sheet_name},
                }
            ],
        }
    provider = mutation_session_provider or XLWingsWorkbookMutationProvider(workbook_path)
    open_result = provider.open_write_session(operator_context=None)
    if open_result.discrepancy_code is not None or open_result.session is None:
        return {
            "requested": True,
            "status": "hard_blocked_no_write",
            "preflight": to_jsonable(open_result.preflight),
            "target_probes": [],
            "commit_marker": None,
            "discrepancies": [
                {
                    "code": open_result.discrepancy_code or "excel_adapter_unavailable",
                    "severity": "hard_block",
                    "message": open_result.discrepancy_message
                    or "Workbook write session could not be opened safely.",
                    "details": open_result.discrepancy_details,
                }
            ],
        }

    session = open_result.session
    probes: list[dict[str, object]] = []
    try:
        live_snapshot = session.capture_snapshot()
        prevalidation = prevalidate_import_btb_lc_write_plan(
            run_id=run_id,
            workbook_snapshot=live_snapshot,
            staged_write_plan=staged_write_plan,
        )
        probes.extend(prevalidation["target_probes"])
        if prevalidation["discrepancies"]:
            return {
                "requested": True,
                "status": "hard_blocked_no_write",
                "preflight": to_jsonable(open_result.preflight),
                "target_probes": probes,
                "commit_marker": None,
                "discrepancies": prevalidation["discrepancies"],
            }

        column_by_key = _import_column_index_by_key(mapping)
        for operation in staged_write_plan:
            session.write_cell(
                sheet_name=operation.sheet_name,
                row_index=operation.row_index,
                column_index=column_by_key[operation.column_key],
                value=_coerce_import_write_value(
                    operation.expected_post_write_value,
                    number_format=operation.number_format,
                ),
                number_format=operation.number_format,
            )
        post_probes = _collect_import_post_write_probes(
            session=session,
            mapping=mapping,
            staged_write_plan=staged_write_plan,
        )
        probes.extend(post_probes)
        mismatches = [probe for probe in post_probes if probe["classification"] != "matches_post_write"]
        if mismatches:
            return {
                "requested": True,
                "status": "uncertain_not_committed",
                "preflight": to_jsonable(open_result.preflight),
                "target_probes": probes,
                "commit_marker": None,
                "discrepancies": [
                    {
                        "code": "workbook_post_write_probe_mismatch",
                        "severity": "hard_block",
                        "message": "One or more import workbook targets did not match after write application.",
                        "details": {"mismatches": mismatches},
                    }
                ],
            }
        try:
            session.save()
        except Exception as exc:
            return {
                "requested": True,
                "status": "uncertain_not_committed",
                "preflight": to_jsonable(open_result.preflight),
                "target_probes": probes,
                "commit_marker": None,
                "discrepancies": [
                    {
                        "code": "workbook_save_conflict",
                        "severity": "hard_block",
                        "message": "Workbook save failed after import writes were applied.",
                        "details": {"error": str(exc)},
                    }
                ],
            }
        return {
            "requested": True,
            "status": "committed",
            "preflight": to_jsonable(open_result.preflight),
            "target_probes": probes,
            "commit_marker": {
                "run_id": run_id,
                "workflow_id": WorkflowId.IMPORT_BTB_LC.value,
                "committed_at_utc": utc_timestamp(),
                "operation_count": len(staged_write_plan),
                "staged_write_plan_hash": canonical_json_hash(to_jsonable(staged_write_plan)),
            },
            "discrepancies": [],
        }
    finally:
        try:
            session.close()
        except Exception:
            pass


def prevalidate_import_btb_lc_write_plan(
    *,
    run_id: str,
    workbook_snapshot: WorkbookSnapshot,
    staged_write_plan: list[WriteOperation],
) -> dict[str, object]:
    mapping = resolve_import_btb_lc_header_mapping(workbook_snapshot)
    if mapping is None:
        return {
            "target_probes": [],
            "summary": {"status": "hard_blocked", "total_targets": len(staged_write_plan)},
            "discrepancies": [
                {
                    "code": "workbook_header_mapping_invalid",
                    "severity": "hard_block",
                    "message": "Required import workbook headers could not be resolved deterministically during target prevalidation.",
                    "details": {"sheet_name": workbook_snapshot.sheet_name},
                }
            ],
        }
    column_by_key = _import_column_index_by_key(mapping)
    rows_by_index = {row.row_index: row for row in workbook_snapshot.rows}
    probes: list[dict[str, object]] = []
    discrepancies: list[dict[str, object]] = []
    for operation in staged_write_plan:
        column_index = column_by_key.get(operation.column_key)
        row = rows_by_index.get(operation.row_index)
        observed = row.values.get(column_index, "") if row is not None and column_index is not None else None
        failure_reason = None
        if operation.sheet_name != workbook_snapshot.sheet_name:
            failure_reason = "sheet_name_mismatch"
        elif column_index is None:
            failure_reason = "column_key_unmapped"
        elif _normalize_cell_value(observed) not in {"", None}:
            failure_reason = "target_cell_already_populated"
        classification = (
            "matches_pre_write"
            if failure_reason is None
            and _normalize_cell_value(observed) in {"", None}
            and operation.expected_pre_write_value is None
            else "mismatch_unknown"
        )
        probe = {
            "write_operation_id": operation.write_operation_id,
            "run_id": run_id,
            "mail_id": operation.mail_id,
            "probe_stage": "prevalidation",
            "sheet_name": operation.sheet_name,
            "row_index": operation.row_index,
            "column_key": operation.column_key,
            "column_index": column_index,
            "expected_pre_write_value": operation.expected_pre_write_value,
            "expected_post_write_value": operation.expected_post_write_value,
            "observed_value": observed,
            "classification": classification,
            "failure_reason": failure_reason,
        }
        probes.append(probe)
        if classification != "matches_pre_write":
            discrepancies.append(
                {
                    "code": "import_target_cell_already_populated"
                    if failure_reason == "target_cell_already_populated"
                    else "workbook_target_prevalidation_failed",
                    "severity": "hard_block",
                    "message": "Staged import workbook target failed prevalidation.",
                    "details": probe,
                }
            )
    return {
        "target_probes": probes,
        "summary": {
            "status": "passed" if not discrepancies else "hard_blocked",
            "total_targets": len(probes),
            "matches_pre_write": sum(1 for probe in probes if probe["classification"] == "matches_pre_write"),
            "mismatch_unknown": sum(1 for probe in probes if probe["classification"] == "mismatch_unknown"),
        },
        "discrepancies": discrepancies,
    }


def render_import_btb_lc_html_report(report: dict[str, object]) -> str:
    outcomes = report.get("document_outcomes", [])
    rows = []
    if isinstance(outcomes, list):
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            fields = outcome.get("extracted_fields") if isinstance(outcome.get("extracted_fields"), dict) else {}
            rows.append(
                "<tr>"
                f"<td>{escape(str(outcome.get('decision', '')))}</td>"
                f"<td>{escape(str(outcome.get('filename', '')))}</td>"
                f"<td>{escape(str(fields.get('btb_lc_number', '') if isinstance(fields, dict) else ''))}</td>"
                f"<td>{escape(str(fields.get('btb_lc_date', '') if isinstance(fields, dict) else ''))}</td>"
                f"<td>{escape(str(fields.get('btb_lc_value', '') if isinstance(fields, dict) else ''))}</td>"
                f"<td>{escape(str(fields.get('related_export_lc_number', '') if isinstance(fields, dict) else ''))}</td>"
                f"<td>{escape(str(outcome.get('selected_row_index', '') or ''))}</td>"
                f"<td>{escape(str(outcome.get('write_disposition', '')))}</td>"
                "</tr>"
            )
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            "<title>Import BTB LC Workflow Report</title>",
            "<style>",
            "body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1f2937}",
            "table{border-collapse:collapse;width:100%;margin-top:16px}",
            "th,td{border:1px solid #d1d5db;padding:6px 8px;text-align:left}",
            "th{background:#f3f4f6}",
            "code{background:#f3f4f6;padding:2px 4px}",
            "</style>",
            "</head>",
            "<body>",
            "<h1>Import BTB LC Workflow Report</h1>",
            f"<p><strong>Run:</strong> <code>{escape(str(report.get('run_id', '')))}</code></p>",
            f"<p><strong>Decision:</strong> {escape(str(report.get('overall_decision', '')))}</p>",
            f"<p><strong>Summary:</strong> {escape(json.dumps(summary, sort_keys=True))}</p>",
            "<table>",
            "<thead><tr><th>Decision</th><th>Filename</th><th>BTB LC</th><th>BTB LC Issue Date</th><th>Value</th><th>Related Export LC</th><th>Row</th><th>Disposition</th></tr></thead>",
            "<tbody>",
            *rows,
            "</tbody>",
            "</table>",
            "</body>",
            "</html>",
        ]
    )


def _allocate_one_document(
    *,
    document: ImportBTBLCDocument,
    workbook_snapshot: WorkbookSnapshot,
    mapping: ImportBTBLCHeaderMapping,
    run_id: str,
    reserved_rows: set[int],
    accepted_signatures_by_btb: dict[str, tuple[object, ...]],
) -> tuple[dict[str, object], list[WriteOperation]]:
    base = _base_document_outcome(document)
    extraction_decision = str(document.extraction_artifact.get("overall_extraction_decision", "hard_block"))
    if extraction_decision == "hard_block":
        base["decision"] = "hard_block"
        base["hard_block_discrepancies"] = list(
            document.extraction_artifact.get("hard_block_discrepancies", [])
        )
        base["decision_reasons"] = ["Extraction produced hard-block discrepancies."]
        return base, []

    required_missing = [
        field_name
        for field_name, value in (
            ("btb_lc_number", document.btb_lc_number),
            ("btb_lc_date", document.btb_lc_date),
            ("btb_lc_value", document.btb_lc_value),
            ("currency", document.currency),
            ("related_export_lc_number", document.related_export_lc_number),
        )
        if value is None
    ]
    if required_missing:
        base["decision"] = "hard_block"
        base["hard_block_discrepancies"].append(
            {
                "code": "import_required_field_missing",
                "severity": "hard_block",
                "message": "A required import BTB LC field was missing after extraction.",
                "details": {"missing_fields": required_missing},
            }
        )
        return base, []

    signature = _same_run_signature(document)
    if document.btb_lc_number in accepted_signatures_by_btb:
        if accepted_signatures_by_btb[document.btb_lc_number] == signature:
            base["decision"] = "warning"
            base["write_disposition"] = "duplicate_only_noop"
            base["warnings"].append(
                {
                    "code": "import_duplicate_document_same_run",
                    "severity": "warning",
                    "message": "Duplicate import BTB LC evidence in the same run was ignored.",
                    "details": {"btb_lc_number": document.btb_lc_number},
                }
            )
            base["decision_reasons"] = ["Duplicate import BTB LC already accepted earlier in this run."]
            return base, []
        base["decision"] = "hard_block"
        base["hard_block_discrepancies"].append(
            {
                "code": "import_duplicate_document_conflict",
                "severity": "hard_block",
                "message": "The same BTB LC number appeared with conflicting import evidence in this run.",
                "details": {"btb_lc_number": document.btb_lc_number},
            }
        )
        return base, []

    workbook_duplicate = _classify_workbook_duplicate(
        document=document,
        workbook_snapshot=workbook_snapshot,
        mapping=mapping,
    )
    if workbook_duplicate is not None:
        base["candidate_rows"] = workbook_duplicate["candidate_rows"]
        if workbook_duplicate["status"] == "duplicate":
            base["decision"] = "warning"
            base["write_disposition"] = "duplicate_only_noop"
            base["warnings"].append(workbook_duplicate["discrepancy"])
            base["decision_reasons"] = [
                "BTB LC was already recorded in the workbook with matching export LC and import amount."
            ]
            accepted_signatures_by_btb[document.btb_lc_number or ""] = signature
            return base, []
        base["decision"] = "hard_block"
        base["hard_block_discrepancies"].append(workbook_duplicate["discrepancy"])
        base["decision_reasons"] = ["Workbook duplicate evidence could not be verified safely."]
        return base, []

    candidate_result = _select_candidate_row(
        document=document,
        workbook_snapshot=workbook_snapshot,
        mapping=mapping,
        reserved_rows=reserved_rows,
    )
    base["candidate_rows"] = candidate_result["candidate_rows"]
    if candidate_result["status"] != "selected":
        base["decision"] = "hard_block"
        base["hard_block_discrepancies"].append(candidate_result["discrepancy"])
        base["decision_reasons"] = [candidate_result["reason"]]
        return base, []

    row_index = int(candidate_result["selected_row_index"])
    operations = _stage_import_write_operations(
        run_id=run_id,
        document=document,
        workbook_snapshot=workbook_snapshot,
        mapping=mapping,
        row_index=row_index,
    )
    reserved_rows.add(row_index)
    accepted_signatures_by_btb[document.btb_lc_number or ""] = signature
    base["decision"] = "warning" if base["warnings"] else "pass"
    base["write_disposition"] = "new_writes_staged"
    base["selected_row_index"] = row_index
    base["staged_write_operations"] = to_jsonable(operations)
    base["decision_reasons"] = [
        f"Selected workbook row {row_index} for import BTB LC write staging."
    ]
    return base, operations


def _stage_import_write_operations(
    *,
    run_id: str,
    document: ImportBTBLCDocument,
    workbook_snapshot: WorkbookSnapshot,
    mapping: ImportBTBLCHeaderMapping,
    row_index: int,
) -> list[WriteOperation]:
    assert document.btb_lc_number is not None
    assert document.btb_lc_date is not None
    assert document.btb_lc_value_text is not None
    values = (
        ("btb_lc_no", document.btb_lc_number, None),
        ("btb_lc_issue_date", document.btb_lc_date, IMPORT_BTB_LC_DATE_NUMBER_FORMAT),
        ("import_amount", document.btb_lc_value_text, IMPORT_BTB_LC_AMOUNT_NUMBER_FORMAT),
    )
    operations: list[WriteOperation] = []
    for operation_index, (column_key, value, number_format) in enumerate(values):
        operations.append(
            WriteOperation(
                write_operation_id=build_write_operation_id(
                    run_id=run_id,
                    mail_id=document.document_id,
                    operation_index_within_mail=operation_index,
                    sheet_name=workbook_snapshot.sheet_name,
                    row_index=row_index,
                    column_key=column_key,
                ),
                run_id=run_id,
                mail_id=document.document_id,
                operation_index_within_mail=operation_index,
                sheet_name=workbook_snapshot.sheet_name,
                row_index=row_index,
                column_key=column_key,
                expected_pre_write_value=None,
                expected_post_write_value=value,
                row_eligibility_checks=[
                    "import_related_export_lc_match",
                    "import_up_no_blank",
                    "import_target_cell_blank",
                    "import_value_40_to_80_percent",
                ],
                number_format=number_format,
            )
        )
    return operations


def _select_candidate_row(
    *,
    document: ImportBTBLCDocument,
    workbook_snapshot: WorkbookSnapshot,
    mapping: ImportBTBLCHeaderMapping,
    reserved_rows: set[int],
) -> dict[str, object]:
    candidate_rows = []
    partial_conflicts = []
    for row in sorted(workbook_snapshot.rows, key=lambda item: item.row_index):
        related_match = (
            _canonicalize_workbook_lc(row.values.get(mapping.lc_sc_no, ""))
            == document.related_export_lc_number
        )
        export_amount = _parse_workbook_decimal(row.values.get(mapping.export_amount, ""))
        btb_value = _parse_workbook_decimal(row.values.get(mapping.import_amount, ""))
        btb_no = row.values.get(mapping.btb_lc_no, "").strip()
        issue_date_raw = row.values.get(mapping.btb_lc_issue_date, "").strip()
        import_amount_raw = row.values.get(mapping.import_amount, "").strip()
        partial = related_match and (bool(btb_no) != bool(import_amount_raw))
        up_blank = not row.values.get(mapping.up_no, "").strip()
        issue_date_blank = not issue_date_raw
        targets_blank = not btb_no and issue_date_blank and not import_amount_raw
        value_ratio = None
        value_eligible = False
        if export_amount is not None and document.btb_lc_value is not None and export_amount > 0:
            value_ratio = document.btb_lc_value / export_amount
            value_eligible = Decimal("0.40") <= value_ratio <= Decimal("0.80")
        evidence = {
            "row_index": row.row_index,
            "related_export_lc_raw": row.values.get(mapping.lc_sc_no, ""),
            "related_export_lc_canonical": _canonicalize_workbook_lc(row.values.get(mapping.lc_sc_no, "")),
            "related_export_lc_matches": related_match,
            "up_no_blank": up_blank,
            "btb_lc_no_blank": not btb_no,
            "btb_lc_issue_date_blank": issue_date_blank,
            "import_amount_blank": not import_amount_raw,
            "partial_import_target_state": partial,
            "reserved_in_run": row.row_index in reserved_rows,
            "export_amount_raw": row.values.get(mapping.export_amount, ""),
            "export_amount_canonical": str(export_amount) if export_amount is not None else None,
            "value_ratio": str(value_ratio) if value_ratio is not None else None,
            "value_eligible_40_to_80": value_eligible,
        }
        if related_match:
            candidate_rows.append(evidence)
        if partial:
            partial_conflicts.append(evidence)
    if partial_conflicts:
        return {
            "status": "hard_block",
            "candidate_rows": candidate_rows,
            "reason": "A matching related-export-LC row has a partial import target state.",
            "discrepancy": {
                "code": "import_workbook_candidate_invalid",
                "severity": "hard_block",
                "message": "Matching workbook row has exactly one import target populated.",
                "details": {"partial_conflicts": partial_conflicts},
            },
        }

    eligible = [
        candidate
        for candidate in candidate_rows
        if candidate["related_export_lc_matches"]
        and candidate["up_no_blank"]
        and candidate["btb_lc_no_blank"]
        and candidate["btb_lc_issue_date_blank"]
        and candidate["import_amount_blank"]
        and not candidate["reserved_in_run"]
        and candidate["value_eligible_40_to_80"]
    ]
    if not eligible:
        return {
            "status": "hard_block",
            "candidate_rows": candidate_rows,
            "reason": "No qualified workbook row was available for this import BTB LC.",
            "discrepancy": {
                "code": "import_no_qualified_workbook_row",
                "severity": "hard_block",
                "message": "No workbook row matched related export LC, blank target, UP blank, reservation, and value rules.",
                "details": {
                    "btb_lc_number": document.btb_lc_number,
                    "btb_lc_value": document.btb_lc_value_text,
                    "related_export_lc_number": document.related_export_lc_number,
                    "candidate_rows": candidate_rows,
                },
            },
        }
    selected = sorted(
        eligible,
        key=lambda candidate: (
            -Decimal(str(candidate["export_amount_canonical"])),
            int(candidate["row_index"]),
        ),
    )[0]
    return {
        "status": "selected",
        "selected_row_index": selected["row_index"],
        "candidate_rows": candidate_rows,
    }


def _classify_workbook_duplicate(
    *,
    document: ImportBTBLCDocument,
    workbook_snapshot: WorkbookSnapshot,
    mapping: ImportBTBLCHeaderMapping,
) -> dict[str, object] | None:
    matches = []
    for row in sorted(workbook_snapshot.rows, key=lambda item: item.row_index):
        raw_btb = row.values.get(mapping.btb_lc_no, "").strip()
        if raw_btb != document.btb_lc_number:
            continue
        import_amount = _parse_workbook_decimal(row.values.get(mapping.import_amount, ""))
        issue_date = _normalize_date_value(row.values.get(mapping.btb_lc_issue_date, ""))
        related = _canonicalize_workbook_lc(row.values.get(mapping.lc_sc_no, ""))
        matches.append(
            {
                "row_index": row.row_index,
                "btb_lc_no": raw_btb,
                "related_export_lc_raw": row.values.get(mapping.lc_sc_no, ""),
                "related_export_lc_canonical": related,
                "import_amount_raw": row.values.get(mapping.import_amount, ""),
                "import_amount_canonical": str(import_amount) if import_amount is not None else None,
                "btb_lc_issue_date_raw": row.values.get(mapping.btb_lc_issue_date, ""),
                "btb_lc_issue_date_canonical": issue_date,
                "matches_related_export_lc": related == document.related_export_lc_number,
                "matches_import_amount": import_amount == document.btb_lc_value,
                "matches_btb_lc_issue_date": (
                    issue_date is None or issue_date == document.btb_lc_date
                ),
            }
        )
    if not matches:
        return None
    exact = [
        match
        for match in matches
        if match["matches_related_export_lc"]
        and match["matches_import_amount"]
        and match["matches_btb_lc_issue_date"]
    ]
    if len(matches) == 1 and len(exact) == 1:
        return {
            "status": "duplicate",
            "candidate_rows": matches,
            "discrepancy": {
                "code": "import_duplicate_document_in_workbook",
                "severity": "warning",
                "message": "Import BTB LC is already recorded in the workbook with matching evidence.",
                "details": {"workbook_duplicate_row": exact[0]},
            },
        }
    return {
        "status": "hard_block",
        "candidate_rows": matches,
        "discrepancy": {
            "code": "import_workbook_duplicate_unverifiable",
            "severity": "hard_block",
            "message": "Workbook contains BTB LC duplicate evidence that cannot be verified as one exact duplicate.",
            "details": {"workbook_duplicate_rows": matches},
        },
    }


def _base_document_outcome(document: ImportBTBLCDocument) -> dict[str, object]:
    return {
        "document_id": document.document_id,
        "source_path": document.source_path,
        "filename": document.filename,
        "file_sha256": document.file_sha256,
        "snapshot_index": document.snapshot_index,
        "attachment_index": document.attachment_index,
        "extracted_fields": {
            "btb_lc_number": document.btb_lc_number,
            "btb_lc_date": document.btb_lc_date,
            "btb_lc_value": document.btb_lc_value_text,
            "currency": document.currency,
            "seller_pi_numbers": list(document.seller_pi_numbers),
            "related_export_lc_number": document.related_export_lc_number,
        },
        "bank_detection": document.extraction_artifact.get("bank_detection"),
        "filename_comparison": document.extraction_artifact.get("filename_comparison"),
        "decision": "pass",
        "write_disposition": "not_staged",
        "selected_row_index": None,
        "candidate_rows": [],
        "staged_write_operations": [],
        "warnings": list(document.extraction_artifact.get("warnings", [])),
        "hard_block_discrepancies": [],
        "decision_reasons": [],
    }


def _blocked_document_outcome(
    *,
    document: ImportBTBLCDocument,
    code: str,
    message: str,
    details: dict[str, object],
) -> dict[str, object]:
    outcome = _base_document_outcome(document)
    outcome["decision"] = "hard_block"
    outcome["hard_block_discrepancies"] = [
        {
            "code": code,
            "severity": "hard_block",
            "message": message,
            "details": details,
        }
    ]
    outcome["decision_reasons"] = [message]
    return outcome


def _document_from_artifact(
    *,
    artifact: dict[str, object],
    snapshot_index: int,
    attachment_index: int | None,
) -> ImportBTBLCDocument:
    source = artifact.get("source")
    if not isinstance(source, dict):
        raise ValueError("Import extraction artifact is missing source metadata")
    fields = artifact.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("Import extraction artifact is missing field payloads")
    btb_value_text = _field_canonical(fields, "btb_lc_value")
    return ImportBTBLCDocument(
        document_id=f"import-doc-{snapshot_index:04d}-{canonical_json_hash(source)[:12]}",
        source_path=str(source.get("path") or ""),
        filename=str(source.get("filename") or Path(str(source.get("path") or "")).name),
        file_sha256=str(source.get("file_sha256") or ""),
        snapshot_index=snapshot_index,
        attachment_index=attachment_index,
        extraction_artifact=artifact,
        btb_lc_number=_field_canonical(fields, "btb_lc_number"),
        btb_lc_date=_field_canonical(fields, "btb_lc_date"),
        btb_lc_value=_parse_canonical_decimal(btb_value_text),
        btb_lc_value_text=btb_value_text,
        currency=_field_canonical(fields, "currency"),
        seller_pi_numbers=tuple(_field_canonical_list(fields, "seller_pi_numbers")),
        related_export_lc_number=_field_canonical(fields, "related_export_lc_number"),
    )


def _load_or_extract_artifact(source: Path, *, extraction_directory: Path) -> dict[str, object]:
    if source.suffix.casefold() == ".json":
        with source.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError(f"Import extraction artifact must be a JSON object: {source}")
        return payload
    artifact = extract_import_btb_lc_pdf(pdf_path=source)
    output_path = extraction_directory / f"{source.name}.import-btb-lc.json"
    atomic_write_text(output_path, pretty_json_dumps(artifact))
    return artifact


def _resolve_import_inputs(input_path: Path) -> list[Path]:
    resolved = input_path.resolve()
    if resolved.is_file():
        if resolved.suffix.casefold() not in {".pdf", ".json"}:
            raise ValueError(f"Input file must be a PDF or import extraction JSON: {input_path}")
        return [resolved]
    if not resolved.is_dir():
        raise ValueError(f"Input path does not exist: {input_path}")
    paths = sorted(
        (
            path.resolve()
            for path in resolved.iterdir()
            if path.is_file() and path.suffix.casefold() in {".pdf", ".json"}
        ),
        key=lambda path: (path.suffix.casefold() != ".pdf", path.name.casefold(), str(path)),
    )
    if not paths:
        raise ValueError(f"Input directory contains no PDF or JSON files: {input_path}")
    return paths


def load_import_workbook_snapshot(
    *,
    workbook_json: Path | None,
    workbook_path: Path | None,
) -> WorkbookSnapshot:
    if workbook_json is not None and workbook_path is not None:
        raise ValueError("Choose either --workbook-json or --workbook, not both for snapshot input")
    if workbook_json is not None:
        from project.workbook import JsonManifestWorkbookSnapshotProvider

        return JsonManifestWorkbookSnapshotProvider(workbook_json).load_snapshot()
    if workbook_path is not None:
        return XLWingsWorkbookSnapshotProvider(workbook_path).load_snapshot()
    raise ValueError("Import BTB LC workflow requires --workbook-json or --workbook")


def _field_canonical(fields: dict[str, object], field_name: str) -> str | None:
    field = fields.get(field_name)
    if not isinstance(field, dict):
        return None
    canonical = field.get("canonical")
    return canonical if isinstance(canonical, str) and canonical.strip() else None


def _field_canonical_list(fields: dict[str, object], field_name: str) -> list[str]:
    field = fields.get(field_name)
    if not isinstance(field, dict):
        return []
    canonical = field.get("canonical")
    if not isinstance(canonical, list):
        return []
    return [item for item in canonical if isinstance(item, str) and item.strip()]


def _allocation_sort_key(document: ImportBTBLCDocument) -> tuple[object, ...]:
    value_sort = -document.btb_lc_value if document.btb_lc_value is not None else Decimal("0")
    return (
        document.related_export_lc_number or "",
        value_sort,
        document.btb_lc_number or "",
        document.snapshot_index,
        document.attachment_index if document.attachment_index is not None else 999999,
        document.source_path.casefold(),
    )


def _same_run_signature(document: ImportBTBLCDocument) -> tuple[object, ...]:
    return (
        document.btb_lc_number,
        document.btb_lc_date,
        document.btb_lc_value_text,
        document.currency,
        document.seller_pi_numbers,
        document.related_export_lc_number,
    )


def _canonicalize_workbook_lc(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    direct = _canonicalize_extracted_related_lc(text, None)
    if direct is not None:
        return direct
    for hint in ("LC", "SC"):
        candidate = _canonicalize_extracted_related_lc(text, hint)
        if candidate is not None:
            return candidate
    return None


def _parse_canonical_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _parse_workbook_decimal(value: object) -> Decimal | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidates = [text, text.replace(",", "")]
    if "," in text and "." not in text:
        candidates.append(text.replace(",", "."))
    for candidate in candidates:
        try:
            return Decimal(candidate)
        except InvalidOperation:
            continue
    return None


def _resolve_header(
    headers: list[WorkbookHeader],
    required_text: str,
    aliases: tuple[str, ...] = (),
) -> int | None:
    matches = _resolve_header_candidates(headers, required_text, aliases=aliases)
    return matches[0] if len(matches) == 1 else None


def _resolve_header_candidates(
    headers: list[WorkbookHeader],
    required_text: str,
    aliases: tuple[str, ...] = (),
    required_column_index: int | None = None,
) -> list[int]:
    allowed = {_normalize_header(required_text), *(_normalize_header(alias) for alias in aliases)}
    matches = [
        header.column_index
        for header in headers
        if _normalize_header(header.text) in allowed
        and (required_column_index is None or header.column_index == required_column_index)
    ]
    return matches


def _normalize_header(value: str) -> str:
    return " ".join(value.strip().casefold().replace(".", "").split())


def _import_column_index_by_key(mapping: ImportBTBLCHeaderMapping) -> dict[str, int]:
    return {
        "btb_lc_no": mapping.btb_lc_no,
        "btb_lc_issue_date": mapping.btb_lc_issue_date,
        "import_amount": mapping.import_amount,
    }


def _normalize_cell_value(value: object) -> str | None:
    if value is None:
        return None
    return str(value).strip()


def _collect_import_post_write_probes(
    *,
    session,
    mapping: ImportBTBLCHeaderMapping,
    staged_write_plan: list[WriteOperation],
) -> list[dict[str, object]]:
    column_by_key = _import_column_index_by_key(mapping)
    probes: list[dict[str, object]] = []
    for operation in staged_write_plan:
        column_index = column_by_key[operation.column_key]
        observed = session.read_cell(
            sheet_name=operation.sheet_name,
            row_index=operation.row_index,
            column_index=column_index,
        )
        expected = operation.expected_post_write_value
        classification = (
            "matches_post_write"
            if _post_write_value_matches(observed, expected)
            else "mismatch_unknown"
        )
        probes.append(
            {
                "write_operation_id": operation.write_operation_id,
                "run_id": operation.run_id,
                "mail_id": operation.mail_id,
                "probe_stage": "post_write",
                "sheet_name": operation.sheet_name,
                "row_index": operation.row_index,
                "column_key": operation.column_key,
                "column_index": column_index,
                "expected_post_write_value": expected,
                "observed_value": observed,
                "classification": classification,
            }
        )
    return probes


def _post_write_value_matches(observed: object, expected: object) -> bool:
    observed_date = _normalize_date_value(observed)
    expected_date = _normalize_date_value(expected)
    if observed_date is not None and expected_date is not None:
        return observed_date == expected_date
    observed_decimal = _parse_workbook_decimal(observed)
    expected_decimal = _parse_workbook_decimal(expected)
    if observed_decimal is not None and expected_decimal is not None:
        return observed_decimal == expected_decimal
    return str(observed or "").strip() == str(expected or "").strip()


def _coerce_import_write_value(value: object, *, number_format: str | None) -> object:
    if value is None or number_format is None:
        return value
    normalized_format = number_format.lower()
    if "d" not in normalized_format or "y" not in normalized_format:
        return value
    normalized_date = _normalize_date_value(value)
    if normalized_date is None:
        return value
    return date.fromisoformat(normalized_date)


def _normalize_date_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed_datetime = datetime.fromisoformat(text)
        return parsed_datetime.date().isoformat()
    except ValueError:
        pass
    for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(text, date_format).date().isoformat()
        except ValueError:
            continue
    return None


def _summarize_outcomes(outcomes: list[dict[str, object]]) -> dict[str, int]:
    return {
        "pass": sum(1 for outcome in outcomes if outcome.get("decision") == "pass"),
        "warning": sum(1 for outcome in outcomes if outcome.get("decision") == "warning"),
        "hard_block": sum(1 for outcome in outcomes if outcome.get("decision") == "hard_block"),
        "staged": sum(1 for outcome in outcomes if outcome.get("write_disposition") == "new_writes_staged"),
        "duplicate_only": sum(1 for outcome in outcomes if outcome.get("write_disposition") == "duplicate_only_noop"),
    }


def _overall_decision_from_document_outcomes(
    outcomes: Iterable[dict[str, object]],
    *,
    write_execution: dict[str, object] | None = None,
) -> str:
    outcome_list = list(outcomes)
    if any(outcome.get("decision") == "hard_block" for outcome in outcome_list):
        return "hard_block"
    if write_execution and write_execution.get("status") in {
        "hard_blocked_no_write",
        "uncertain_not_committed",
    }:
        return "hard_block"
    if any(outcome.get("decision") == "warning" for outcome in outcome_list):
        return "warning"
    return "pass"
