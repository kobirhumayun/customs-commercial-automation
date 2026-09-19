from __future__ import annotations

from datetime import date, datetime
import webbrowser
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from pathlib import Path
import re

from project.models import (
    DiscrepancyReport,
    FinalDecision,
    MailOutcomeRecord,
    MailProcessingStatus,
    MailReport,
    RunReport,
    WorkflowId,
    WriteOperation,
)
from project.reporting.schemas import REPORT_SCHEMA_VERSION
from project.storage import write_json
from project.storage.artifacts import atomic_write_text
from project.utils.hashing import canonical_json_hash
from project.utils.ids import build_mail_id, build_write_operation_id
from project.utils.json import to_jsonable
from project.utils.time import utc_timestamp, validate_timezone
from project.workbook import WorkbookRow, WorkbookSnapshot, resolve_bb_dashboard_header_mapping
from project.workflows.bb_dashboard_verification.providers import (
    DashboardFamilySnapshot,
    DashboardLookupProvider,
    DashboardLookupResult,
    normalize_dashboard_search_key,
)
from project.workflows.validation import ValidationBatchResult


_COMPLIANT_VALUES = {"OK", "OK (KGS)"}
_FIRST_LINE_PREFIXES = ("EXP", "IP")
_PIONEER_BENEFICIARY = "PIONEER DENIM LIMITED"
_WHITESPACE_RE = re.compile(r"\s+")
_SPECIAL_RE = re.compile(r"[^A-Z0-9]+")
_LTD_OR_LIMITED_RE = re.compile(r"\b(?:LTD|LIMITED)\b")
_DATE_NUMBER_FORMAT = "dd/mm/yyyy"
_DEFAULT_DECIMAL_TOLERANCE = Decimal("0.01")
_NET_WEIGHT_TOLERANCE = Decimal("0.8")
_MAX_SHIPMENT_DATE_OFFSET_DAYS = 250
_EXPIRY_MIN_OFFSET_DAYS = 5
_EXPIRY_MAX_OFFSET_DAYS = 90
_MIN_LC_VALUE_EXCESS = Decimal("100.00")
_MIN_EXCESS_QUANTITY_RATIO = Decimal("0.20")
_MAX_EXCESS_QUANTITY_RATIO = Decimal("0.80")


@dataclass(slots=True, frozen=True)
class DashboardCandidateRow:
    row_index: int
    sl_no: str
    lc_sc_no: str
    lc_sc_key: str
    master_lc_values: list[str]
    dashboard_status: str
    shipment_date: str
    expiry_date: str
    shipment_date_number_format: str
    expiry_date_number_format: str
    number_formats: dict[int, str]


@dataclass(slots=True, frozen=True)
class DashboardCandidateFamily:
    family_id: str
    lc_sc_no: str
    lc_sc_key: str
    row_indexes: list[int]
    sl_no_values: list[str]
    master_lc_values: list[str]
    rows: list[DashboardCandidateRow]


@dataclass(slots=True, frozen=True)
class ERPFamilyAggregate:
    lc_sc_no: str
    lc_sc_key: str
    buyer_name: str
    lc_date: str
    ship_date: str
    expiry_date: str
    current_lc_value: Decimal
    lc_qty: Decimal
    net_weight: Decimal | None
    ship_remarks: str | None
    source_row_count: int


@dataclass(slots=True, frozen=True)
class BBDashboardVerificationResult:
    validation_result: ValidationBatchResult
    report_payload: dict[str, object]
    report_html: str


def validate_bb_dashboard_verification_run(
    *,
    run_report: RunReport,
    workbook_snapshot: WorkbookSnapshot | None,
    erp_rows: list,
    dashboard_provider: DashboardLookupProvider,
    live_workbook_path: Path | None = None,
) -> BBDashboardVerificationResult:
    if run_report.workflow_id != WorkflowId.BB_DASHBOARD_VERIFICATION:
        raise ValueError("Bangladesh Bank dashboard validation requires workflow_id=bb_dashboard_verification")
    if workbook_snapshot is None:
        raise ValueError("Bangladesh Bank dashboard verification requires --workbook-json or --live-workbook")
    if dashboard_provider is None:
        raise ValueError("Bangladesh Bank dashboard verification requires a dashboard lookup provider")

    header_mapping = resolve_bb_dashboard_header_mapping(workbook_snapshot)
    if header_mapping is None:
        discrepancy = _build_discrepancy(
            run_report=run_report,
            code="workbook_header_mapping_invalid",
            message="Required workbook headers could not be resolved for Bangladesh Bank dashboard verification.",
            details={"sheet_name": workbook_snapshot.sheet_name},
        )
        validation_result = ValidationBatchResult(
            run_report=replace(
                run_report,
                summary={"pass": 0, "warning": 0, "hard_block": 1},
            ),
            mail_outcomes=[],
            mail_reports=[],
            discrepancy_reports=[discrepancy],
            staged_write_plan=[],
            target_probes=[],
            commit_marker=None,
        )
        return BBDashboardVerificationResult(
            validation_result=validation_result,
            report_payload=_build_report_payload(
                run_report=validation_result.run_report,
                families=[],
            ),
            report_html=_build_report_html(
                report_payload=_build_report_payload(
                    run_report=validation_result.run_report,
                    families=[],
                )
            ),
        )

    sl_no_values_by_row = _resolve_sl_no_values_by_row(
        workbook_snapshot=workbook_snapshot,
        sl_no_column_index=header_mapping["sl_no"],
        live_workbook_path=live_workbook_path,
    )
    master_lc_values_by_row = _resolve_master_lc_values_by_row(
        workbook_snapshot=workbook_snapshot,
        master_lc_column_index=header_mapping["master_lc_no"],
        live_workbook_path=live_workbook_path,
    )
    candidate_families = _build_candidate_families(
        workbook_snapshot=workbook_snapshot,
        header_mapping=header_mapping,
        sl_no_values_by_row=sl_no_values_by_row,
        master_lc_values_by_row=master_lc_values_by_row,
    )
    erp_rows_by_family = _group_erp_rows_by_family(erp_rows)

    discrepancy_reports: list[DiscrepancyReport] = []
    mail_outcomes: list[MailOutcomeRecord] = []
    mail_reports: list[MailReport] = []
    staged_write_plan: list[WriteOperation] = []
    summary = {"pass": 0, "warning": 0, "hard_block": 0}
    report_families: list[dict[str, object]] = []

    for family in candidate_families:
        if any(not value.strip() for value in family.sl_no_values):
            discrepancy = _build_discrepancy(
                run_report=run_report,
                code="bb_dashboard_family_input_invalid",
                message=f"One or more filtered workbook rows in family {family.lc_sc_no} are missing SL.No. values.",
                mail_id=family.family_id,
                details={"lc_sc_no": family.lc_sc_no, "row_indexes": family.row_indexes},
            )
            discrepancy_reports.append(discrepancy)
            summary["hard_block"] += 1
            family_operations = _build_family_write_operations(
                run_report=run_report,
                family=family,
                sheet_name=workbook_snapshot.sheet_name,
                final_status=discrepancy.message,
                writes_dates=False,
                ship_date="",
                expiry_date="",
            )
            staged_write_plan.extend(family_operations)
            mail_outcomes.append(
                _build_family_mail_outcome(
                    run_report=run_report,
                    family=family,
                    family_index=len(mail_outcomes),
                    final_decision=FinalDecision.HARD_BLOCK,
                    decision_reasons=[discrepancy.message],
                    staged_write_operations=family_operations,
                )
            )
            report_families.append(
                _build_report_family(
                    family=family,
                    final_decision=FinalDecision.HARD_BLOCK,
                    final_workbook_value=discrepancy.message,
                    decision_reasons=[discrepancy.message],
                    search_attempts=[],
                    erp_aggregate=None,
                    dashboard_snapshot=None,
                    written_shipment_date=None,
                    written_expiry_date=None,
                )
            )
            continue

        aggregate, family_discrepancy = _build_erp_family_aggregate(
            run_report=run_report,
            family=family,
            erp_rows=erp_rows_by_family.get(family.lc_sc_key, []),
        )
        if family_discrepancy is not None:
            discrepancy_reports.append(family_discrepancy)
            summary["hard_block"] += 1
            family_operations = _build_family_write_operations(
                run_report=run_report,
                family=family,
                sheet_name=workbook_snapshot.sheet_name,
                final_status=family_discrepancy.message,
                writes_dates=False,
                ship_date="",
                expiry_date="",
            )
            staged_write_plan.extend(family_operations)
            mail_outcomes.append(
                _build_family_mail_outcome(
                    run_report=run_report,
                    family=family,
                    family_index=len(mail_outcomes),
                    final_decision=FinalDecision.HARD_BLOCK,
                    decision_reasons=[family_discrepancy.message],
                    staged_write_operations=family_operations,
                )
            )
            report_families.append(
                _build_report_family(
                    family=family,
                    final_decision=FinalDecision.HARD_BLOCK,
                    final_workbook_value=family_discrepancy.message,
                    decision_reasons=[family_discrepancy.message],
                    search_attempts=[],
                    erp_aggregate=None,
                    dashboard_snapshot=None,
                    written_shipment_date=None,
                    written_expiry_date=None,
                )
            )
            continue

        search_keys = _build_search_keys(
            ship_remarks=aggregate.ship_remarks,
            workbook_lc_sc_no=family.lc_sc_no,
        )
        lookup_result: DashboardLookupResult | None = None
        try:
            lookup_result = dashboard_provider.lookup_family(search_keys=search_keys)
        except Exception as exc:
            discrepancy = _build_discrepancy(
                run_report=run_report,
                code="bb_dashboard_fetch_runtime_error",
                message=f"Dashboard fetch failed for family {family.lc_sc_no}.",
                mail_id=family.family_id,
                details={
                    "lc_sc_no": family.lc_sc_no,
                    "search_keys": search_keys,
                    "error": str(exc),
                },
            )
            discrepancy_reports.append(discrepancy)
            summary["hard_block"] += 1
            family_operations = _build_family_write_operations(
                run_report=run_report,
                family=family,
                sheet_name=workbook_snapshot.sheet_name,
                final_status=discrepancy.message,
                writes_dates=False,
                ship_date="",
                expiry_date="",
            )
            staged_write_plan.extend(family_operations)
            mail_outcomes.append(
                _build_family_mail_outcome(
                    run_report=run_report,
                    family=family,
                    family_index=len(mail_outcomes),
                    final_decision=FinalDecision.HARD_BLOCK,
                    decision_reasons=[discrepancy.message],
                    staged_write_operations=family_operations,
                )
            )
            report_families.append(
                _build_report_family(
                    family=family,
                    final_decision=FinalDecision.HARD_BLOCK,
                    final_workbook_value=discrepancy.message,
                    decision_reasons=[discrepancy.message],
                    search_attempts=[to_jsonable(item) for item in lookup_result.attempts] if lookup_result is not None else [],
                    erp_aggregate=aggregate,
                    dashboard_snapshot=None,
                    written_shipment_date=_format_workbook_date(aggregate.ship_date),
                    written_expiry_date=_format_workbook_date(aggregate.expiry_date),
                )
            )
            continue

        if lookup_result.outcome == "fetch_error":
            discrepancy = _build_discrepancy(
                run_report=run_report,
                code="bb_dashboard_fetch_runtime_error",
                message=f"Dashboard fetch failed for family {family.lc_sc_no}.",
                mail_id=family.family_id,
                details={
                    "lc_sc_no": family.lc_sc_no,
                    "search_attempts": [
                        {
                            "search_key": attempt.search_key,
                            "outcome": attempt.outcome,
                            "message": attempt.message,
                        }
                        for attempt in lookup_result.attempts
                    ],
                    "error": lookup_result.message,
                },
            )
            discrepancy_reports.append(discrepancy)
            summary["hard_block"] += 1
            family_operations = _build_family_write_operations(
                run_report=run_report,
                family=family,
                sheet_name=workbook_snapshot.sheet_name,
                final_status=discrepancy.message,
                writes_dates=True,
                ship_date=aggregate.ship_date,
                expiry_date=aggregate.expiry_date,
            )
            staged_write_plan.extend(family_operations)
            mail_outcomes.append(
                _build_family_mail_outcome(
                    run_report=run_report,
                    family=family,
                    family_index=len(mail_outcomes),
                    final_decision=FinalDecision.HARD_BLOCK,
                    decision_reasons=[discrepancy.message],
                    staged_write_operations=family_operations,
                )
            )
            report_families.append(
                _build_report_family(
                    family=family,
                    final_decision=FinalDecision.HARD_BLOCK,
                    final_workbook_value=discrepancy.message,
                    decision_reasons=[discrepancy.message],
                    search_attempts=[
                        {
                            "search_key": attempt.search_key,
                            "outcome": attempt.outcome,
                            "message": attempt.message,
                        }
                        for attempt in lookup_result.attempts
                    ],
                    erp_aggregate=aggregate,
                    dashboard_snapshot=None,
                    written_shipment_date=_format_workbook_date(aggregate.ship_date),
                    written_expiry_date=_format_workbook_date(aggregate.expiry_date),
                )
            )
            continue

        final_decision, final_status, decision_reasons, dashboard_snapshot, writes_dates = _evaluate_lookup_result(
            family=family,
            aggregate=aggregate,
            lookup_result=lookup_result,
        )
        family_operations = _build_family_write_operations(
            run_report=run_report,
            family=family,
            sheet_name=workbook_snapshot.sheet_name,
            final_status=final_status,
            writes_dates=writes_dates,
            ship_date=aggregate.ship_date,
            expiry_date=aggregate.expiry_date,
        )
        summary[final_decision.value] += 1
        staged_write_plan.extend(family_operations)
        mail_outcomes.append(
            _build_family_mail_outcome(
                run_report=run_report,
                family=family,
                family_index=len(mail_outcomes),
                final_decision=final_decision,
                decision_reasons=decision_reasons,
                staged_write_operations=family_operations,
            )
        )
        report_families.append(
            _build_report_family(
                family=family,
                final_decision=final_decision,
                final_workbook_value=final_status,
                decision_reasons=decision_reasons,
                search_attempts=[
                    {
                        "search_key": attempt.search_key,
                        "outcome": attempt.outcome,
                        "message": attempt.message,
                    }
                    for attempt in lookup_result.attempts
                ],
                erp_aggregate=aggregate,
                dashboard_snapshot=dashboard_snapshot,
                written_shipment_date=_format_workbook_date(aggregate.ship_date) if writes_dates else None,
                written_expiry_date=_format_workbook_date(aggregate.expiry_date) if writes_dates else None,
            )
        )

    for outcome in mail_outcomes:
        mail_reports.append(
            MailReport(
                run_id=run_report.run_id,
                mail_id=outcome.mail_id,
                workflow_id=run_report.workflow_id,
                rule_pack_id=run_report.rule_pack_id,
                rule_pack_version=run_report.rule_pack_version,
                applied_rule_ids=[],
                final_decision=outcome.final_decision or FinalDecision.PASS,
                decision_reasons=list(outcome.decision_reasons),
                file_numbers_extracted=[],
                saved_documents=[],
                staged_write_operations=list(outcome.staged_write_operations),
                discrepancies=[],
            )
        )

    updated_run_report = replace(
        run_report,
        summary=summary,
        staged_write_plan_hash=canonical_json_hash(to_jsonable(staged_write_plan)),
    )
    validation_result = ValidationBatchResult(
        run_report=updated_run_report,
        mail_outcomes=mail_outcomes,
        mail_reports=mail_reports,
        discrepancy_reports=discrepancy_reports,
        staged_write_plan=staged_write_plan,
        target_probes=[],
        commit_marker=None,
    )
    report_payload = _build_report_payload(
        run_report=updated_run_report,
        families=report_families,
    )
    return BBDashboardVerificationResult(
        validation_result=validation_result,
        report_payload=report_payload,
        report_html=_build_report_html(report_payload=report_payload),
    )


