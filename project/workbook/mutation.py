from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from project.models import OperatorContext, WorkbookSessionPreflight
from project.utils.time import utc_timestamp
from project.workbook.models import WorkbookSnapshot
from project.workbook.providers import (
    _build_snapshot_from_book,
    _load_xlwings_module,
    _stringify_cell,
)
from project.workbook.session import (
    LOCK_CONFLICT_TOKENS,
    READ_ONLY_TOKENS,
    _book_is_read_only,
    _build_session_id,
    _resolve_operator_runtime,
)


@dataclass(slots=True, frozen=True)
class WorkbookMutationOpenResult:
    preflight: WorkbookSessionPreflight
    session: "WorkbookMutationSession" | None
    discrepancy_code: str | None = None
    discrepancy_message: str | None = None
    discrepancy_details: dict[str, Any] = field(default_factory=dict)


class WorkbookMutationSession(Protocol):
    preflight: WorkbookSessionPreflight

    def capture_snapshot(self) -> WorkbookSnapshot:
        """Capture the current workbook state without closing the session."""

    def write_cell(
        self,
        *,
        sheet_name: str,
        row_index: int,
        column_index: int,
        value: object,
        number_format: str | None = None,
    ) -> None:
        """Apply one cell mutation inside the active workbook session."""

    def read_cell(self, *, sheet_name: str, row_index: int, column_index: int) -> str | None:
        """Read one workbook cell from the active session."""

    def save(self) -> None:
        """Persist the active workbook session."""

    def close(self) -> None:
        """Release workbook and adapter resources."""


class WorkbookMutationSessionProvider(Protocol):
    def open_write_session(
        self,
        *,
        operator_context: OperatorContext | None,
        max_attempts: int = 3,
    ) -> WorkbookMutationOpenResult:
        """Open a workbook session capable of no-mutation preflight and later writes."""


@dataclass(slots=True)
class XLWingsWorkbookMutationSession:
    app: Any
    book: Any
    preflight: WorkbookSessionPreflight

    def capture_snapshot(self) -> WorkbookSnapshot:
        return _build_snapshot_from_book(self.book)

    def write_cell(
        self,
        *,
        sheet_name: str,
        row_index: int,
        column_index: int,
        value: object,
        number_format: str | None = None,
    ) -> None:
        sheet = _resolve_sheet(self.book, sheet_name)
        target_range = sheet.range((row_index, column_index))
        target_range.value = value
        if number_format is not None:
            target_range.number_format = number_format

    def read_cell(self, *, sheet_name: str, row_index: int, column_index: int) -> str | None:
        sheet = _resolve_sheet(self.book, sheet_name)
        value = sheet.range((row_index, column_index)).value
        return _stringify_cell(value)

    def save(self) -> None:
        self.book.save()

    def close(self) -> None:
        self.book.close()
        self.app.quit()


