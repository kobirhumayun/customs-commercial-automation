from __future__ import annotations

import csv
import json
import hashlib
import io
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin

from project.erp.models import ERPRegisterRow
from project.erp.normalization import (
    normalize_buyer_name,
    normalize_buyer_name_for_paths,
    normalize_lc_sc_date,
    normalize_lc_sc_number,
)
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
    report_relative_url: str = "/RptCommercialExport/DateWiseLCRegisterForDocuments"
    browser_channel: str | None = None
    storage_state_path: Path | None = None
    field_values: tuple[tuple[str, str], ...] = ()
    submit_selector: str | None = None
    post_submit_wait_selector: str | None = None
    download_menu_selector: str | None = None
    download_format_selector: str | None = None
    table_selector: str = "table"
    timeout_ms: int = 120_000
    headless: bool = True

    def lookup_rows(self, *, file_numbers: list[str]) -> dict[str, list[ERPRegisterRow]]:
        if self._uses_download_flow:
            rows = _load_rows_from_playwright_download(
                base_url=self.base_url,
                report_relative_url=self.report_relative_url,
                browser_channel=self.browser_channel,
                storage_state_path=self.storage_state_path,
                timeout_ms=self.timeout_ms,
                headless=self.headless,
                field_values=list(self.field_values),
                submit_selector=self.submit_selector,
                post_submit_wait_selector=self.post_submit_wait_selector,
                download_menu_selector=self.download_menu_selector,
                download_format_selector=self.download_format_selector,
            )
            return _index_rows(file_numbers=file_numbers, rows=rows)

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

    @property
    def _uses_download_flow(self) -> bool:
        return bool(
            self.field_values
            or self.submit_selector
            or self.post_submit_wait_selector
            or self.download_menu_selector
            or self.download_format_selector
        )


def inspect_playwright_report_download(
    *,
    base_url: str,
    report_relative_url: str,
    browser_channel: str | None,
    storage_state_path: Path | None,
    timeout_ms: int,
    headless: bool,
    output_dir: Path,
    field_values: list[tuple[str, str]],
    submit_selector: str | None = None,
    post_submit_wait_selector: str | None = None,
    download_menu_selector: str | None = None,
    download_format_selector: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "issue",
        "target_url": None,
        "final_url": None,
        "page_title": None,
        "headless": headless,
        "output_dir": str(output_dir),
        "filled_fields": [
            {"selector": selector, "value": value}
            for selector, value in field_values
        ],
        "submit_selector": submit_selector,
        "post_submit_wait_selector": post_submit_wait_selector,
        "download_menu_selector": download_menu_selector,
        "download_format_selector": download_format_selector,
        "html_path": None,
        "screenshot_path": None,
        "downloaded_file_path": None,
        "field_readbacks": [],
        "download_receipt": None,
        "error": None,
    }
    output_dir.mkdir(parents=True, exist_ok=True)

    if not base_url.strip():
        payload["error"] = "Live ERP provider requires a non-empty erp_base_url"
        return payload
    if storage_state_path is not None and not storage_state_path.exists():
        payload["error"] = f"Playwright storage state path does not exist: {storage_state_path}"
        return payload

    target_url = urljoin(base_url.rstrip("/") + "/", report_relative_url.lstrip("/"))
    payload["target_url"] = target_url
    screenshot_path = output_dir / "erp-page.png"
    html_path = output_dir / "erp-page.html"
    payload["screenshot_path"] = str(screenshot_path)
    payload["html_path"] = str(html_path)

    page = None
    browser = None
    context = None
    try:
        sync_playwright = _load_playwright_sync_api()
        with sync_playwright() as playwright:
            browser_launch_kwargs: dict[str, object] = {"headless": headless}
            if browser_channel:
                browser_launch_kwargs["channel"] = browser_channel
            browser = playwright.chromium.launch(**browser_launch_kwargs)
            context_kwargs: dict[str, object] = {"accept_downloads": True}
            if storage_state_path is not None:
                context_kwargs["storage_state"] = str(storage_state_path)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
            _best_effort_wait_for_network_idle(page, timeout_ms=timeout_ms)

            for selector, value in field_values:
                locator = page.locator(selector)
                locator.wait_for(state="visible", timeout=timeout_ms)
                locator.click()
                locator.fill(value)
                try:
                    locator.press("Tab")
                except Exception:
                    pass
            payload["field_readbacks"] = _collect_field_readbacks(page, field_values)

            if submit_selector:
                page.locator(submit_selector).click()
                if post_submit_wait_selector:
                    page.locator(post_submit_wait_selector).wait_for(
                        state="visible",
                        timeout=timeout_ms,
                    )
                else:
                    _best_effort_wait_for_network_idle(page, timeout_ms=timeout_ms)

            if download_menu_selector:
                page.locator(download_menu_selector).click()

            if download_format_selector:
                with page.expect_download(timeout=timeout_ms) as download_info:
                    page.locator(download_format_selector).click()
                download = download_info.value
                suggested_filename = _sanitize_download_filename(download.suggested_filename)
                downloaded_path = output_dir / suggested_filename
                download.save_as(str(downloaded_path))
                payload["downloaded_file_path"] = str(downloaded_path)
                payload["download_receipt"] = _build_download_receipt(
                    downloaded_path=downloaded_path,
                    suggested_filename=download.suggested_filename,
                )

            payload["final_url"] = page.url
            payload["page_title"] = page.title()
            _write_debug_page_artifacts(page, html_path=html_path, screenshot_path=screenshot_path)
            payload["status"] = "ready"
            return payload
    except Exception as exc:
        payload["error"] = str(exc)
        if page is not None:
            try:
                payload["final_url"] = payload["final_url"] or page.url
            except Exception:
                pass
            try:
                _write_debug_page_artifacts(page, html_path=html_path, screenshot_path=screenshot_path)
            except Exception:
                pass
        return payload
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def _index_rows(*, file_numbers: list[str], rows: list[ERPRegisterRow]) -> dict[str, list[ERPRegisterRow]]:
    indexed: dict[str, list[ERPRegisterRow]] = {file_number: [] for file_number in file_numbers}
    for row in rows:
        if row.file_number in indexed:
            indexed[row.file_number].append(row)
    for file_number, matched_rows in indexed.items():
        matched_rows.sort(key=lambda row: row.source_row_index)
    return indexed


