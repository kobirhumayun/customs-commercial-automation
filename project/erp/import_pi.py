from __future__ import annotations

import csv
import io
import json
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

from project.erp.providers import (
    _decode_delimited_export_text,
    _resolve_delimiter,
    inspect_playwright_report_download,
)


@dataclass(slots=True, frozen=True)
class ImportPIRegisterRow:
    pi_number: str
    quantity_kg: str
    total_amount: str
    source_row_index: int
    raw_values: dict[str, str] = field(default_factory=dict)


class ImportPIRegisterProvider(Protocol):
    def lookup_pi_numbers(self, *, pi_numbers: list[str]) -> dict[str, list[ImportPIRegisterRow]]:
        """Return import PI register rows keyed by canonical PI number."""

    def load_rows(self) -> list[ImportPIRegisterRow]:
        """Return all normalized import PI rows available to the provider."""


@dataclass(slots=True, frozen=True)
class EmptyImportPIRegisterProvider:
    def lookup_pi_numbers(self, *, pi_numbers: list[str]) -> dict[str, list[ImportPIRegisterRow]]:
        return {pi_number: [] for pi_number in pi_numbers}

    def load_rows(self) -> list[ImportPIRegisterRow]:
        return []


@dataclass(slots=True, frozen=True)
class JsonManifestImportPIRegisterProvider:
    manifest_path: Path

    def lookup_pi_numbers(self, *, pi_numbers: list[str]) -> dict[str, list[ImportPIRegisterRow]]:
        return _index_pi_rows(pi_numbers=pi_numbers, rows=self.load_rows())

    def load_rows(self) -> list[ImportPIRegisterRow]:
        with self.manifest_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        raw_rows = payload.get("rows") if isinstance(payload, dict) else payload
        if not isinstance(raw_rows, list):
            raise ValueError("Import PI register manifest must be a JSON array or an object with a 'rows' array")
        rows = []
        for index, item in enumerate(raw_rows):
            if not isinstance(item, dict):
                raise ValueError(f"Import PI register row at index {index} must be a JSON object")
            rows.append(
                _build_import_pi_row(
                    item,
                    source_row_index=_require_int(item, "source_row_index", index),
                    row_label=f"Import PI manifest row {index}",
                )
            )
        return rows


@dataclass(slots=True, frozen=True)
class DelimitedImportPIRegisterProvider:
    export_path: Path
    delimiter: str | None = None

    def lookup_pi_numbers(self, *, pi_numbers: list[str]) -> dict[str, list[ImportPIRegisterRow]]:
        return _index_pi_rows(pi_numbers=pi_numbers, rows=self.load_rows())

    def load_rows(self) -> list[ImportPIRegisterRow]:
        if not self.export_path.exists():
            raise ValueError(f"Import PI register path does not exist: {self.export_path}")
        text = _decode_delimited_export_text(self.export_path)
        handle = io.StringIO(text, newline="")
        sample = handle.read(4096)
        handle.seek(0)
        reader = csv.reader(handle, delimiter=self.delimiter or _resolve_delimiter(self.export_path, sample))
        matrix = [[cell.strip() for cell in row] for row in reader]
        return _load_import_pi_rows_from_matrix(matrix, source_name=str(self.export_path))


