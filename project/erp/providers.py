from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin

from project.erp.models import ERPRegisterRow
from project.erp.normalization import normalize_buyer_name, normalize_lc_sc_date, normalize_lc_sc_number
from project.workflows.export_lc_sc.parsing import normalize_file_number

REQUIRED_ERP_EXPORT_HEADERS = ("file_number", "lc_sc_number", "buyer_name", "lc_sc_date")
ERP_EXPORT_HEADER_ALIASES = {
    "file_number": ("FILE NO", "FILE NUMBER"),
    "lc_sc_number": ("L C NO", "LC NO", "LC SC NO"),
    "buyer_name": ("BUYER NAME", "BUYER"),
    "lc_sc_date": ("LC DT", "LC DATE"),
    "notify_bank": ("NOTIFY BANK",),
    "current_lc_value": ("CURRENT LC VALUE", "CURRENT VALUE"),
    "ship_date": ("SHIP DT", "SHIP DATE"),
    "expiry_date": ("EXPIRY DT", "EXPIRY DATE"),
    "lc_qty": ("LC QTY", "QUANTITY"),
    "lc_unit": ("LC UNIT", "UNIT"),
    "amd_no": ("AMD NO", "AMENDMENT NO"),
    "amd_date": ("AMD DT", "AMD DATE", "AMENDMENT DATE"),
    "nego_bank": ("NEGO BANK", "NEGOTIATING BANK"),
    "master_lc_no": ("MASTER LC NO",),
    "master_lc_date": ("M L C DATE", "MASTER LC DATE"),
}


class ERPRowProvider(Protocol):
    def lookup_rows(self, *, file_numbers: list[str]) -> dict[str, list[ERPRegisterRow]]:
        """Return ERP matches keyed by canonical file number."""


@dataclass(slots=True, frozen=True)
class EmptyERPRowProvider:
    def lookup_rows(self, *, file_numbers: list[str]) -> dict[str, list[ERPRegisterRow]]:
        return {file_number: [] for file_number in file_numbers}


@dataclass(slots=True, frozen=True)
class JsonManifestERPRowProvider:
    manifest_path: Path

    def lookup_rows(self, *, file_numbers: list[str]) -> dict[str, list[ERPRegisterRow]]:
        manifest_rows = _load_manifest_rows(self.manifest_path)
        return _index_rows(file_numbers=file_numbers, rows=manifest_rows)


@dataclass(slots=True, frozen=True)
class DelimitedERPExportRowProvider:
    export_path: Path
    delimiter: str | None = None

    def lookup_rows(self, *, file_numbers: list[str]) -> dict[str, list[ERPRegisterRow]]:
        export_rows = _load_delimited_export_rows(self.export_path, delimiter=self.delimiter)
        return _index_rows(file_numbers=file_numbers, rows=export_rows)


@dataclass(slots=True, frozen=True)
class PlaywrightERPRowProvider:
    base_url: str
    report_relative_url: str = "/rptDateWiseLCRegister"
    browser_channel: str | None = None
    storage_state_path: Path | None = None
    table_selector: str = "table"
    timeout_ms: int = 120_000
    headless: bool = True

    def lookup_rows(self, *, file_numbers: list[str]) -> dict[str, list[ERPRegisterRow]]:
        tables = _fetch_playwright_report_tables(
            base_url=self.base_url,
            report_relative_url=self.report_relative_url,
            browser_channel=self.browser_channel,
            storage_state_path=self.storage_state_path,
            table_selector=self.table_selector,
            timeout_ms=self.timeout_ms,
            headless=self.headless,
        )
        last_error: ValueError | None = None
        for table_index, table in enumerate(tables):
            try:
                rows = _load_rows_from_table_matrix(
                    table,
                    source_name=f"Playwright ERP table {table_index}",
                )
            except ValueError as exc:
                last_error = exc
                continue
            return _index_rows(file_numbers=file_numbers, rows=rows)
        if last_error is not None:
            raise ValueError(f"Live ERP report did not expose a parseable register table: {last_error}") from last_error
        raise ValueError("Live ERP report did not expose any table content")


