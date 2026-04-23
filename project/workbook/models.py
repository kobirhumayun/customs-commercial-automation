from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class WorkbookHeader:
    column_index: int
    text: str


@dataclass(slots=True, frozen=True)
class WorkbookRow:
    row_index: int
    values: dict[int, str]
    number_formats: dict[int, str] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class WorkbookSnapshot:
    sheet_name: str
    headers: list[WorkbookHeader]
    rows: list[WorkbookRow]
