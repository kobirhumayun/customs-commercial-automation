from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from project.erp.models import ERPRegisterRow
from project.erp.normalization import normalize_buyer_name, normalize_lc_sc_date, normalize_lc_sc_number
from project.workflows.export_lc_sc.parsing import normalize_file_number


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
        indexed: dict[str, list[ERPRegisterRow]] = {file_number: [] for file_number in file_numbers}
        for row in manifest_rows:
            if row.file_number in indexed:
                indexed[row.file_number].append(row)
        for file_number, rows in indexed.items():
            rows.sort(key=lambda row: row.source_row_index)
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
        file_number = normalize_file_number(_require_string(item, "file_number", index))
        lc_sc_number = normalize_lc_sc_number(_require_string(item, "lc_sc_number", index))
        buyer_name = normalize_buyer_name(_require_string(item, "buyer_name", index))
        lc_sc_date = normalize_lc_sc_date(_require_string(item, "lc_sc_date", index))
        source_row_index = _require_int(item, "source_row_index", index)
        if file_number is None or lc_sc_number is None or buyer_name is None or lc_sc_date is None:
            raise ValueError(f"ERP row at index {index} contains an invalid canonical field")
        rows.append(
            ERPRegisterRow(
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
        )
    return rows


def _require_string(item: dict, key: str, index: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"ERP row at index {index} is missing non-empty '{key}'")
    return value


def _require_int(item: dict, key: str, index: int) -> int:
    value = item.get(key)
    if isinstance(value, int):
        return value
    raise ValueError(f"ERP row at index {index} is missing integer '{key}'")


def _optional_string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