def persist_bb_dashboard_verification_report(
    *,
    run_root: Path,
    report_payload: dict[str, object],
    report_html: str,
) -> tuple[Path, Path]:
    json_path = run_root / "bb_dashboard_verification_report.json"
    html_path = run_root / "bb_dashboard_verification_report.html"
    write_json(json_path, report_payload)
    atomic_write_text(html_path, report_html)
    return json_path, html_path


def open_bb_dashboard_verification_report_in_browser(*, html_path: Path) -> None:
    if not html_path.exists():
        raise FileNotFoundError(str(html_path))
    if not webbrowser.open(html_path.resolve().as_uri()):
        raise RuntimeError(f"Browser open request was not acknowledged for {html_path}")


def _build_candidate_families(
    *,
    workbook_snapshot: WorkbookSnapshot,
    header_mapping: dict[str, int],
    sl_no_values_by_row: dict[int, str],
    master_lc_values_by_row: dict[int, list[str]],
) -> list[DashboardCandidateFamily]:
    families: dict[str, list[DashboardCandidateRow]] = {}
    ordered_keys: list[str] = []
    for row in workbook_snapshot.rows:
        candidate = _build_candidate_row(
            row=row,
            header_mapping=header_mapping,
            sl_no_values_by_row=sl_no_values_by_row,
            master_lc_values_by_row=master_lc_values_by_row,
        )
        if candidate is None:
            continue
        if candidate.lc_sc_key not in families:
            families[candidate.lc_sc_key] = []
            ordered_keys.append(candidate.lc_sc_key)
        families[candidate.lc_sc_key].append(candidate)

    results: list[DashboardCandidateFamily] = []
    for family_key in ordered_keys:
        rows = families[family_key]
        master_lc_values = _unique_preserve_order(
            value
            for row in rows
            for value in row.master_lc_values
        )
        sl_no_values = [row.sl_no for row in rows]
        family_label = rows[0].lc_sc_no
        results.append(
            DashboardCandidateFamily(
                family_id=build_mail_id(f"bb_dashboard|{family_key}"),
                lc_sc_no=family_label,
                lc_sc_key=family_key,
                row_indexes=[row.row_index for row in rows],
                sl_no_values=sl_no_values,
                master_lc_values=master_lc_values,
                rows=rows,
            )
        )
    return results


def _build_candidate_row(
    *,
    row: WorkbookRow,
    header_mapping: dict[str, int],
    sl_no_values_by_row: dict[int, str],
    master_lc_values_by_row: dict[int, list[str]],
) -> DashboardCandidateRow | None:
    up_no = row.values.get(header_mapping["up_no"], "").strip()
    if up_no:
        return None

    shared_value = row.values.get(header_mapping["ud_ip_shared"], "")
    first_non_empty_line = _first_non_empty_line(shared_value)
    if not first_non_empty_line:
        return None
    if any(first_non_empty_line.upper().startswith(prefix) for prefix in _FIRST_LINE_PREFIXES):
        return None

    dashboard_status = row.values.get(header_mapping["dashboard_status"], "").strip()
    if dashboard_status in _COMPLIANT_VALUES:
        return None

    raw_lc_sc_no = row.values.get(header_mapping["lc_sc_no"], "").strip()
    lc_sc_key = _normalize_lc_family_key(raw_lc_sc_no)
    if not lc_sc_key:
        return None

    sl_no = sl_no_values_by_row.get(row.row_index, "").strip()
    master_lc_values = master_lc_values_by_row.get(row.row_index)
    if master_lc_values is None:
        master_lc_values = _split_multiline_values(row.values.get(header_mapping["master_lc_no"], ""))
    return DashboardCandidateRow(
        row_index=row.row_index,
        sl_no=sl_no,
        lc_sc_no=raw_lc_sc_no,
        lc_sc_key=lc_sc_key,
        master_lc_values=master_lc_values,
        dashboard_status=dashboard_status,
        shipment_date=row.values.get(header_mapping["shipment_date"], "").strip(),
        expiry_date=row.values.get(header_mapping["expiry_date"], "").strip(),
        shipment_date_number_format=(
            str(row.number_formats.get(header_mapping["shipment_date"], "")).strip()
            or _DATE_NUMBER_FORMAT
        ),
        expiry_date_number_format=(
            str(row.number_formats.get(header_mapping["expiry_date"], "")).strip()
            or _DATE_NUMBER_FORMAT
        ),
        number_formats=dict(row.number_formats),
    )


def _resolve_sl_no_values_by_row(
    *,
    workbook_snapshot: WorkbookSnapshot,
    sl_no_column_index: int,
    live_workbook_path: Path | None,
) -> dict[int, str]:
    row_indexes = {row.row_index for row in workbook_snapshot.rows}
    if live_workbook_path is not None:
        return _resolve_live_display_values_by_row(
            workbook_path=live_workbook_path,
            column_index=sl_no_column_index,
            row_indexes=row_indexes,
            field_label="SL.No.",
        )
    rows_by_index = {row.row_index: row for row in workbook_snapshot.rows}
    resolved: dict[int, str] = {}
    for row_index in row_indexes:
        row = rows_by_index.get(row_index)
        if row is None:
            continue
        resolved[row_index] = _stringify_sl_no_text(row.values.get(sl_no_column_index, ""))
    return resolved


def _resolve_master_lc_values_by_row(
    *,
    workbook_snapshot: WorkbookSnapshot,
    master_lc_column_index: int,
    live_workbook_path: Path | None,
) -> dict[int, list[str]]:
    row_indexes = {row.row_index for row in workbook_snapshot.rows}
    if live_workbook_path is not None:
        displayed_values = _resolve_live_display_values_by_row(
            workbook_path=live_workbook_path,
            column_index=master_lc_column_index,
            row_indexes=row_indexes,
            field_label="Master L/C No.",
        )
        return {
            row_index: _split_multiline_values(value)
            for row_index, value in displayed_values.items()
        }

    rows_by_index = {row.row_index: row for row in workbook_snapshot.rows}
    resolved: dict[int, list[str]] = {}
    for row_index in row_indexes:
        row = rows_by_index.get(row_index)
        if row is None:
            continue
        resolved[row_index] = _split_multiline_values(row.values.get(master_lc_column_index, ""))
    return resolved


def _resolve_live_display_values_by_row(
    *,
    workbook_path: Path,
    column_index: int,
    row_indexes: set[int],
    field_label: str,
) -> dict[int, str]:
    if not row_indexes:
        return {}
    try:
        import xlwings  # type: ignore
    except ImportError as exc:
        raise ValueError(
            f"xlwings is required to resolve displayed workbook {field_label} values from the live workbook."
        ) from exc

    app = xlwings.App(visible=False, add_book=False)
    book = None
    try:
        book = app.books.open(str(workbook_path), update_links=False, read_only=True)
        sheet = book.sheets[0]
        return _read_live_display_values(
            sheet=sheet,
            column_index=column_index,
            row_indexes=sorted(row_indexes),
        )
    finally:
        if book is not None:
            book.close()
        app.quit()