def _index_rows(*, file_numbers: list[str], rows: list[ERPRegisterRow]) -> dict[str, list[ERPRegisterRow]]:
    indexed: dict[str, list[ERPRegisterRow]] = {file_number: [] for file_number in file_numbers}
    for row in rows:
        if row.file_number in indexed:
            indexed[row.file_number].append(row)
    for file_number, matched_rows in indexed.items():
        matched_rows.sort(key=lambda row: row.source_row_index)
    return indexed


def _load_manifest_rows(path: Path) -> list[ERPRegisterRow]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, dict):
        raw_rows = payload.get("rows")
    else:
        raw_rows = payload
    if not isinstance(raw_rows, list):
        raise ValueError("ERP manifest must be a JSON array or an object with a 'rows' array")

    rows: list[ERPRegisterRow] = []
    for index, item in enumerate(raw_rows):
        if not isinstance(item, dict):
            raise ValueError(f"ERP row at index {index} must be a JSON object")
        rows.append(
            _build_erp_row(
                item,
                source_row_index=_require_int(item, "source_row_index", index),
                row_label=f"ERP manifest row {index}",
            )
        )
    return rows


def _load_delimited_export_rows(path: Path, *, delimiter: str | None) -> list[ERPRegisterRow]:
    if not path.exists():
        raise ValueError(f"ERP export path does not exist: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        resolved_delimiter = delimiter or _resolve_delimiter(path, sample)
        reader = csv.reader(handle, delimiter=resolved_delimiter)
        matrix = [[cell.strip() for cell in row] for row in reader]
    return _load_rows_from_table_matrix(matrix, source_name=str(path))


def _resolve_delimiter(path: Path, sample: str) -> str:
    if path.suffix.lower() == ".tsv":
        return "\t"
    try:
        dialect = csv.Sniffer().sniff(sample or ",", delimiters=",\t;")
        return dialect.delimiter
    except csv.Error:
        return ","


def _load_rows_from_table_matrix(matrix: list[list[str]], *, source_name: str) -> list[ERPRegisterRow]:
    if len(matrix) < 2:
        raise ValueError(f"{source_name} must contain at least a title row and a header row")

    headers = matrix[1]
    header_mapping = _resolve_header_mapping(headers, source_name=source_name)
    rows: list[ERPRegisterRow] = []
    for source_row_index, row_values in enumerate(matrix[2:], start=3):
        row = _extract_canonical_row_values(row_values, header_mapping)
        if not any(value.strip() for value in row.values()):
            continue
        if not row["file_number"].strip():
            continue
        rows.append(
            _build_erp_row(
                row,
                source_row_index=source_row_index,
                row_label=f"{source_name} row {source_row_index}",
            )
        )
    return rows


def _resolve_header_mapping(headers: list[str], *, source_name: str) -> dict[str, int]:
    normalized_headers = [_normalize_header(header) for header in headers]
    mapping: dict[str, int] = {}
    for canonical_key, aliases in ERP_EXPORT_HEADER_ALIASES.items():
        alias_set = {_normalize_header(alias) for alias in aliases}
        for index, header in enumerate(normalized_headers):
            if header in alias_set:
                mapping[canonical_key] = index
                break
    missing = [header for header in REQUIRED_ERP_EXPORT_HEADERS if header not in mapping]
    if missing:
        raise ValueError(
            f"{source_name} is missing required ERP headers: {', '.join(sorted(missing))}"
        )
    return mapping


def _extract_canonical_row_values(row_values: list[str], header_mapping: dict[str, int]) -> dict[str, str]:
    extracted: dict[str, str] = {}
    for canonical_key, column_index in header_mapping.items():
        extracted[canonical_key] = row_values[column_index].strip() if column_index < len(row_values) else ""
    return extracted


def _build_erp_row(item: dict[str, object], *, source_row_index: int, row_label: str) -> ERPRegisterRow:
    file_number = normalize_file_number(_require_string(item, "file_number", row_label))
    lc_sc_number = normalize_lc_sc_number(_require_string(item, "lc_sc_number", row_label))
    buyer_name = normalize_buyer_name(_require_string(item, "buyer_name", row_label))
    lc_sc_date = normalize_lc_sc_date(_require_string(item, "lc_sc_date", row_label))
    if file_number is None or lc_sc_number is None or buyer_name is None or lc_sc_date is None:
        raise ValueError(f"{row_label} contains an invalid canonical ERP field")
    return ERPRegisterRow(
        file_number=file_number,
        lc_sc_number=lc_sc_number,
        buyer_name=buyer_name,
        lc_sc_date=lc_sc_date,
        source_row_index=source_row_index,
        notify_bank=_optional_string(item.get("notify_bank")),
        current_lc_value=_optional_string(item.get("current_lc_value")),
        ship_date=_optional_string(item.get("ship_date")),
        expiry_date=_optional_string(item.get("expiry_date")),
        lc_qty=_optional_string(item.get("lc_qty")),
        lc_unit=_optional_string(item.get("lc_unit")),
        amd_no=_optional_string(item.get("amd_no")),
        amd_date=_optional_string(item.get("amd_date")),
        nego_bank=_optional_string(item.get("nego_bank")),
        master_lc_no=_optional_string(item.get("master_lc_no")),
        master_lc_date=_optional_string(item.get("master_lc_date")),
    )


def _normalize_header(raw_value: str) -> str:
    normalized = raw_value.strip().upper()
    normalized = normalized.replace("/", " ")
    normalized = normalized.replace("\\", " ")
    normalized = normalized.replace(".", " ")
    normalized = normalized.replace("-", " ")
    normalized = " ".join(normalized.split())
    return normalized


def _fetch_playwright_report_tables(
    *,
    base_url: str,
    report_relative_url: str,
    browser_channel: str | None,
    storage_state_path: Path | None,
    table_selector: str,
    timeout_ms: int,
    headless: bool,
) -> list[list[list[str]]]:
    if not base_url.strip():
        raise ValueError("Live ERP provider requires a non-empty erp_base_url")
    if storage_state_path is not None and not storage_state_path.exists():
        raise ValueError(f"Playwright storage state path does not exist: {storage_state_path}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ValueError("Playwright is required for live ERP lookup") from exc

    target_url = urljoin(base_url.rstrip("/") + "/", report_relative_url.lstrip("/"))
    with sync_playwright() as playwright:
        browser_launch_kwargs: dict[str, object] = {"headless": headless}
        if browser_channel:
            browser_launch_kwargs["channel"] = browser_channel
        browser = playwright.chromium.launch(**browser_launch_kwargs)
        try:
            context_kwargs: dict[str, object] = {}
            if storage_state_path is not None:
                context_kwargs["storage_state"] = str(storage_state_path)
            context = browser.new_context(**context_kwargs)
            try:
                page = context.new_page()
                page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
                return _extract_table_matrices(page, table_selector=table_selector)
            finally:
                context.close()
        finally:
            browser.close()


def _extract_table_matrices(page, *, table_selector: str) -> list[list[list[str]]]:
    table_locator = page.locator(table_selector)
    table_count = table_locator.count()
    matrices: list[list[list[str]]] = []
    for table_index in range(table_count):
        row_locator = table_locator.nth(table_index).locator("tr")
        row_count = row_locator.count()
        matrix: list[list[str]] = []
        for row_index in range(row_count):
            cell_locator = row_locator.nth(row_index).locator("th, td")
            matrix.append([text.strip() for text in cell_locator.all_inner_texts()])
        matrices.append(matrix)
    return matrices


def _require_string(item: dict[str, object], key: str, row_label: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{row_label} is missing non-empty '{key}'")
    return value


def _require_int(item: dict[str, object], key: str, index: int) -> int:
    value = item.get(key)
    if isinstance(value, int):
        return value
    raise ValueError(f"ERP row at index {index} is missing integer '{key}'")


def _optional_string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