def _load_rows_from_playwright_download(
    *,
    base_url: str,
    report_relative_url: str,
    browser_channel: str | None,
    storage_state_path: Path | None,
    timeout_ms: int,
    headless: bool,
    field_values: list[tuple[str, str]],
    submit_selector: str | None,
    post_submit_wait_selector: str | None,
    download_menu_selector: str | None,
    download_format_selector: str | None,
) -> list[ERPRegisterRow]:
    if not download_format_selector:
        raise ValueError("Live ERP download flow requires a configured download format selector.")

    with tempfile.TemporaryDirectory() as temp_dir:
        payload = inspect_playwright_report_download(
            base_url=base_url,
            report_relative_url=report_relative_url,
            browser_channel=browser_channel,
            storage_state_path=storage_state_path,
            timeout_ms=timeout_ms,
            headless=headless,
            output_dir=Path(temp_dir),
            field_values=field_values,
            submit_selector=submit_selector,
            post_submit_wait_selector=post_submit_wait_selector,
            download_menu_selector=download_menu_selector,
            download_format_selector=download_format_selector,
        )

        if payload.get("status") != "ready":
            raise ValueError(f"Live ERP download flow failed: {payload.get('error') or 'unknown error'}")

        readbacks = payload.get("field_readbacks")
        if isinstance(readbacks, list):
            mismatched_selectors = [
                str(item.get("selector"))
                for item in readbacks
                if isinstance(item, dict) and item.get("matched") is False
            ]
            if mismatched_selectors:
                raise ValueError(
                    "Live ERP form did not retain one or more filled values: "
                    + ", ".join(mismatched_selectors)
                )

        downloaded_file_path = str(payload.get("downloaded_file_path") or "").strip()
        if not downloaded_file_path:
            raise ValueError("Live ERP download flow completed without a downloaded file.")

        receipt = payload.get("download_receipt")
        if isinstance(receipt, dict):
            if receipt.get("exists") is False:
                raise ValueError("Live ERP downloaded file was not saved successfully.")
            if receipt.get("is_empty") is True:
                raise ValueError("Live ERP downloaded file is empty.")
            if receipt.get("looks_like_html") is True:
                raise ValueError("Live ERP downloaded file appears to be HTML instead of a delimited export.")
            if receipt.get("has_required_erp_headers") is False:
                missing = receipt.get("erp_header_missing")
                if isinstance(missing, list) and missing:
                    raise ValueError(
                        "Live ERP downloaded file is missing required ERP headers: "
                        + ", ".join(str(item) for item in missing)
                    )
                raise ValueError("Live ERP downloaded file did not expose required ERP headers.")

        return _load_delimited_export_rows(Path(downloaded_file_path), delimiter=None)


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
    text = _decode_delimited_export_text(path)
    handle = io.StringIO(text, newline="")
    sample = handle.read(4096)
    handle.seek(0)
    resolved_delimiter = delimiter or _resolve_delimiter(path, sample)
    reader = csv.reader(handle, delimiter=resolved_delimiter)
    matrix = [[cell.strip() for cell in row] for row in reader]
    return _load_rows_from_table_matrix(matrix, source_name=str(path))