def _read_live_display_values(*, sheet, column_index: int, row_indexes: list[int]) -> dict[int, str]:
    if not row_indexes:
        return {}
    resolved: dict[int, str] = {}
    for row_index in row_indexes:
        displayed_value = sheet.range((row_index, column_index)).api.Text
        resolved[row_index] = _stringify_sl_no_text(displayed_value)
    return resolved


def _stringify_sl_no_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return ""
        if "." not in text_value and "e" not in text_value.lower():
            return text_value
        try:
            decimal_value = Decimal(text_value.replace(",", ""))
        except (InvalidOperation, ValueError):
            return text_value
        if decimal_value == decimal_value.to_integral_value():
            return str(int(decimal_value))
        return text_value
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return str(int(value))
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        decimal_value = Decimal(str(value))
        if decimal_value == decimal_value.to_integral_value():
            return str(int(decimal_value))
        return str(value)
    text_value = str(value).strip()
    if not text_value:
        return ""
    try:
        decimal_value = Decimal(text_value.replace(",", ""))
    except (InvalidOperation, ValueError):
        return text_value
    if decimal_value == decimal_value.to_integral_value():
        return str(int(decimal_value))
    return text_value


def _group_erp_rows_by_family(erp_rows: list) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for row in erp_rows:
        family_key = _normalize_lc_family_key(getattr(row, "lc_sc_number", ""))
        if not family_key:
            continue
        grouped.setdefault(family_key, []).append(row)
    return grouped


def _build_erp_family_aggregate(
    *,
    run_report: RunReport,
    family: DashboardCandidateFamily,
    erp_rows: list,
) -> tuple[ERPFamilyAggregate | None, DiscrepancyReport | None]:
    if not erp_rows:
        return None, _build_discrepancy(
            run_report=run_report,
            code="bb_dashboard_family_input_invalid",
            message=f"No ERP rows were available for workbook family {family.lc_sc_no}.",
            mail_id=family.family_id,
            details={"lc_sc_no": family.lc_sc_no, "row_indexes": family.row_indexes},
        )

    deduped_rows = _dedupe_erp_rows(erp_rows)
    buyer_names = _unique_preserve_order(
        _normalize_special_text(getattr(row, "folder_buyer_name", "") or getattr(row, "buyer_name", ""))
        for row in deduped_rows
        if (_normalize_special_text(getattr(row, "folder_buyer_name", "") or getattr(row, "buyer_name", "")))
    )
    lc_dates = _unique_preserve_order(str(getattr(row, "lc_sc_date", "")).strip() for row in deduped_rows if str(getattr(row, "lc_sc_date", "")).strip())
    ship_dates = _unique_preserve_order(str(getattr(row, "ship_date", "")).strip() for row in deduped_rows if str(getattr(row, "ship_date", "")).strip())
    expiry_dates = _unique_preserve_order(str(getattr(row, "expiry_date", "")).strip() for row in deduped_rows if str(getattr(row, "expiry_date", "")).strip())
    ship_remarks = _unique_preserve_order(
        normalize_dashboard_search_key(getattr(row, "ship_remarks", ""))
        for row in deduped_rows
        if normalize_dashboard_search_key(getattr(row, "ship_remarks", ""))
    )
    consistency_issues = _build_erp_consistency_issues(
        buyer_names=buyer_names,
        lc_dates=lc_dates,
        ship_dates=ship_dates,
        expiry_dates=expiry_dates,
        ship_remarks=ship_remarks,
    )
    if consistency_issues:
        issue_text = "; ".join(consistency_issues)
        return None, _build_discrepancy(
            run_report=run_report,
            code="bb_dashboard_family_input_invalid",
            message=(
                f"ERP family inputs were not deterministically consistent for workbook family {family.lc_sc_no}: "
                f"{issue_text}."
            ),
            mail_id=family.family_id,
            details={
                "lc_sc_no": family.lc_sc_no,
                "buyer_names": buyer_names,
                "lc_dates": lc_dates,
                "ship_dates": ship_dates,
                "expiry_dates": expiry_dates,
                "ship_remarks": ship_remarks,
            },
        )

    current_lc_value = _sum_decimal_strings(getattr(row, "current_lc_value", "") for row in deduped_rows)
    lc_qty = _sum_decimal_strings(getattr(row, "lc_qty", "") for row in deduped_rows)
    net_weight = _sum_decimal_strings(
        getattr(row, "net_weight", "")
        for row in deduped_rows
        if str(getattr(row, "net_weight", "")).strip()
    )
    if current_lc_value is None or lc_qty is None:
        return None, _build_discrepancy(
            run_report=run_report,
            code="bb_dashboard_family_input_invalid",
            message=f"ERP family inputs were missing required numeric fields for workbook family {family.lc_sc_no}.",
            mail_id=family.family_id,
            details={"lc_sc_no": family.lc_sc_no},
        )

    return (
        ERPFamilyAggregate(
            lc_sc_no=family.lc_sc_no,
            lc_sc_key=family.lc_sc_key,
            buyer_name=buyer_names[0],
            lc_date=lc_dates[0],
            ship_date=ship_dates[0],
            expiry_date=expiry_dates[0],
            current_lc_value=current_lc_value,
            lc_qty=lc_qty,
            net_weight=net_weight,
            ship_remarks=ship_remarks[0] if ship_remarks else None,
            source_row_count=len(deduped_rows),
        ),
        None,
    )


def _evaluate_lookup_result(
    *,
    family: DashboardCandidateFamily,
    aggregate: ERPFamilyAggregate,
    lookup_result: DashboardLookupResult,
) -> tuple[FinalDecision, str, list[str], DashboardFamilySnapshot | None, bool]:
    if lookup_result.outcome == "no_result":
        search_key = lookup_result.matched_search_key or family.lc_sc_no
        message = f"No dashboard result was found for '{search_key}'."
        return FinalDecision.WARNING, message, [message], None, True
    if lookup_result.outcome == "incomplete_data" or lookup_result.snapshot is None:
        search_key = lookup_result.matched_search_key or family.lc_sc_no
        message = lookup_result.message or f"Dashboard data was incomplete for '{search_key}'."
        return FinalDecision.WARNING, message, [message], lookup_result.snapshot, True

    comparison = _compare_dashboard_snapshot(
        family=family,
        aggregate=aggregate,
        snapshot=lookup_result.snapshot,
    )
    if comparison["status"] == "OK":
        return FinalDecision.PASS, "OK", ["Dashboard verification matched ERP and workbook inputs."], lookup_result.snapshot, True
    if comparison["status"] == "OK (KGS)":
        return FinalDecision.PASS, "OK (KGS)", ["Dashboard quantity matched ERP net weight instead of ERP LC quantity."], lookup_result.snapshot, True
    return FinalDecision.WARNING, str(comparison["status"]), list(comparison["decision_reasons"]), lookup_result.snapshot, True


def _compare_dashboard_snapshot(
    *,
    family: DashboardCandidateFamily,
    aggregate: ERPFamilyAggregate,
    snapshot: DashboardFamilySnapshot,
) -> dict[str, object]:
    mismatch_messages: list[str] = []
    dashboard_beneficiary = _normalize_special_text(snapshot.beneficiary_name)
    if dashboard_beneficiary != _normalize_special_text(_PIONEER_BENEFICIARY):
        mismatch_messages.append(
            f"Beneficiary mismatch: dashboard '{snapshot.beneficiary_name}' != '{_PIONEER_BENEFICIARY}'."
        )

    mismatch_messages.extend(
        _compare_buyer_details(
            buyer_name=aggregate.buyer_name,
            irc_details=snapshot.irc_details,
            erc_details=snapshot.erc_details,
        )
    )

    normalized_snapshot_lc_date = _normalize_date(snapshot.lc_date)
    normalized_snapshot_ship_date = _normalize_date(snapshot.last_date_of_shipment)
    normalized_snapshot_expiry_date = _normalize_date(snapshot.lc_expiry_date)
    normalized_erp_lc_date = _normalize_date(aggregate.lc_date)
    normalized_erp_ship_date = _normalize_date(aggregate.ship_date)
    normalized_erp_expiry_date = _normalize_date(aggregate.expiry_date)

    if normalized_snapshot_lc_date != normalized_erp_lc_date:
        mismatch_messages.append(f"LC Date mismatch: dashboard '{snapshot.lc_date}' != ERP '{aggregate.lc_date}'.")
    if not _date_is_same_or_after_and_within_days(
        dashboard_date=normalized_snapshot_ship_date,
        erp_date=normalized_erp_ship_date,
        max_days=_MAX_SHIPMENT_DATE_OFFSET_DAYS,
    ):
        mismatch_messages.append(
            "Last Date of Shipment mismatch: dashboard "
            f"'{snapshot.last_date_of_shipment}' must be on or after ERP '{aggregate.ship_date}' and no more than "
            f"{_MAX_SHIPMENT_DATE_OFFSET_DAYS} days later."
        )
    if not _date_is_same_or_after(
        dashboard_date=normalized_snapshot_expiry_date,
        erp_date=normalized_erp_expiry_date,
    ):
        mismatch_messages.append(
            f"LC Expiry Date mismatch: dashboard '{snapshot.lc_expiry_date}' must be on or after ERP '{aggregate.expiry_date}'."
        )
    if not _date_is_between_days_after(
        earlier_date=normalized_snapshot_ship_date,
        later_date=normalized_snapshot_expiry_date,
        min_days=_EXPIRY_MIN_OFFSET_DAYS,
        max_days=_EXPIRY_MAX_OFFSET_DAYS,
    ):
        mismatch_messages.append(
            "LC Expiry Date mismatch: dashboard expiry "
            f"'{snapshot.lc_expiry_date}' must be between {_EXPIRY_MIN_OFFSET_DAYS} and {_EXPIRY_MAX_OFFSET_DAYS} days "
            f"after dashboard shipment '{snapshot.last_date_of_shipment}'."
        )
    if not _date_is_between_days_after(
        earlier_date=normalized_erp_ship_date,
        later_date=normalized_erp_expiry_date,
        min_days=_EXPIRY_MIN_OFFSET_DAYS,
        max_days=_EXPIRY_MAX_OFFSET_DAYS,
    ):
        mismatch_messages.append(
            "ERP date window mismatch: ERP expiry "
            f"'{aggregate.expiry_date}' must be between {_EXPIRY_MIN_OFFSET_DAYS} and {_EXPIRY_MAX_OFFSET_DAYS} days "
            f"after ERP shipment '{aggregate.ship_date}'."
        )

    dashboard_lc_value = _parse_decimal(snapshot.lc_value)
    if dashboard_lc_value is None:
        mismatch_messages.append(f"LC Value could not be parsed from dashboard value '{snapshot.lc_value}'.")

    workbook_master_values = {
        _normalize_foreign_lc_reference(value)
        for value in family.master_lc_values
        if _normalize_foreign_lc_reference(value)
    }
    dashboard_foreign_values = {
        _normalize_foreign_lc_reference(value)
        for value in snapshot.foreign_lc_numbers
        if _normalize_foreign_lc_reference(value)
    }
    if not workbook_master_values or not dashboard_foreign_values or workbook_master_values.isdisjoint(dashboard_foreign_values):
        mismatch_messages.append("Related Foreign LC/Contract Information did not overlap workbook Master L/C No. values.")

    quantity_sum = _sum_decimal_strings(snapshot.commodity_quantities)
    if quantity_sum is None:
        mismatch_messages.append("Dashboard quantity rows could not be parsed.")
    if dashboard_lc_value is None or quantity_sum is None:
        return {
            "status": _build_mismatch_status(mismatch_messages),
            "decision_reasons": mismatch_messages,
        }

    value_quantity_result = _compare_value_and_quantity(
        dashboard_lc_value=dashboard_lc_value,
        quantity_sum=quantity_sum,
        aggregate=aggregate,
    )
    if not mismatch_messages and value_quantity_result["status"] in _COMPLIANT_VALUES:
        return value_quantity_result
    if value_quantity_result["status"] not in _COMPLIANT_VALUES:
        mismatch_messages.extend(value_quantity_result["decision_reasons"])
    return {
        "status": _build_mismatch_status(mismatch_messages),
        "decision_reasons": mismatch_messages,
    }