@dataclass(slots=True, frozen=True)
class XLWingsWorkbookMutationProvider:
    workbook_path: Path

    def open_write_session(
        self,
        *,
        operator_context: OperatorContext | None,
        max_attempts: int = 3,
    ) -> WorkbookMutationOpenResult:
        host_name, process_id = _resolve_operator_runtime(operator_context)
        if not os.access(self.workbook_path, os.W_OK):
            return WorkbookMutationOpenResult(
                preflight=WorkbookSessionPreflight(
                    workbook_path=str(self.workbook_path),
                    adapter_name="xlwings",
                    status="read_only_conflict",
                    attempt_count=1,
                    host_name=host_name,
                    process_id=process_id,
                    read_only=True,
                    save_capable=False,
                    details={"reason": "path_not_writable"},
                ),
                session=None,
                discrepancy_code="workbook_open_readonly",
                discrepancy_message="Workbook path is not writable by the current operator context.",
                discrepancy_details={"workbook_path": str(self.workbook_path)},
            )

        attempts = max(1, max_attempts)
        last_error: str | None = None
        for attempt in range(1, attempts + 1):
            app = None
            book = None
            try:
                xlwings_module = _load_xlwings_module()
                app = xlwings_module.App(visible=False, add_book=False)
                book = app.books.open(str(self.workbook_path), update_links=False, read_only=False)
                if _book_is_read_only(book):
                    if book is not None:
                        book.close()
                    if app is not None:
                        app.quit()
                    return WorkbookMutationOpenResult(
                        preflight=WorkbookSessionPreflight(
                            workbook_path=str(self.workbook_path),
                            adapter_name="xlwings",
                            status="read_only_conflict",
                            attempt_count=attempt,
                            host_name=host_name,
                            process_id=process_id,
                            session_id=_build_session_id(),
                            read_only=True,
                            save_capable=False,
                        ),
                        session=None,
                        discrepancy_code="workbook_open_readonly",
                        discrepancy_message="Workbook opened read-only when a write-capable session was required.",
                        discrepancy_details={"workbook_path": str(self.workbook_path)},
                    )

                preflight = WorkbookSessionPreflight(
                    workbook_path=str(self.workbook_path),
                    adapter_name="xlwings",
                    status="ready",
                    attempt_count=attempt,
                    host_name=host_name,
                    process_id=process_id,
                    session_id=_build_session_id(),
                    opened_at_utc=utc_timestamp(),
                    read_only=False,
                    save_capable=True,
                    details={"capability_check": "opened_with_write_intent"},
                )
                return WorkbookMutationOpenResult(
                    preflight=preflight,
                    session=XLWingsWorkbookMutationSession(app=app, book=book, preflight=preflight),
                )
            except Exception as exc:  # pragma: no cover - exercised via fakes/mocks
                message = str(exc).strip() or exc.__class__.__name__
                last_error = message
                normalized = message.lower()
                if any(token in normalized for token in READ_ONLY_TOKENS):
                    return WorkbookMutationOpenResult(
                        preflight=WorkbookSessionPreflight(
                            workbook_path=str(self.workbook_path),
                            adapter_name="xlwings",
                            status="read_only_conflict",
                            attempt_count=attempt,
                            host_name=host_name,
                            process_id=process_id,
                            read_only=True,
                            save_capable=False,
                            details={"error": message},
                        ),
                        session=None,
                        discrepancy_code="workbook_open_readonly",
                        discrepancy_message="Workbook fell back to read-only mode during write-session creation.",
                        discrepancy_details={"workbook_path": str(self.workbook_path), "error": message},
                    )
                if any(token in normalized for token in LOCK_CONFLICT_TOKENS):
                    return WorkbookMutationOpenResult(
                        preflight=WorkbookSessionPreflight(
                            workbook_path=str(self.workbook_path),
                            adapter_name="xlwings",
                            status="lock_conflict",
                            attempt_count=attempt,
                            host_name=host_name,
                            process_id=process_id,
                            read_only=None,
                            save_capable=False,
                            details={"error": message},
                        ),
                        session=None,
                        discrepancy_code="workbook_lock_conflict",
                        discrepancy_message="Workbook lock or conflicting open session prevented write-session creation.",
                        discrepancy_details={"workbook_path": str(self.workbook_path), "error": message},
                    )
                if book is not None:
                    book.close()
                if app is not None:
                    app.quit()

        return WorkbookMutationOpenResult(
            preflight=WorkbookSessionPreflight(
                workbook_path=str(self.workbook_path),
                adapter_name="xlwings",
                status="adapter_unavailable",
                attempt_count=attempts,
                host_name=host_name,
                process_id=process_id,
                read_only=None,
                save_capable=False,
                details={"last_error": last_error} if last_error else {},
            ),
            session=None,
            discrepancy_code="excel_adapter_unavailable",
            discrepancy_message="Excel adapter was unavailable after the retry envelope.",
            discrepancy_details={
                "workbook_path": str(self.workbook_path),
                "attempt_count": attempts,
                "last_error": last_error,
            },
        )


def _resolve_sheet(book, sheet_name: str):
    for sheet in book.sheets:
        if getattr(sheet, "name", None) == sheet_name:
            return sheet
    raise ValueError(f"Workbook sheet does not exist: {sheet_name}")