def _decode_delimited_export_text(path: Path) -> str:
    raw_bytes = path.read_bytes()
    decode_errors: list[str] = []
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError as exc:
            decode_errors.append(f"{encoding}: {exc}")
    raise ValueError(
        f"ERP export could not be decoded using supported encodings for {path}: " + "; ".join(decode_errors)
    )


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
    raw_buyer_name = _require_string(item, "buyer_name", row_label)
    buyer_name = normalize_buyer_name(raw_buyer_name)
    folder_buyer_name = normalize_buyer_name_for_paths(raw_buyer_name)
    lc_sc_date = normalize_lc_sc_date(_require_string(item, "lc_sc_date", row_label))
    if (
        file_number is None
        or lc_sc_number is None
        or buyer_name is None
        or folder_buyer_name is None
        or lc_sc_date is None
    ):
        raise ValueError(f"{row_label} contains an invalid canonical ERP field")
    return ERPRegisterRow(
        file_number=file_number,
        lc_sc_number=lc_sc_number,
        buyer_name=buyer_name,
        lc_sc_date=lc_sc_date,
        source_row_index=source_row_index,
        folder_buyer_name=folder_buyer_name,
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

    target_url = urljoin(base_url.rstrip("/") + "/", report_relative_url.lstrip("/"))
    sync_playwright = _load_playwright_sync_api()
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
                _best_effort_wait_for_network_idle(page, timeout_ms=timeout_ms)
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


def _load_playwright_sync_api():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ValueError("Playwright is required for live ERP lookup") from exc
    return sync_playwright


def _best_effort_wait_for_network_idle(page, *, timeout_ms: int) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        return None


def _write_debug_page_artifacts(page, *, html_path: Path, screenshot_path: Path) -> None:
    html_path.write_text(page.content(), encoding="utf-8")
    page.screenshot(path=str(screenshot_path), full_page=True)


def _sanitize_download_filename(filename: str) -> str:
    normalized = filename.strip() or "erp-download.bin"
    return Path(normalized).name or "erp-download.bin"


def _collect_field_readbacks(page, field_values: list[tuple[str, str]]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for selector, expected_value in field_values:
        locator = page.locator(selector)
        try:
            matched_count = locator.count()
        except Exception as exc:
            records.append(
                {
                    "selector": selector,
                    "expected_value": expected_value,
                    "observed_value": None,
                    "matched": False,
                    "matched_count": None,
                    "error": str(exc),
                }
            )
            continue

        observed_value: str | None = None
        error: str | None = None
        if matched_count > 0:
            try:
                observed_value = locator.first.input_value()
            except Exception as exc:
                error = str(exc)
        records.append(
            {
                "selector": selector,
                "expected_value": expected_value,
                "observed_value": observed_value,
                "matched": observed_value == expected_value,
                "matched_count": matched_count,
                "error": error,
            }
        )
    return records


def _build_download_receipt(*, downloaded_path: Path, suggested_filename: str | None) -> dict[str, object]:
    exists = downloaded_path.exists()
    size_bytes = downloaded_path.stat().st_size if exists else None
    sha256 = _sha256_file(downloaded_path) if exists else None
    file_probe = _probe_downloaded_file(downloaded_path) if exists else {
        "content_kind": "missing",
        "looks_like_html": False,
        "is_empty": True,
        "line_count": 0,
        "header_preview": None,
        "has_required_erp_headers": False,
        "erp_header_missing": list(REQUIRED_ERP_EXPORT_HEADERS),
    }
    return {
        "path": str(downloaded_path),
        "exists": exists,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "suggested_filename": suggested_filename,
        "saved_filename": downloaded_path.name,
        **file_probe,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _probe_downloaded_file(path: Path) -> dict[str, object]:
    raw_bytes = path.read_bytes()
    if not raw_bytes:
        return {
            "content_kind": "empty",
            "looks_like_html": False,
            "is_empty": True,
            "line_count": 0,
            "header_preview": None,
            "has_required_erp_headers": False,
            "erp_header_missing": list(REQUIRED_ERP_EXPORT_HEADERS),
        }

    header_sample = raw_bytes[:512].decode("utf-8", errors="ignore").lstrip()
    looks_like_html = header_sample.lower().startswith("<!doctype html") or header_sample.lower().startswith("<html")

    text = raw_bytes.decode("utf-8-sig", errors="ignore")
    lines = [line.strip() for line in text.splitlines()]
    non_empty_lines = [line for line in lines if line]
    header_preview = non_empty_lines[1] if len(non_empty_lines) >= 2 else (non_empty_lines[0] if non_empty_lines else None)
    header_cells = [cell.strip() for cell in header_preview.split(",")] if header_preview else []
    has_required_headers = False
    missing_headers = list(REQUIRED_ERP_EXPORT_HEADERS)
    if header_cells:
        try:
            mapping = _resolve_header_mapping(header_cells, source_name=str(path))
            has_required_headers = all(key in mapping for key in REQUIRED_ERP_EXPORT_HEADERS)
            missing_headers = []
        except ValueError:
            has_required_headers = False

    return {
        "content_kind": "html" if looks_like_html else "delimited_text",
        "looks_like_html": looks_like_html,
        "is_empty": False,
        "line_count": len(non_empty_lines),
        "header_preview": header_preview,
        "has_required_erp_headers": has_required_headers,
        "erp_header_missing": missing_headers,
    }