def _compare_value_and_quantity(
    *,
    dashboard_lc_value: Decimal,
    quantity_sum: Decimal,
    aggregate: ERPFamilyAggregate,
) -> dict[str, object]:
    quantity_matches_lc_qty = _decimal_matches(quantity_sum, aggregate.lc_qty)
    quantity_matches_net_weight = (
        aggregate.net_weight is not None
        and _decimal_matches(
            quantity_sum,
            aggregate.net_weight,
            tolerance=_NET_WEIGHT_TOLERANCE,
        )
    )
    value_matches_exact = _decimal_matches(dashboard_lc_value, aggregate.current_lc_value)

    if value_matches_exact and quantity_matches_lc_qty:
        return {"status": "OK", "decision_reasons": ["Dashboard quantity matched ERP LC quantity."]}
    if value_matches_exact and quantity_matches_net_weight:
        return {"status": "OK (KGS)", "decision_reasons": ["Dashboard quantity matched ERP net weight."]}

    value_relation = _decimal_relation(dashboard_lc_value, aggregate.current_lc_value)
    quantity_relation = _decimal_relation(quantity_sum, aggregate.lc_qty)

    lower_reasons: list[str] = []
    if value_relation == "lower":
        lower_reasons.append(
            "LC Value mismatch: dashboard "
            f"'{_decimal_to_string(dashboard_lc_value)}' was lower than ERP "
            f"'{_decimal_to_string(aggregate.current_lc_value)}'."
        )
    if quantity_relation == "lower":
        lower_reasons.append(
            "Quantity mismatch: dashboard total "
            f"'{_decimal_to_string(quantity_sum)}' was lower than ERP LC Qty '{_decimal_to_string(aggregate.lc_qty)}'."
        )
    if lower_reasons:
        return {
            "status": "",
            "decision_reasons": lower_reasons,
        }
    if value_relation == "higher" and quantity_relation == "equal":
        return {
            "status": "",
            "decision_reasons": [
                "Excess mismatch: dashboard LC Value exceeded ERP while dashboard quantity matched ERP LC Qty; both fields must be higher together to use the excess rule."
            ],
        }
    if value_relation == "equal" and quantity_relation == "higher":
        return {
            "status": "",
            "decision_reasons": [
                "Excess mismatch: dashboard quantity exceeded ERP LC Qty while dashboard LC Value matched ERP; single-field excess is not allowed."
            ],
        }
    if value_relation == "higher" and quantity_relation == "higher":
        value_excess = (dashboard_lc_value - aggregate.current_lc_value).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        quantity_excess = (quantity_sum - aggregate.lc_qty).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        if value_excess < _MIN_LC_VALUE_EXCESS:
            return {
                "status": "",
                "decision_reasons": [
                    "Excess mismatch: dashboard LC Value excess "
                    f"'{_decimal_to_string(value_excess)}' was below the minimum allowed "
                    f"'{_decimal_to_string(_MIN_LC_VALUE_EXCESS)}'."
                ],
            }

        minimum_quantity_excess = (value_excess * _MIN_EXCESS_QUANTITY_RATIO).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        maximum_quantity_excess = (value_excess * _MAX_EXCESS_QUANTITY_RATIO).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        if minimum_quantity_excess <= quantity_excess <= maximum_quantity_excess:
            return {
                "status": "OK",
                "decision_reasons": ["Dashboard LC value and quantity satisfied the approved excess rule."],
            }
        return {
            "status": "",
            "decision_reasons": [
                "Excess mismatch: dashboard quantity excess "
                f"'{_decimal_to_string(quantity_excess)}' was outside the allowed range "
                f"'{_decimal_to_string(minimum_quantity_excess)}' to '{_decimal_to_string(maximum_quantity_excess)}' "
                f"for dashboard LC Value excess '{_decimal_to_string(value_excess)}'."
            ],
        }
    return {
        "status": "",
        "decision_reasons": [
            "Quantity mismatch: dashboard total "
            f"'{_decimal_to_string(quantity_sum)}' did not match ERP LC Qty '{_decimal_to_string(aggregate.lc_qty)}'"
            + (
                f" or ERP Net Weight '{_decimal_to_string(aggregate.net_weight)}'."
                if aggregate.net_weight is not None
                else "."
            )
        ],
    }


def _build_family_write_operations(
    *,
    run_report: RunReport,
    family: DashboardCandidateFamily,
    sheet_name: str,
    final_status: str,
    writes_dates: bool,
    ship_date: str,
    expiry_date: str,
) -> list[WriteOperation]:
    operations: list[WriteOperation] = []
    operation_index = 0
    shipment_value = _format_workbook_date(ship_date)
    expiry_value = _format_workbook_date(expiry_date)
    for row in family.rows:
        operations.append(
            WriteOperation(
                write_operation_id=build_write_operation_id(
                    run_report.run_id,
                    family.family_id,
                    operation_index,
                    sheet_name,
                    row.row_index,
                    "dashboard_status",
                ),
                run_id=run_report.run_id,
                mail_id=family.family_id,
                operation_index_within_mail=operation_index,
                sheet_name=sheet_name,
                row_index=row.row_index,
                column_key="dashboard_status",
                expected_pre_write_value=row.dashboard_status,
                expected_post_write_value=final_status,
                row_eligibility_checks=["target_cell_matches_expected_pre_write"],
            )
        )
        operation_index += 1
        if writes_dates:
            operations.append(
                WriteOperation(
                    write_operation_id=build_write_operation_id(
                        run_report.run_id,
                        family.family_id,
                        operation_index,
                        sheet_name,
                        row.row_index,
                        "shipment_date",
                    ),
                    run_id=run_report.run_id,
                    mail_id=family.family_id,
                    operation_index_within_mail=operation_index,
                    sheet_name=sheet_name,
                    row_index=row.row_index,
                    column_key="shipment_date",
                    expected_pre_write_value=row.shipment_date,
                    expected_post_write_value=shipment_value,
                    row_eligibility_checks=["target_cell_matches_expected_pre_write"],
                    number_format=row.shipment_date_number_format,
                )
            )
            operation_index += 1
            operations.append(
                WriteOperation(
                    write_operation_id=build_write_operation_id(
                        run_report.run_id,
                        family.family_id,
                        operation_index,
                        sheet_name,
                        row.row_index,
                        "expiry_date",
                    ),
                    run_id=run_report.run_id,
                    mail_id=family.family_id,
                    operation_index_within_mail=operation_index,
                    sheet_name=sheet_name,
                    row_index=row.row_index,
                    column_key="expiry_date",
                    expected_pre_write_value=row.expiry_date,
                    expected_post_write_value=expiry_value,
                    row_eligibility_checks=["target_cell_matches_expected_pre_write"],
                    number_format=row.expiry_date_number_format,
                )
            )
            operation_index += 1
    return operations


def _build_family_mail_outcome(
    *,
    run_report: RunReport,
    family: DashboardCandidateFamily,
    family_index: int,
    final_decision: FinalDecision,
    decision_reasons: list[str],
    staged_write_operations: list[WriteOperation],
) -> MailOutcomeRecord:
    return MailOutcomeRecord(
        run_id=run_report.run_id,
        mail_id=family.family_id,
        workflow_id=run_report.workflow_id,
        snapshot_index=family_index,
        processing_status=(
            MailProcessingStatus.BLOCKED
            if final_decision == FinalDecision.HARD_BLOCK
            else MailProcessingStatus.VALIDATED
        ),
        final_decision=final_decision,
        decision_reasons=list(decision_reasons),
        eligible_for_write=bool(staged_write_operations),
        eligible_for_print=False,
        eligible_for_mail_move=False,
        source_entry_id=family.family_id,
        subject_raw=family.lc_sc_no,
        sender_address="",
        rule_pack_id=run_report.rule_pack_id,
        rule_pack_version=run_report.rule_pack_version,
        applied_rule_ids=[],
        discrepancies=[],
        file_numbers_extracted=[],
        saved_documents=[],
        staged_write_operations=to_jsonable(staged_write_operations),
        write_disposition="new_writes_staged" if staged_write_operations else "no_write_noop",
    )


def _build_discrepancy(
    *,
    run_report: RunReport,
    code: str,
    message: str,
    details: dict[str, object],
    mail_id: str | None = None,
) -> DiscrepancyReport:
    return DiscrepancyReport(
        run_id=run_report.run_id,
        mail_id=mail_id,
        workflow_id=run_report.workflow_id,
        severity=FinalDecision.HARD_BLOCK,
        code=code,
        message=message,
        created_at_utc=utc_timestamp(),
        details=details,
    )


def _build_report_payload(
    *,
    run_report: RunReport,
    families: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run_report.run_id,
        "workflow_id": run_report.workflow_id.value,
        "rule_pack_id": run_report.rule_pack_id,
        "rule_pack_version": run_report.rule_pack_version,
        "state_timezone": run_report.state_timezone,
        "generated_at_utc": utc_timestamp(),
        "summary": dict(run_report.summary),
        "family_count": len(families),
        "families": families,
    }


