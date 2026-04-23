from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from project.workbook.models import WorkbookHeader, WorkbookRow, WorkbookSnapshot

_PER_CELL_NUMBER_FORMAT_HEADERS = {"Quantity of Fabrics (Yds/Mtr)"}


class WorkbookSnapshotProvider(Protocol):
    def load_snapshot(self) -> WorkbookSnapshot | None:
        """Return a workbook snapshot for deterministic staging."""


@dataclass(slots=True, frozen=True)
class EmptyWorkbookSnapshotProvider:
    def load_snapshot(self) -> WorkbookSnapshot | None:
        return None


@dataclass(slots=True, frozen=True)
class JsonManifestWorkbookSnapshotProvider:
    manifest_path: Path

    def load_snapshot(self) -> WorkbookSnapshot:
        with self.manifest_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("Workbook manifest must be a JSON object")

        sheet_name = payload.get("sheet_name", "Sheet1")
        headers_payload = payload.get("headers")
        rows_payload = payload.get("rows", [])
        if not isinstance(headers_payload, list):
            raise ValueError("Workbook manifest must include a 'headers' array")
        if not isinstance(rows_payload, list):
            raise ValueError("Workbook manifest 'rows' must be an array")

        headers = [
            WorkbookHeader(
                column_index=_require_int(item, "column_index", "header"),
                text=_require_string(item, "text", "header"),
            )
            for item in headers_payload
        ]
        rows = [
            WorkbookRow(
                row_index=_require_int(item, "row_index", "row"),
                values=_parse_row_values(item.get("values")),
                number_formats=_parse_row_values(item.get("number_formats", {})),
            )
            for item in rows_payload
        ]
        return WorkbookSnapshot(sheet_name=sheet_name, headers=headers, rows=rows)


@dataclass(slots=True, frozen=True)
class XLWingsWorkbookSnapshotProvider:
    workbook_path: Path

    def load_snapshot(self) -> WorkbookSnapshot:
        xlwings_module = _load_xlwings_module()
        app = xlwings_module.App(visible=False, add_book=False)
        book = None
        try:
            book = app.books.open(str(self.workbook_path), update_links=False, read_only=True)
            return _build_snapshot_from_book(book)
        finally:
            if book is not None:
                book.close()
            app.quit()


def _parse_row_values(value: object) -> dict[int, str]:
    if not isinstance(value, dict):
        raise ValueError("Workbook row values must be an object keyed by column index")
    parsed: dict[int, str] = {}
    for key, item in value.items():
        try:
            column_index = int(key)
        except (TypeError, ValueError) as exc:
            raise ValueError("Workbook row values must use integer-like column keys") from exc
        parsed[column_index] = "" if item is None else str(item)
    return parsed


def _require_string(item: object, key: str, label: str) -> str:
    if not isinstance(item, dict):
        raise ValueError(f"Workbook {label} entries must be objects")
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Workbook {label} is missing non-empty '{key}'")
    return value


def _require_int(item: object, key: str, label: str) -> int:
    if not isinstance(item, dict):
        raise ValueError(f"Workbook {label} entries must be objects")
    value = item.get(key)
    if isinstance(value, int):
        return value
    raise ValueError(f"Workbook {label} is missing integer '{key}'")


def _load_xlwings_module():
    try:
        import xlwings  # type: ignore
    except ImportError as exc:
        raise ValueError("xlwings is required for live workbook inspection") from exc
    return xlwings


def _build_snapshot_from_book(book) -> WorkbookSnapshot:
    sheet = book.sheets[0]
    used_range = sheet.used_range
    last_row = int(used_range.last_cell.row)
    last_column = int(used_range.last_cell.column)
    header_values = sheet.range((2, 1), (2, last_column)).value or []
    if not isinstance(header_values, list):
        header_values = [header_values]
    headers = [
        WorkbookHeader(column_index=index, text=str(value).strip())
        for index, value in enumerate(header_values, start=1)
        if str(value or "").strip()
    ]
    per_cell_number_format_columns = {
        header.column_index
        for header in headers
        if header.text in _PER_CELL_NUMBER_FORMAT_HEADERS
    }

    rows: list[WorkbookRow] = []
    if last_row >= 3:
        body_range = sheet.range((3, 1), (last_row, last_column))
        body_matrix = _coerce_range_matrix(body_range.value, row_count=last_row - 2)
        number_format_matrix = _coerce_range_matrix(getattr(body_range, "number_format", None), row_count=last_row - 2)

        for row_offset, row_values in enumerate(body_matrix, start=3):
            values = {
                column_index: _stringify_cell(cell_value)
                for column_index, cell_value in enumerate(row_values, start=1)
            }
            number_formats = {
                column_index: _stringify_cell(number_format)
                for column_index, number_format in enumerate(
                    number_format_matrix[row_offset - 3] if row_offset - 3 < len(number_format_matrix) else [],
                    start=1,
                )
                if _stringify_cell(number_format)
            }
            for column_index in per_cell_number_format_columns:
                number_format = _stringify_cell(sheet.range((row_offset, column_index)).number_format)
                if number_format:
                    number_formats[column_index] = number_format
            rows.append(WorkbookRow(row_index=row_offset, values=values, number_formats=number_formats))

    return WorkbookSnapshot(sheet_name=sheet.name, headers=headers, rows=rows)


def _coerce_range_matrix(value: object, *, row_count: int) -> list[list[object]]:
    if value is None:
        return []
    if row_count == 1 and not isinstance(value, list):
        return [[value]]
    if isinstance(value, list) and value and not isinstance(value[0], list):
        return [value]
    return value or []


def _stringify_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
