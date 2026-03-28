from __future__ import annotations

import json
from dataclasses import dataclass
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