def _build_report_family(
    *,
    family: DashboardCandidateFamily,
    final_decision: FinalDecision,
    final_workbook_value: str | None,
    decision_reasons: list[str],
    search_attempts: list[dict[str, object]],
    erp_aggregate: ERPFamilyAggregate | None,
    dashboard_snapshot: DashboardFamilySnapshot | None,
    written_shipment_date: str | None,
    written_expiry_date: str | None,
) -> dict[str, object]:
    return {
        "family_id": family.family_id,
        "lc_sc_no": family.lc_sc_no,
        "row_indexes": list(family.row_indexes),
        "sl_no_values": list(family.sl_no_values),
        "workbook_master_lc_values": list(family.master_lc_values),
        "final_decision": final_decision.value,
        "final_workbook_value": final_workbook_value,
        "decision_reasons": list(decision_reasons),
        "search_attempts": list(search_attempts),
        "comparison_evidence": _build_comparison_evidence(
            family=family, aggregate=erp_aggregate, snapshot=dashboard_snapshot,
        ),
        "erp": (
            {
                "buyer_name": erp_aggregate.buyer_name,
                "lc_date": erp_aggregate.lc_date,
                "ship_date": erp_aggregate.ship_date,
                "expiry_date": erp_aggregate.expiry_date,
                "current_lc_value": _decimal_to_string(erp_aggregate.current_lc_value),
                "lc_qty": _decimal_to_string(erp_aggregate.lc_qty),
                "net_weight": _decimal_to_string(erp_aggregate.net_weight),
                "ship_remarks": erp_aggregate.ship_remarks,
                "source_row_count": erp_aggregate.source_row_count,
            }
            if erp_aggregate is not None
            else None
        ),
        "dashboard": (
            {
                "beneficiary_name": dashboard_snapshot.beneficiary_name,
                "irc_details": dashboard_snapshot.irc_details,
                "erc_details": dashboard_snapshot.erc_details,
                "lc_date": dashboard_snapshot.lc_date,
                "last_date_of_shipment": dashboard_snapshot.last_date_of_shipment,
                "lc_expiry_date": dashboard_snapshot.lc_expiry_date,
                "lc_value": dashboard_snapshot.lc_value,
                "foreign_lc_numbers": list(dashboard_snapshot.foreign_lc_numbers),
                "commodity_quantities": list(dashboard_snapshot.commodity_quantities),
                "source_url": dashboard_snapshot.source_url,
            }
            if dashboard_snapshot is not None
            else None
        ),
        "written_shipment_date": written_shipment_date,
        "written_expiry_date": written_expiry_date,
    }


def _build_comparison_evidence(
    *, family: DashboardCandidateFamily, aggregate: ERPFamilyAggregate | None,
    snapshot: DashboardFamilySnapshot | None,
) -> dict[str, object]:
    """Report-only observations; never used to select decisions or workbook writes."""
    if aggregate is None or snapshot is None:
        return {"availability": "unavailable", "fields": [], "numeric_rule_status": None,
                "rule_results": [{"rule_id": "comparison_inputs", "status": "unavailable",
                                  "prerequisites": "ERP aggregate and dashboard snapshot",
                                  "reason": "ERP aggregate or dashboard snapshot is missing; see family decision and search attempts."}]}

    fields: list[dict[str, object]] = []

    def add(field, source, expected, observed, normalized_expected, normalized_observed, rule, difference=None):
        fields.append({
            "field": field, "reference_source": source,
            "reference_value": expected, "dashboard_value": observed,
            "normalized_reference": normalized_expected, "normalized_dashboard": normalized_observed,
            "dashboard_minus_reference": difference, "rule": rule,
        })

    dashboard_value = _parse_decimal(snapshot.lc_value)
    quantity = _sum_decimal_strings(snapshot.commodity_quantities)
    for label, expected, observed, raw, tolerance in (
        ("LC Value", aggregate.current_lc_value, dashboard_value, snapshot.lc_value, _DEFAULT_DECIMAL_TOLERANCE),
        ("LC Qty", aggregate.lc_qty, quantity, snapshot.commodity_quantities, _DEFAULT_DECIMAL_TOLERANCE),
        ("Net Weight", aggregate.net_weight, quantity, snapshot.commodity_quantities, _NET_WEIGHT_TOLERANCE),
    ):
        finite = expected is not None and expected.is_finite() and observed is not None and observed.is_finite()
        add(label, "ERP", _decimal_to_string(expected), raw,
            _decimal_to_string(expected), _decimal_to_string(observed),
            f"Round each input to 2 decimals; absolute tolerance {tolerance}. See combined value/quantity rule.",
            _decimal_to_string(observed - expected) if finite else None)

    numeric_status = None
    numeric_reasons = ["Numeric comparison unavailable: missing, invalid, or nonfinite input."]
    if all(value is not None and value.is_finite() for value in (
        dashboard_value, quantity, aggregate.current_lc_value, aggregate.lc_qty,
    )) and (aggregate.net_weight is None or aggregate.net_weight.is_finite()):
        result = _compare_value_and_quantity(
            dashboard_lc_value=dashboard_value, quantity_sum=quantity, aggregate=aggregate,
        )
        numeric_status = result["status"] or "mismatch"
        numeric_reasons = result["decision_reasons"]

    for label, expected, observed, rule in (
        ("LC Date", aggregate.lc_date, snapshot.lc_date, "Same calendar date."),
        ("Shipment Date", aggregate.ship_date, snapshot.last_date_of_shipment, "Dashboard offset: 0 through 250 days inclusive."),
        ("Expiry Date", aggregate.expiry_date, snapshot.lc_expiry_date, "Dashboard expiry on/after ERP expiry; each expiry 5 through 90 days after its shipment."),
    ):
        left, right = _normalize_date(expected), _normalize_date(observed)
        add(label, "ERP", expected, observed, left, right, rule,
            _days_between_dates(start_date=left, end_date=right))

    add("Beneficiary", "Configured beneficiary", _PIONEER_BENEFICIARY, snapshot.beneficiary_name,
        _normalize_special_text(_PIONEER_BENEFICIARY), _normalize_special_text(snapshot.beneficiary_name),
        "Normalized equality.")
    for label, observed in (("IRC Details", snapshot.irc_details), ("ERC Details", snapshot.erc_details)):
        add(label, "ERP buyer", aggregate.buyer_name, observed,
            _normalize_buyer_comparison_text(aggregate.buyer_name), _normalize_buyer_comparison_text(observed),
            "Every populated section must contain the normalized buyer; at least one section required.")
    add("Foreign LC", "Workbook Master L/C No.", family.master_lc_values, snapshot.foreign_lc_numbers,
        [_normalize_foreign_lc_reference(value) for value in family.master_lc_values],
        [_normalize_foreign_lc_reference(value) for value in snapshot.foreign_lc_numbers],
        "At least one nonempty normalized reference must overlap.")
    evidence = {
        "availability": "available", "fields": fields,
        "numeric_rule_status": numeric_status, "numeric_rule_reasons": numeric_reasons,
        "rule_results": _build_rule_results(family=family, aggregate=aggregate, snapshot=snapshot,
                                            numeric_status=numeric_status),
        "numeric_rule": "OK: value/quantity match, or value excess >= 100 and quantity excess 20%-80% of value excess. OK (KGS): value matches and quantity matches net weight within 0.8. Other checks must also pass.",
        "input_observations": ["Blank commodity quantity entries are omitted by the existing summation rule."]
        if any(not str(value).strip() for value in snapshot.commodity_quantities) else [],
    }
    for label, start, end in (
        ("Dashboard shipment to expiry", snapshot.last_date_of_shipment, snapshot.lc_expiry_date),
        ("ERP shipment to expiry", aggregate.ship_date, aggregate.expiry_date),
    ):
        left, right = _normalize_date(start), _normalize_date(end)
        add(label, "Dashboard dates" if label.startswith("Dashboard") else "ERP dates",
            start, end, left, right,
            f"Expiry must be {_EXPIRY_MIN_OFFSET_DAYS}-{_EXPIRY_MAX_OFFSET_DAYS} days after shipment, inclusive.",
            _days_between_dates(start_date=left, end_date=right))
    return _describe_comparison_evidence(evidence)


def _describe_comparison_evidence(evidence: dict[str, object]) -> dict[str, object]:
    """Add presentation metadata from diagnostic results, preserving legacy fields."""
    rules = {rule["rule_id"]: rule for rule in evidence["rule_results"]}
    def passed(name: str) -> bool:
        return rules.get(name, {}).get("status") == "pass"
    path = ("Value and LC quantity within tolerance" if passed("exact_value_quantity") else
            "KGS alternative" if passed("kgs_alternative") else
            "Approved excess" if passed("excess_minimum") and passed("excess_quantity_range") else
            "No numeric acceptance path" if evidence.get("numeric_rule_status") == "mismatch" else
            "Unavailable")
    for rule_id, rule in rules.items():
        status = rule["status"]
        evaluation = {"pass": "Satisfied", "fail": "Evaluated; not satisfied", "unavailable": "Not evaluable"}.get(status)
        if evaluation is None:
            if rule_id == "exact_value_quantity":
                evaluation = "Evaluated; did not qualify"
            elif rule_id == "kgs_alternative" and not passed("exact_value_quantity"):
                evaluation = "Not eligible: LC value does not match"
            elif rule_id == "lc_quantity_match":
                evaluation = "Difference accepted by alternative path"
            else:
                evaluation = "Not required"
        rule["evaluation"] = evaluation

    evidence["decision_summary"] = {
        "numeric_path": path,
        "failed_rule_ids": [name for name, rule in rules.items() if rule["status"] == "fail"],
        "unavailable_rule_ids": [name for name, rule in rules.items() if rule["status"] == "unavailable"],
    }
    evidence["rule_reference"] = [
        f"Evaluate numeric paths in order: value/LC quantity equality within {_DEFAULT_DECIMAL_TOLERANCE}; then KGS with value equality and net-weight tolerance {_NET_WEIGHT_TOLERANCE}; then approved excess. Inputs are rounded to 2 decimals before summing.",
        f"Excess requires both value and LC quantity to be higher, value excess at least {_MIN_LC_VALUE_EXCESS}, and quantity excess between {_MIN_EXCESS_QUANTITY_RATIO * 100}% and {_MAX_EXCESS_QUANTITY_RATIO * 100}% of value excess, inclusive. Ratio bounds are rounded to 2 decimals.",
        f"LC dates must match. Dashboard shipment must be 0-{_MAX_SHIPMENT_DATE_OFFSET_DAYS} days after ERP shipment. Dashboard expiry must be on/after ERP expiry; each shipment-to-expiry interval must be {_EXPIRY_MIN_OFFSET_DAYS}-{_EXPIRY_MAX_OFFSET_DAYS} days inclusive.",
        f"Beneficiary must normalize to {_PIONEER_BENEFICIARY}. Every populated IRC/ERC section must contain the normalized ERP buyer; at least one section must be populated.",
        "At least one normalized dashboard foreign LC reference must overlap the workbook master LC references. All nonnumeric checks must also pass for family acceptance.",
    ]
    links = {
        "LC Value": ["value_floor", "exact_value_quantity", "kgs_alternative", "excess_alternative", "excess_minimum", "excess_quantity_range"],
        "LC Qty": ["lc_quantity_match", "exact_value_quantity", "excess_alternative", "excess_minimum", "excess_quantity_range", "quantity_completeness"],
        "Net Weight": ["kgs_alternative"], "LC Date": ["lc_date"],
        "Shipment Date": ["shipment_offset"], "Expiry Date": ["expiry_order"],
        "Dashboard shipment to expiry": ["dashboard_expiry_window"], "ERP shipment to expiry": ["erp_expiry_window"],
        "Beneficiary": ["beneficiary"], "IRC Details": ["buyer_section_present", "irc_buyer"],
        "ERC Details": ["buyer_section_present", "erc_buyer"], "Foreign LC": ["foreign_lc_overlap"],
    }
    numeric = {"LC Value", "LC Qty", "Net Weight"}
    dates = {"LC Date", "Shipment Date", "Expiry Date", "Dashboard shipment to expiry", "ERP shipment to expiry"}
    for field in evidence["fields"]:
        name = field["field"]
        field["rule_ids"] = [rule_id for rule_id in links.get(name, []) if rule_id in rules]
        field["display_source"] = "ERP aggregate" if name in numeric else field["reference_source"]
        field["difference_basis"] = ("Compared minus reference, days" if name in dates else
                                     "Dashboard total minus ERP net weight, KGS comparison basis" if name == "Net Weight" else
                                     "Dashboard total minus ERP LC quantity; unit not captured" if name == "LC Qty" else
                                     "Dashboard minus ERP value; currency not captured" if name == "LC Value" else "Not applicable")
        field["difference_display"] = ("Not applicable" if name not in numeric | dates else
                                       "Unavailable" if field["dashboard_minus_reference"] is None else
                                       str(field["dashboard_minus_reference"]))
    return evidence


