from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from project.workbook.models import WorkbookHeader, WorkbookRow, WorkbookSnapshot


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

            rows: list[WorkbookRow] = []
            if last_row >= 3:
                body_values = sheet.range((3, 1), (last_row, last_column)).value
                if body_values is None:
                    body_matrix: list[list[object]] = []
                elif last_row == 3 and not isinstance(body_values, list):
                    body_matrix = [[body_values]]
                elif body_values and isinstance(body_values, list) and body_values and not isinstance(body_values[0], list):
                    body_matrix = [body_values]
                else:
                    body_matrix = body_values or []

                for row_offset, row_values in enumerate(body_matrix, start=3):
                    values = {
                        column_index: _stringify_cell(cell_value)
                        for column_index, cell_value in enumerate(row_values, start=1)
                    }
                    rows.append(WorkbookRow(row_index=row_offset, values=values))

            return WorkbookSnapshot(sheet_name=sheet.name, headers=headers, rows=rows)
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


def _stringify_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
