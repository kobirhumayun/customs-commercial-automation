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
    candidate_families = _build_candidate_families(
        workbook_snapshot=workbook_snapshot,
        header_mapping=header_mapping,
        sl_no_values_by_row=sl_no_values_by_row,
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
) -> list[DashboardCandidateFamily]:
    families: dict[str, list[DashboardCandidateRow]] = {}
    ordered_keys: list[str] = []
    for row in workbook_snapshot.rows:
        candidate = _build_candidate_row(
            row=row,
            header_mapping=header_mapping,
            sl_no_values_by_row=sl_no_values_by_row,
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
        return _resolve_live_sl_no_values_by_row(
            workbook_path=live_workbook_path,
            column_index=sl_no_column_index,
            row_indexes=row_indexes,
        )
    rows_by_index = {row.row_index: row for row in workbook_snapshot.rows}
    resolved: dict[int, str] = {}
    for row_index in row_indexes:
        row = rows_by_index.get(row_index)
        if row is None:
            continue
        resolved[row_index] = _stringify_sl_no_text(row.values.get(sl_no_column_index, ""))
    return resolved


def _resolve_live_sl_no_values_by_row(
    *,
    workbook_path: Path,
    column_index: int,
    row_indexes: set[int],
) -> dict[int, str]:
    if not row_indexes:
        return {}
    try:
        import xlwings  # type: ignore
    except ImportError as exc:
        raise ValueError("xlwings is required to resolve displayed workbook SL.No. values from the live workbook.") from exc

    app = xlwings.App(visible=False, add_book=False)
    book = None
    try:
        book = app.books.open(str(workbook_path), update_links=False, read_only=True)
        sheet = book.sheets[0]
        return _read_live_sl_no_values(
            sheet=sheet,
            column_index=column_index,
            row_indexes=sorted(row_indexes),
        )
    finally:
        if book is not None:
            book.close()
        app.quit()


def _read_live_sl_no_values(*, sheet, column_index: int, row_indexes: list[int]) -> dict[int, str]:
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
    if not buyer_names or len(buyer_names) != 1 or len(lc_dates) != 1 or len(ship_dates) != 1 or len(expiry_dates) != 1 or len(ship_remarks) > 1:
        return None, _build_discrepancy(
            run_report=run_report,
            code="bb_dashboard_family_input_invalid",
            message=f"ERP family inputs were not deterministically consistent for workbook family {family.lc_sc_no}.",
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
    normalized = _normalize_special_text(value)
    if not normalized:
        return ""
    tokens = [token for token in normalized.split() if token != "AND"]
    return " ".join(tokens)


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