def _build_rule_results(
    *, family: DashboardCandidateFamily, aggregate: ERPFamilyAggregate,
    snapshot: DashboardFamilySnapshot, numeric_status: str | None,
) -> list[dict[str, str]]:
    """Explain independent checks and alternative paths without changing decisions."""
    results: list[dict[str, str]] = []

    def add(rule_id: str, status: str, reason: str, prerequisites: str = "None") -> None:
        results.append(dict(rule_id=rule_id, status=status, reason=reason, prerequisites=prerequisites))

    beneficiary = _normalize_special_text(snapshot.beneficiary_name)
    add("beneficiary", "pass" if beneficiary == _normalize_special_text(_PIONEER_BENEFICIARY) else "fail",
        "Beneficiary is missing/empty after normalization." if not beneficiary else
        f"Normalized beneficiary '{beneficiary}'; expected '{_PIONEER_BENEFICIARY}'.")
    buyer = _normalize_buyer_comparison_text(aggregate.buyer_name)
    sections = {"irc": _normalize_buyer_comparison_text(snapshot.irc_details),
                "erc": _normalize_buyer_comparison_text(snapshot.erc_details)}
    add("buyer_section_present", "pass" if any(sections.values()) else "fail",
        "At least one populated buyer section is required; " +
        ("a section is populated." if any(sections.values()) else "both IRC and ERC are empty after normalization."))
    for name, value in sections.items():
        if not value:
            add(name + "_buyer", "not_applicable", "Section is empty; the populated-section rule determines whether another section is required.")
        elif not buyer:
            add(name + "_buyer", "unavailable", "ERP buyer is empty after normalization; existing containment behavior is unchanged.", "Nonempty normalized ERP buyer")
        else:
            add(name + "_buyer", "pass" if buyer in value else "fail",
                f"Normalized section '{value}' {'contains' if buyer in value else 'does not contain'} ERP buyer '{buyer}'.")

    def date_check(rule_id: str, start: str, end: str, minimum: int, maximum: int | None, labels: str) -> None:
        left, right = _normalize_date(start), _normalize_date(end)
        invalid = [f"{label} is {'missing' if not str(raw).strip() else 'malformed'} ({raw!r})"
                   for label, raw, parsed in (("start date", start, left), ("end date", end, right)) if parsed is None]
        if invalid:
            reason = labels + ": " + "; ".join(invalid) + "."
            if rule_id == "lc_date" and left is None and right is None:
                reason += " Existing LC-date equality treats two unparseable dates as equal; family decision is unchanged."
            add(rule_id, "unavailable", reason, "Two parseable dates")
            return
        days = _days_between_dates(start_date=left, end_date=right)
        passed = days >= minimum and (maximum is None or days <= maximum)
        bound = f"{minimum} through {maximum} inclusive" if maximum is not None else f"at least {minimum}"
        add(rule_id, "pass" if passed else "fail", f"{labels}: {days} days; required {bound} days.", "Two parseable dates")

    date_check("lc_date", aggregate.lc_date, snapshot.lc_date, 0, 0, "ERP LC date to dashboard LC date")
    date_check("shipment_offset", aggregate.ship_date, snapshot.last_date_of_shipment, 0, _MAX_SHIPMENT_DATE_OFFSET_DAYS, "ERP shipment to dashboard shipment")
    date_check("expiry_order", aggregate.expiry_date, snapshot.lc_expiry_date, 0, None, "ERP expiry to dashboard expiry")
    date_check("dashboard_expiry_window", snapshot.last_date_of_shipment, snapshot.lc_expiry_date, _EXPIRY_MIN_OFFSET_DAYS, _EXPIRY_MAX_OFFSET_DAYS, "Dashboard shipment to dashboard expiry")
    date_check("erp_expiry_window", aggregate.ship_date, aggregate.expiry_date, _EXPIRY_MIN_OFFSET_DAYS, _EXPIRY_MAX_OFFSET_DAYS, "ERP shipment to ERP expiry")

    references = {value for raw in family.master_lc_values if (value := _normalize_foreign_lc_reference(raw))}
    foreign = {value for raw in snapshot.foreign_lc_numbers if (value := _normalize_foreign_lc_reference(raw))}
    missing = []
    if not references:
        missing.append("Workbook Master L/C No. has no usable reference")
    if not foreign:
        missing.append("Dashboard Foreign LC No. has no usable reference")
    overlap = sorted(references & foreign)
    add("foreign_lc_overlap", "pass" if overlap else "fail",
        "; ".join(missing) if missing else (f"Common normalized references: {', '.join(overlap)}." if overlap else "Both reference lists are populated, but no normalized reference overlaps."))

    def number(raw: object, label: str) -> Decimal | None:
        parsed = _parse_decimal(raw)
        if parsed is None or not parsed.is_finite():
            add(label, "unavailable", f"{label}: {'missing' if not str(raw).strip() else 'invalid or nonfinite'} value {raw!r}.")
            return None
        return parsed

    value = number(snapshot.lc_value, "dashboard_value_input")
    erp_value = number(aggregate.current_lc_value, "erp_value_input")
    erp_quantity = number(aggregate.lc_qty, "erp_quantity_input")
    parsed_quantities = [number(raw, f"dashboard_quantity_row_{index}")
                         for index, raw in enumerate(snapshot.commodity_quantities, 1) if str(raw).strip()]
    if not parsed_quantities:
        add("dashboard_quantity_input", "unavailable", "Dashboard quantity list is empty or contains only blank entries.")
    quantity = sum(parsed_quantities, Decimal("0")) if parsed_quantities and all(item is not None for item in parsed_quantities) else None
    blank_count = sum(not str(raw).strip() for raw in snapshot.commodity_quantities)
    if blank_count:
        add("quantity_completeness", "unavailable", f"{blank_count} blank quantity entries omitted by existing summation; the total may be partial. Family decision is unchanged.")
    vr = _decimal_relation(value, erp_value) if value is not None and erp_value is not None else None
    qr = _decimal_relation(quantity, erp_quantity) if quantity is not None and erp_quantity is not None else None
    add("value_floor", "unavailable" if vr is None else ("fail" if vr == "lower" else "pass"),
        "Not evaluated: invalid/missing value input." if vr is None else f"Dashboard LC value is {vr} relative to ERP (tolerance 0.01); lower is rejected.", "Finite ERP and dashboard values")
    alternate_quantity = qr is not None and qr != "equal" and numeric_status in _COMPLIANT_VALUES
    add("lc_quantity_match", "unavailable" if qr is None else ("pass" if qr == "equal" else ("not_applicable" if alternate_quantity else "fail")),
        "Not evaluated: invalid/missing quantity input." if qr is None else
        f"Dashboard total is {qr} relative to ERP LC quantity (tolerance 0.01). " +
        (f"Exact quantity equality is not required: the alternative numeric path returned {numeric_status}." if alternate_quantity else
         "This is the exact-path check; KGS or excess acceptance can override its failure."), "Finite ERP quantity and dashboard total")
    exact = vr == "equal" and qr == "equal"
    add("exact_value_quantity", "unavailable" if vr is None or qr is None else ("pass" if exact else "not_applicable"),
        "Not evaluated: value or quantity input unavailable." if vr is None or qr is None else
        ("Value and LC quantity match within 0.01." if exact else f"Exact path not satisfied: value is {vr}, quantity is {qr}; alternative paths are evaluated separately."), "Finite values and quantities")

    weight = aggregate.net_weight
    finite_weight = weight is not None and weight.is_finite()
    kgs = vr == "equal" and qr is not None and not exact and quantity is not None and finite_weight and _decimal_matches(quantity, weight, tolerance=_NET_WEIGHT_TOLERANCE)
    if exact or (vr is not None and vr != "equal"):
        add("kgs_alternative", "not_applicable", "Exact path already passes." if exact else f"KGS requires matching LC value; dashboard value is {vr}.")
    elif vr is None or qr is None or not finite_weight:
        add("kgs_alternative", "unavailable", "Not evaluated: matching value, usable quantity and finite ERP net weight are required; " + ("ERP net weight is missing or nonfinite." if not finite_weight else "value/quantity input is unavailable."))
    else:
        add("kgs_alternative", "pass" if kgs else "fail", f"Dashboard quantity minus ERP net weight = {_decimal_to_string(quantity - weight)}; absolute tolerance 0.8.")

    if exact or kgs:
        add("excess_alternative", "not_applicable", "Exact or KGS acceptance path already passes.")
    elif vr is None or qr is None:
        add("excess_alternative", "unavailable", "Not evaluated: value or quantity input unavailable.")
    elif vr != "higher" or qr != "higher":
        add("excess_alternative", "fail", f"Both must be higher: value is {vr}, quantity is {qr}; excess cannot compensate for a lower or equal field.")
    else:
        excess = value - erp_value
        delta = quantity - erp_quantity
        low = (excess * _MIN_EXCESS_QUANTITY_RATIO).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        high = (excess * _MAX_EXCESS_QUANTITY_RATIO).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        add("excess_minimum", "pass" if excess >= _MIN_LC_VALUE_EXCESS else "fail", f"Value excess {excess}; minimum {_MIN_LC_VALUE_EXCESS}.")
        add("excess_quantity_range", "pass" if low <= delta <= high else "fail", f"Quantity excess {delta}; allowed {low} through {high} inclusive (20%-80% of value excess). Both excess checks must pass.")
    return results