@dataclass(slots=True)
class PlaywrightImportPIRegisterProvider:
    base_url: str
    report_relative_url: str = "/RptExportPInLC/PIRegisterCustomsPDL"
    browser_channel: str | None = None
    storage_state_path: Path | None = None
    field_values: tuple[tuple[str, str], ...] = ()
    submit_selector: str | None = None
    post_submit_wait_selector: str | None = None
    download_menu_selector: str | None = None
    download_format_selector: str | None = None
    timeout_ms: int = 120_000
    headless: bool = True
    _cached_rows: tuple[ImportPIRegisterRow, ...] | None = field(default=None, init=False, repr=False)

    def lookup_pi_numbers(self, *, pi_numbers: list[str]) -> dict[str, list[ImportPIRegisterRow]]:
        return _index_pi_rows(pi_numbers=pi_numbers, rows=self.load_rows())

    def load_rows(self) -> list[ImportPIRegisterRow]:
        if self._cached_rows is not None:
            return list(self._cached_rows)
        rows = _load_import_pi_rows_from_playwright_download(
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
        self._cached_rows = tuple(rows)
        return rows


def canonicalize_import_pi_number(raw_value: object) -> str | None:
    text = str(raw_value or "").strip().upper()
    text = text.replace("\\", "/").replace("-", "/")
    parts = [part.strip() for part in text.split("/") if part.strip()]
    if len(parts) != 3 or parts[0] not in {"BTL", "KYL"}:
        return None
    if not (parts[1].isdigit() and len(parts[1]) == 2 and parts[2].isdigit() and len(parts[2]) == 4):
        return None
    return f"{parts[0]}/{parts[1]}/{parts[2]}"


def parse_import_pi_decimal(raw_value: object) -> Decimal | None:
    text = str(raw_value or "").strip().replace(" ", "")
    if not text or any(character not in "0123456789,." for character in text):
        return None
    if text.endswith(".") and text.count(".") == 1:
        text = text[:-1]
    if not text:
        return None

    candidates: list[str] = []
    if "," in text and "." in text:
        decimal_separator = "." if text.rfind(".") > text.rfind(",") else ","
        grouping_separator = "," if decimal_separator == "." else "."
        parsed = _parse_grouped_decimal(
            text,
            grouping_separator=grouping_separator,
            decimal_separator=decimal_separator,
        )
        if parsed is not None:
            candidates.append(parsed)
    elif "," in text:
        grouped = _parse_grouped_decimal(text, grouping_separator=",", decimal_separator=None)
        if grouped is not None:
            candidates.append(grouped)
        if text.count(",") == 1:
            integer_part, fraction_part = text.split(",", 1)
            if integer_part.isdigit() and fraction_part.isdigit() and 1 <= len(fraction_part) <= 3:
                candidates.append(f"{integer_part}.{fraction_part}")
    elif "." in text:
        if text.count(".") == 1:
            integer_part, fraction_part = text.split(".", 1)
            if integer_part.isdigit() and fraction_part.isdigit() and 1 <= len(fraction_part) <= 3:
                candidates.append(f"{integer_part}.{fraction_part}")
        grouped = _parse_grouped_decimal(text, grouping_separator=".", decimal_separator=None)
        if grouped is not None:
            candidates.append(grouped)
    else:
        candidates.append(text if text.isdigit() else "")

    for candidate in candidates:
        try:
            value = Decimal(candidate)
        except InvalidOperation:
            continue
        if value >= 0:
            return value
    return None


def format_import_pi_decimal(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(int(value))
    normalized = value.normalize()
    return format(normalized, "f")


def _load_import_pi_rows_from_playwright_download(
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
) -> list[ImportPIRegisterRow]:
    if not download_format_selector:
        raise ValueError("Live import PI register flow requires a configured download format selector.")
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
            raise ValueError(f"Live import PI register download failed: {payload.get('error') or 'unknown error'}")
        downloaded_file_path = str(payload.get("downloaded_file_path") or "").strip()
        if not downloaded_file_path:
            raise ValueError("Live import PI register flow completed without a downloaded file.")
        return DelimitedImportPIRegisterProvider(Path(downloaded_file_path)).load_rows()


def _load_import_pi_rows_from_matrix(matrix: list[list[str]], *, source_name: str) -> list[ImportPIRegisterRow]:
    header_index, mapping = _find_import_pi_header_mapping(matrix)
    if header_index is None or mapping is None:
        raise ValueError(f"{source_name} is missing required import PI register headers: PI Number, Qty.Kg, Total Amount")
    rows = []
    for source_row_index, row_values in enumerate(matrix[header_index + 1 :], start=header_index + 2):
        extracted = {
            key: row_values[column_index].strip() if column_index < len(row_values) else ""
            for key, column_index in mapping.items()
        }
        if not any(value.strip() for value in extracted.values()):
            continue
        if not extracted["pi_number"].strip():
            continue
        rows.append(
            _build_import_pi_row(
                extracted,
                source_row_index=source_row_index,
                row_label=f"{source_name} row {source_row_index}",
            )
        )
    return rows


def _find_import_pi_header_mapping(matrix: list[list[str]]) -> tuple[int | None, dict[str, int] | None]:
    for index, row in enumerate(matrix):
        mapping = _build_import_pi_header_mapping(row)
        if {"pi_number", "quantity_kg", "total_amount"} <= set(mapping):
            return index, mapping
    return None, None


def _build_import_pi_header_mapping(headers: list[str]) -> dict[str, int]:
    aliases = {
        "pi_number": {"PI NUMBER", "PI NO", "PI NO"},
        "quantity_kg": {"QTY KG", "QTY KGS", "QUANTITY KG", "QUANTITY KGS", "QUANTITY KGS"},
        "total_amount": {"TOTAL AMOUNT", "TOTAL VALUE", "PI VALUE", "AMOUNT"},
    }
    normalized = [_normalize_header(header) for header in headers]
    mapping: dict[str, int] = {}
    for key, allowed in aliases.items():
        for index, header in enumerate(normalized):
            if header in allowed:
                mapping[key] = index
                break
    return mapping


def _build_import_pi_row(
    item: dict[str, object],
    *,
    source_row_index: int,
    row_label: str,
) -> ImportPIRegisterRow:
    pi_number = canonicalize_import_pi_number(item.get("pi_number"))
    quantity = parse_import_pi_decimal(item.get("quantity_kg"))
    total_amount = parse_import_pi_decimal(item.get("total_amount"))
    if pi_number is None:
        raise ValueError(f"{row_label} contains an invalid PI Number")
    if quantity is None:
        raise ValueError(f"{row_label} contains an invalid Qty.Kg")
    if total_amount is None:
        raise ValueError(f"{row_label} contains an invalid Total Amount")
    return ImportPIRegisterRow(
        pi_number=pi_number,
        quantity_kg=format_import_pi_decimal(quantity),
        total_amount=format_import_pi_decimal(total_amount),
        source_row_index=source_row_index,
        raw_values={key: str(value or "") for key, value in item.items()},
    )


def _index_pi_rows(
    *,
    pi_numbers: list[str],
    rows: list[ImportPIRegisterRow],
) -> dict[str, list[ImportPIRegisterRow]]:
    normalized_numbers = []
    for pi_number in pi_numbers:
        canonical = canonicalize_import_pi_number(pi_number)
        if canonical is not None and canonical not in normalized_numbers:
            normalized_numbers.append(canonical)
    indexed: dict[str, list[ImportPIRegisterRow]] = {pi_number: [] for pi_number in normalized_numbers}
    for row in rows:
        if row.pi_number in indexed:
            indexed[row.pi_number].append(row)
    for matched_rows in indexed.values():
        matched_rows.sort(key=lambda row: row.source_row_index)
    return indexed


def _parse_grouped_decimal(
    value: str,
    *,
    grouping_separator: str,
    decimal_separator: str | None,
) -> str | None:
    if decimal_separator is not None and value.count(decimal_separator) > 1:
        return None
    if decimal_separator is not None and decimal_separator in value:
        integer_part, fractional_part = value.rsplit(decimal_separator, 1)
        if not fractional_part.isdigit():
            return None
    else:
        integer_part, fractional_part = value, ""
    if grouping_separator not in integer_part:
        return integer_part + (f".{fractional_part}" if fractional_part else "") if integer_part.isdigit() else None
    groups = integer_part.split(grouping_separator)
    if not _valid_grouped_integer(groups):
        return None
    return "".join(groups) + (f".{fractional_part}" if fractional_part else "")


def _valid_grouped_integer(groups: list[str]) -> bool:
    if len(groups) < 2:
        return False
    if not groups[0].isdigit() or not 1 <= len(groups[0]) <= 3:
        return False
    if any(not group.isdigit() for group in groups[1:]):
        return False
    western_grouping = all(len(group) == 3 for group in groups[1:])
    indian_grouping = len(groups[-1]) == 3 and all(len(group) == 2 for group in groups[1:-1])
    return western_grouping or indian_grouping


def _normalize_header(raw_value: str) -> str:
    normalized = raw_value.strip().upper()
    normalized = normalized.replace("/", " ")
    normalized = normalized.replace("\\", " ")
    normalized = normalized.replace(".", " ")
    normalized = normalized.replace("-", " ")
    normalized = normalized.replace("(", " ")
    normalized = normalized.replace(")", " ")
    return " ".join(normalized.split())


def _require_int(item: dict[str, object], key: str, index: int) -> int:
    value = item.get(key)
    if isinstance(value, int):
        return value
    raise ValueError(f"Import PI register row at index {index} is missing integer '{key}'")