def _render_comparison_evidence(families) -> str:
    sections: list[str] = []

    def display(value: object) -> str:
        if isinstance(value, list):
            return "<br>".join(escape(str(item)) if str(item).strip() else "(blank)" for item in value) or "(empty list)"
        return escape(str(value)) if value is not None and str(value).strip() else "Unavailable"

    for index, family in enumerate(families if isinstance(families, list) else []):
        if not isinstance(family, dict) or not isinstance(family.get("comparison_evidence"), dict):
            continue
        evidence = family["comparison_evidence"]
        rules = {rule["rule_id"]: rule for rule in evidence.get("rule_results", [])}

        def rule_link(rule_id: str) -> str:
            rule = rules[rule_id]
            return f'<a href="#evidence-{index}-{escape(rule_id, quote=True)}">{escape(rule_id)}: {escape(rule.get("evaluation", rule["status"]))}</a>'

        rule_rows = "".join(f'<tr id="evidence-{index}-{escape(rule["rule_id"], quote=True)}">' + "".join(f"<td>{escape(str(rule.get(key, '')))}</td>"
                            for key in ("rule_id", "status", "evaluation", "prerequisites", "reason")) + "</tr>"
                            for rule in rules.values())
        rows = []
        for field in evidence.get("fields", []):
            name = field.get("field", "")
            compared = display(field.get("dashboard_value"))
            if name in {"LC Qty", "Net Weight"}:
                compared = "Commodity rows:<br>" + compared + "<br>Total: " + display(field.get("normalized_dashboard"))
            cells = [display(name), display(field.get("display_source", field.get("reference_source"))),
                     display(field.get("reference_value")), compared, display(field.get("normalized_reference")),
                     display(field.get("normalized_dashboard")), display(field.get("difference_display", field.get("dashboard_minus_reference"))),
                     display(field.get("difference_basis", "Not specified")),
                     "<br>".join(rule_link(key) for key in field.get("rule_ids", []) if key in rules) or display(field.get("rule"))]
            rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
        summary = evidence.get("decision_summary", {})
        findings = [rule for rule in rules.values() if rule["status"] in {"fail", "unavailable"}]
        findings_html = "".join(f"<li>{rule_link(rule['rule_id'])}: {escape(rule['reason'])}</li>" for rule in findings)
        reference = evidence.get("rule_reference", [])
        sections.append(
            f"<details><summary>{escape(str(family.get('lc_sc_no', '')))} — SL.No. {escape(_format_report_sl_no_values(family.get('sl_no_values', [])))}</summary>"
            "<h3>Decision summary</h3>"
            f"<p>Overall decision: {display(family.get('final_decision'))}. Workbook status: {display(family.get('final_workbook_value'))}.</p>"
            f"<p>Numeric acceptance path: {display(summary.get('numeric_path', 'Unavailable'))}. All other required checks still apply.</p>"
            f"<p>Recorded family reasons: {display(family.get('decision_reasons', []))}</p>"
            "<p>Diagnostic findings (do not replace the recorded decision):</p>"
            + (f"<ul>{findings_html}</ul>" if findings_html else
               "<p>No failed or unavailable diagnostic checks.</p>" if rules else "<p>No rule diagnostics recorded.</p>") +
            '<details><summary>Rule reference</summary><ul>' + "".join(f"<li>{escape(str(item))}</li>" for item in reference) + "</ul></details>"
            "<h3>Comparison evidence</h3>"
            '<div class="table-wrap"><table><thead><tr><th>Field</th><th>Reference source</th><th>Reference value / interval start</th><th>Compared value / interval end</th><th>Normalized reference</th><th>Normalized compared value</th><th>Difference</th><th>Measurement basis</th><th>Interpretation and rule links</th></tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table></div>"
            "<p>Rule explanations are diagnostic. Alternative paths are not individually required; the family decision above remains authoritative.</p>"
            '<div class="table-wrap"><table><thead><tr><th>Rule</th><th>Result</th><th>Evaluation</th><th>Prerequisites</th><th>Explanation</th></tr></thead>'
            f"<tbody>{rule_rows}</tbody></table></div></details>"
        )
    return '<section class="section"><h2>Comparison Evidence</h2>' + "".join(sections) + "</section>" if sections else ""


def _build_report_html(*, report_payload: dict[str, object]) -> str:
    families = report_payload.get("families", [])
    summary = report_payload.get("summary") if isinstance(report_payload.get("summary"), dict) else {}
    family_count = report_payload.get("family_count", 0)
    state_timezone = str(report_payload.get("state_timezone", "Asia/Dhaka") or "Asia/Dhaka")
    generated_at_display = _local_display_timestamp(
        generated_at_utc=report_payload.get("generated_at_utc"),
        state_timezone=state_timezone,
    )
    rows_html = ""
    if isinstance(families, list) and families:
        rendered_rows: list[str] = []
        for family in families:
            if not isinstance(family, dict):
                continue
            dashboard = family.get("dashboard") if isinstance(family.get("dashboard"), dict) else {}
            erp = family.get("erp") if isinstance(family.get("erp"), dict) else {}
            rendered_rows.append(
                "<tr>"
                f"<td>{escape(str(family.get('lc_sc_no', '')))}</td>"
                f"<td>{escape(_format_report_sl_no_values(family.get('sl_no_values', [])))}</td>"
                f"<td>{'<br>'.join(escape(str(item)) for item in family.get('workbook_master_lc_values', []))}</td>"
                f"<td>{escape(str(family.get('final_decision', '')))}</td>"
                f"<td>{escape(str(erp.get('buyer_name', '')))}</td>"
                f"<td>{escape(str(erp.get('current_lc_value', '')))}</td>"
                f"<td>{escape(str(erp.get('lc_qty', '')))}</td>"
                f"<td>{escape(str(erp.get('net_weight', '')))}</td>"
                f"<td>{escape(str(family.get('final_workbook_value', '') or ''))}</td>"
                f"<td>{'<br>'.join(escape(str(item)) for item in family.get('decision_reasons', []))}</td>"
                f"<td>{escape(str(family.get('written_shipment_date', '') or ''))}</td>"
                f"<td>{escape(str(family.get('written_expiry_date', '') or ''))}</td>"
                f"<td>{escape(str(dashboard.get('beneficiary_name', '')))}</td>"
                f"<td>{escape(str(dashboard.get('irc_details', '')))}</td>"
                f"<td>{escape(str(dashboard.get('erc_details', '')))}</td>"
                f"<td>{escape(str(dashboard.get('lc_date', '')))}</td>"
                f"<td>{escape(str(dashboard.get('last_date_of_shipment', '')))}</td>"
                f"<td>{escape(str(dashboard.get('lc_expiry_date', '')))}</td>"
                f"<td>{escape(str(dashboard.get('lc_value', '')))}</td>"
                f"<td>{_format_report_multiline_values(dashboard.get('foreign_lc_numbers', []))}</td>"
                f"<td>{escape(_format_report_quantity_total(dashboard.get('commodity_quantities', [])))}</td>"
                "</tr>"
            )
        rows_html = "\n".join(rendered_rows)
    else:
        rows_html = '<tr><td colspan="21">No eligible workbook families were found.</td></tr>'

    snapshot_rows = [
        ("Run ID", report_payload.get("run_id", "")),
        ("Workflow ID", report_payload.get("workflow_id", "")),
        ("Rule Pack", f"{report_payload.get('rule_pack_id', '')} ({report_payload.get('rule_pack_version', '')})"),
        ("Generated At", f"{generated_at_display} ({state_timezone})"),
        ("Family Count", family_count),
    ]
    summary_rows = [
        ("Pass", summary.get("pass", 0)),
        ("Warning", summary.get("warning", 0)),
        ("Hard Block", summary.get("hard_block", 0)),
    ]

    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\">\n"
        "  <title>Workflow Dashboard: bb_dashboard_verification</title>\n"
        "  <style>\n"
        "    :root { color-scheme: light; }\n"
        "    html, body { height: 100%; }\n"
        "    body { font-family: 'Segoe UI', Tahoma, sans-serif; margin: 0; color: #1f2933; background: #f6f8fb; }\n"
        "    main { width: 100%; max-width: none; margin: 0; padding: 24px; box-sizing: border-box; }\n"
        "    h1, h2 { color: #102a43; }\n"
        "    h1 { margin-bottom: 4px; }\n"
        "    .meta { color: #52606d; margin-bottom: 24px; }\n"
        "    .section { background: #ffffff; border: 1px solid #d9e2ec; border-radius: 10px; padding: 18px 20px; margin-bottom: 18px; }\n"
        "    .family-results-section { padding-bottom: 12px; }\n"
        "    .sticky-section-title { position: sticky; top: 0; z-index: 5; background: #ffffff; padding-bottom: 12px; margin-bottom: 0; }\n"
        "    .table-wrap { width: 100%; max-width: 100%; overflow-x: scroll; overflow-y: auto; max-height: calc(100vh - 240px); padding-bottom: 8px; scrollbar-gutter: stable both-edges; }\n"
        "    table { width: 100%; border-collapse: collapse; margin-top: 10px; }\n"
        "    .wide-table { width: max-content; min-width: 100%; table-layout: auto; margin-top: 0; }\n"
        "    th, td { border-bottom: 1px solid #e5e7eb; border-right: 1px solid #d9e2ec; padding: 10px 12px; text-align: left; vertical-align: top; white-space: normal; overflow-wrap: anywhere; word-break: break-word; background: #ffffff; }\n"
        "    th:last-child, td:last-child { border-right: none; }\n"
        "    th { background: #f0f4f8; font-weight: 600; }\n"
        "    .wide-table thead th { position: sticky; top: 0; z-index: 4; box-shadow: inset 0 -1px 0 #d9e2ec; }\n"
        "    td { line-height: 1.4; }\n"
        "    code { font-family: Consolas, 'Courier New', monospace; background: #f0f4f8; padding: 1px 4px; border-radius: 4px; }\n"
        "    .empty { color: #7b8794; font-style: italic; }\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        "  <main>\n"
        "    <h1>Workflow Dashboard: bb_dashboard_verification</h1>\n"
        f"    <p class=\"meta\">Generated at: {escape(generated_at_display)} ({escape(state_timezone)})</p>\n"
        f"{_render_report_key_value_section('Snapshot', snapshot_rows)}\n"
        f"{_render_report_key_value_section('Summary', summary_rows)}\n"
        "    <section class=\"section family-results-section\">\n"
        "      <h2 class=\"sticky-section-title\">Family Results</h2>\n"
        "      <div class=\"table-wrap\">\n"
        "      <table class=\"wide-table\">\n"
        "        <colgroup>\n"
        "          <col style=\"width: 240px\">\n"
        "          <col style=\"width: 90px\">\n"
        "          <col style=\"width: 240px\">\n"
        "          <col style=\"width: 120px\">\n"
        "          <col style=\"width: 240px\">\n"
        "          <col style=\"width: 130px\">\n"
        "          <col style=\"width: 130px\">\n"
        "          <col style=\"width: 130px\">\n"
        "          <col style=\"width: 360px\">\n"
        "          <col style=\"width: 360px\">\n"
        "          <col style=\"width: 170px\">\n"
        "          <col style=\"width: 170px\">\n"
        "          <col style=\"width: 260px\">\n"
        "          <col style=\"width: 340px\">\n"
        "          <col style=\"width: 340px\">\n"
        "          <col style=\"width: 150px\">\n"
        "          <col style=\"width: 170px\">\n"
        "          <col style=\"width: 150px\">\n"
        "          <col style=\"width: 130px\">\n"
        "          <col style=\"width: 280px\">\n"
        "          <col style=\"width: 180px\">\n"
        "        </colgroup>\n"
        "        <thead><tr><th>LC/SC</th><th>SL.No.</th><th>Workbook Master L/C</th><th>Decision</th><th>ERP Buyer</th><th>ERP LC Value</th><th>ERP LC Qty</th><th>ERP Net Weight</th><th>Final Workbook Value</th><th>Decision Reasons</th><th>Shipment Date Writeback</th><th>Expiry Date Writeback</th><th>Dashboard Beneficiary</th><th>Dashboard IRC</th><th>Dashboard ERC</th><th>Dashboard LC Date</th><th>Dashboard Last Shipment</th><th>Dashboard Expiry</th><th>Dashboard LC Value</th><th>Dashboard Foreign LC No</th><th>Dashboard Quantity Total</th></tr></thead>\n"
        "        <tbody>\n"
        f"{rows_html}\n"
        "        </tbody>\n"
        "      </table>\n"
        "      </div>\n"
        "    </section>\n"
        f"{_render_comparison_evidence(families)}\n"
        "  </main>\n"
        "</body>\n"
        "</html>\n"
    )


def _local_display_timestamp(*, generated_at_utc: object, state_timezone: str) -> str:
    value = str(generated_at_utc or "").strip()
    if not value:
        return ""
    normalized = value.replace("Z", "+00:00")
    try:
        generated_at = datetime.fromisoformat(normalized)
    except ValueError:
        return value
    timezone = validate_timezone(state_timezone)
    return generated_at.astimezone(timezone).strftime("%d/%m/%Y %I:%M:%S %p")


def _render_report_key_value_section(title: str, rows: list[tuple[str, object]]) -> str:
    items = "\n".join(
        f"        <tr><th>{escape(str(label))}</th><td>{escape(str(value))}</td></tr>"
        for label, value in rows
    )
    return "\n".join(
        [
            '    <section class="section">',
            f"      <h2>{escape(title)}</h2>",
            "      <table>",
            "        <tbody>",
            items,
            "        </tbody>",
            "      </table>",
            "    </section>",
        ]
    )


def _format_report_multiline_values(values: object) -> str:
    if not isinstance(values, list):
        return ""
    return "<br>".join(escape(str(item)) for item in values if str(item).strip())


def _format_report_quantity_total(values: object) -> str:
    if not isinstance(values, list):
        return ""
    total = _sum_decimal_strings(values)
    if total is not None:
        return _decimal_to_string(total) or ""
    return ", ".join(str(item).strip() for item in values if str(item).strip())


def _format_report_sl_no_values(values: object) -> str:
    if not isinstance(values, list):
        return ""
    return ", ".join(_format_report_sl_no_value(item) for item in values if str(item).strip())


def _format_report_sl_no_value(value: object) -> str:
    candidate = str(value).strip()
    if not candidate:
        return ""
    try:
        decimal_value = Decimal(candidate.replace(",", ""))
    except (InvalidOperation, ValueError):
        return candidate
    if decimal_value == decimal_value.to_integral_value():
        return str(int(decimal_value))
    return candidate


def _build_search_keys(*, ship_remarks: str | None, workbook_lc_sc_no: str) -> list[str]:
    primary = normalize_dashboard_search_key(ship_remarks or workbook_lc_sc_no)
    if not primary:
        return []
    zero_inserted = _insert_zero_before_last_four(primary)
    if ship_remarks:
        return [primary, zero_inserted] if zero_inserted != primary else [primary]
    return [primary, zero_inserted] if zero_inserted != primary else [primary]


def _insert_zero_before_last_four(value: str) -> str:
    normalized = normalize_dashboard_search_key(value)
    if len(normalized) < 4:
        return normalized
    return f"{normalized[:-4]}0{normalized[-4:]}"


def _split_multiline_values(value: str) -> list[str]:
    return _unique_preserve_order(
        line.strip()
        for line in str(value).splitlines()
        if line.strip()
    )


def _first_non_empty_line(value: str) -> str | None:
    for line in str(value).splitlines():
        normalized = line.strip()
        if normalized:
            return normalized
    return None


def _normalize_lc_family_key(value: str) -> str:
    return _normalize_special_text(value)


def _normalize_special_text(value: str) -> str:
    normalized = _WHITESPACE_RE.sub(" ", str(value).strip().upper())
    normalized = normalized.replace("\\", " ")
    normalized = normalized.replace("/", " ")
    normalized = normalized.replace("-", " ")
    normalized = _SPECIAL_RE.sub(" ", normalized)
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def _normalize_foreign_lc_reference(value: str) -> str:
    numeric_text = _normalize_numeric_foreign_lc_reference(str(value))
    if numeric_text:
        return numeric_text
    normalized = _normalize_special_text(value)
    if not normalized:
        return ""
    tokens = [token for token in normalized.split() if token != "AND"]
    normalized = " ".join(tokens)
    numeric_text = _normalize_numeric_foreign_lc_reference(normalized)
    return numeric_text or normalized


def _normalize_numeric_foreign_lc_reference(value: str) -> str:
    compact = _WHITESPACE_RE.sub("", str(value).strip())
    if not compact:
        return ""
    match = re.fullmatch(r"(\d+)(?:\.0+)?", compact)
    if match is None:
        return ""
    normalized = match.group(1).lstrip("0")
    return normalized or "0"


def _build_erp_consistency_issues(
    *,
    buyer_names: list[str],
    lc_dates: list[str],
    ship_dates: list[str],
    expiry_dates: list[str],
    ship_remarks: list[str],
) -> list[str]:
    issues: list[str] = []
    if not buyer_names:
        issues.append("buyer name was missing")
    elif len(buyer_names) != 1:
        issues.append(f"buyer name values conflicted ({', '.join(buyer_names)})")
    if len(lc_dates) != 1:
        issues.append(_format_single_value_consistency_issue("LC Date", lc_dates))
    if len(ship_dates) != 1:
        issues.append(_format_single_value_consistency_issue("Shipment Date", ship_dates))
    if len(expiry_dates) != 1:
        issues.append(_format_single_value_consistency_issue("Expiry Date", expiry_dates))
    if len(ship_remarks) > 1:
        issues.append(f"Ship. Remarks values conflicted ({', '.join(ship_remarks)})")
    return issues


def _format_single_value_consistency_issue(field_label: str, values: list[str]) -> str:
    if not values:
        return f"{field_label} was missing"
    return f"{field_label} values conflicted ({', '.join(values)})"


def _normalize_buyer_comparison_text(value: str) -> str:
    normalized = _normalize_special_text(value)
    if not normalized:
        return ""
    normalized = _LTD_OR_LIMITED_RE.sub("LTD", normalized)
    tokens = []
    for token in normalized.split():
        if token.endswith("S"):
            token = token[:-1]
        if token:
            tokens.append(token)
    return "".join(tokens)


def _compare_buyer_details(
    *,
    buyer_name: str,
    irc_details: str,
    erc_details: str,
) -> list[str]:
    normalized_buyer = _normalize_buyer_comparison_text(buyer_name)
    normalized_irc = _normalize_buyer_comparison_text(irc_details)
    normalized_erc = _normalize_buyer_comparison_text(erc_details)

    irc_has_data = bool(normalized_irc)
    erc_has_data = bool(normalized_erc)
    irc_matches = irc_has_data and normalized_buyer in normalized_irc
    erc_matches = erc_has_data and normalized_buyer in normalized_erc

    if irc_has_data and erc_has_data:
        messages: list[str] = []
        if not irc_matches:
            messages.append("IRC Details did not contain the ERP buyer name.")
        if not erc_matches:
            messages.append("ERC Details did not contain the ERP buyer name.")
        return messages

    if irc_has_data:
        return [] if irc_matches else ["IRC Details did not contain the ERP buyer name."]
    if erc_has_data:
        return [] if erc_matches else ["ERC Details did not contain the ERP buyer name."]
    return ["Both IRC Details and ERC Details were empty, so the ERP buyer name could not be verified."]


def _build_mismatch_status(decision_reasons: list[str]) -> str:
    topics = _unique_preserve_order(
        topic
        for reason in decision_reasons
        for topic in _decision_reason_to_topics(reason)
        if topic
    )
    if topics:
        return f"{', '.join(topics)} mismatch"
    return "Mismatch"


def _decision_reason_to_topics(reason: str) -> list[str]:
    normalized = str(reason).strip()
    if not normalized:
        return []
    if normalized.startswith("Beneficiary mismatch:"):
        return ["Beneficiary"]
    if normalized.startswith("IRC Details"):
        return ["IRC Details"]
    if normalized.startswith("ERC Details"):
        return ["ERC Details"]
    if normalized.startswith("Both IRC Details and ERC Details"):
        return ["IRC Details", "ERC Details"]
    if normalized.startswith("LC Date mismatch:"):
        return ["LC Date"]
    if normalized.startswith("Last Date of Shipment mismatch:"):
        return ["Shipment Date"]
    if normalized.startswith("LC Expiry Date mismatch:"):
        return ["Expiry Date"]
    if normalized.startswith("ERP date window mismatch:"):
        return ["ERP Date Window"]
    if normalized.startswith("LC Value could not be parsed") or normalized.startswith("LC Value mismatch:"):
        return ["Value"]
    if normalized.startswith("Related Foreign LC/Contract Information"):
        return ["Foreign LC No"]
    if normalized.startswith("Dashboard quantity rows could not be parsed.") or normalized.startswith("Quantity mismatch:"):
        return ["Quantity"]
    if normalized.startswith("Excess mismatch:"):
        return ["Value", "Quantity"]
    return []


def _normalize_date(value: str) -> str | None:
    from project.erp.normalization import normalize_lc_sc_date

    return normalize_lc_sc_date(value)


def _parse_iso_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _date_is_same_or_after(*, dashboard_date: str | None, erp_date: str | None) -> bool:
    if dashboard_date is None or erp_date is None:
        return False
    return dashboard_date >= erp_date


def _date_is_same_or_after_and_within_days(
    *,
    dashboard_date: str | None,
    erp_date: str | None,
    max_days: int,
) -> bool:
    day_delta = _days_between_dates(start_date=erp_date, end_date=dashboard_date)
    if day_delta is None:
        return False
    return 0 <= day_delta <= max_days


def _date_is_between_days_after(
    *,
    earlier_date: str | None,
    later_date: str | None,
    min_days: int,
    max_days: int,
) -> bool:
    day_delta = _days_between_dates(start_date=earlier_date, end_date=later_date)
    if day_delta is None:
        return False
    return min_days <= day_delta <= max_days


def _days_between_dates(*, start_date: str | None, end_date: str | None) -> int | None:
    start = _parse_iso_date(start_date)
    end = _parse_iso_date(end_date)
    if start is None or end is None:
        return None
    return (end - start).days


def _parse_decimal(value: object) -> Decimal | None:
    candidate = str(value).strip().replace(",", "")
    if not candidate:
        return None
    try:
        return Decimal(candidate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def _sum_decimal_strings(values) -> Decimal | None:
    total = Decimal("0.00")
    saw_value = False
    for value in values:
        parsed = _parse_decimal(value)
        if parsed is None:
            if str(value).strip():
                return None
            continue
        saw_value = True
        total += parsed
    if not saw_value:
        return None
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _decimal_matches(
    left: Decimal,
    right: Decimal,
    *,
    tolerance: Decimal = _DEFAULT_DECIMAL_TOLERANCE,
) -> bool:
    return abs(left - right) <= tolerance


def _decimal_relation(
    left: Decimal,
    right: Decimal,
    *,
    tolerance: Decimal = _DEFAULT_DECIMAL_TOLERANCE,
) -> str:
    if _decimal_matches(left, right, tolerance=tolerance):
        return "equal"
    if left > right:
        return "higher"
    return "lower"


def _decimal_to_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = format(value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _format_workbook_date(value: str) -> str:
    normalized = _normalize_date(value)
    if normalized is None:
        return value
    year, month, day = normalized.split("-")
    return f"{day}/{month}/{year}"


def _unique_preserve_order(values) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _dedupe_erp_rows(rows: list) -> list:
    seen: set[tuple[str, ...]] = set()
    deduped: list = []
    for row in rows:
        signature = (
            str(getattr(row, "file_number", "")),
            str(getattr(row, "lc_sc_number", "")),
            str(getattr(row, "buyer_name", "")),
            str(getattr(row, "lc_sc_date", "")),
            str(getattr(row, "current_lc_value", "")),
            str(getattr(row, "ship_date", "")),
            str(getattr(row, "expiry_date", "")),
            str(getattr(row, "lc_qty", "")),
            str(getattr(row, "net_weight", "")),
            str(getattr(row, "ship_remarks", "")),
        )
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(row)
    return deduped
