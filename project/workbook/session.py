from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from project.models import OperatorContext, WorkbookSessionPreflight
from project.utils.time import utc_timestamp
from project.workbook.models import WorkbookSnapshot
from project.workbook.providers import _build_snapshot_from_book, _load_xlwings_module

LOCK_CONFLICT_TOKENS = ("locked", "sharing violation", "already open", "in use")
READ_ONLY_TOKENS = ("read only", "read-only", "readonly")


@dataclass(slots=True, frozen=True)
class WorkbookWriteSessionResult:
    preflight: WorkbookSessionPreflight
    snapshot: WorkbookSnapshot | None
    discrepancy_code: str | None = None
    discrepancy_message: str | None = None
    discrepancy_details: dict[str, Any] = field(default_factory=dict)


class WorkbookWriteSessionProvider(Protocol):
    def open_preflight_session(
        self,
        *,
        operator_context: OperatorContext | None,
        max_attempts: int = 3,
    ) -> WorkbookWriteSessionResult:
        """Open a no-mutation write-intent session and return workbook evidence."""


@dataclass(slots=True, frozen=True)
class XLWingsWorkbookWriteSessionProvider:
    workbook_path: Path

    def open_preflight_session(
        self,
        *,
        operator_context: OperatorContext | None,
        max_attempts: int = 3,
    ) -> WorkbookWriteSessionResult:
        host_name, process_id = _resolve_operator_runtime(operator_context)
        if not os.access(self.workbook_path, os.W_OK):
            return WorkbookWriteSessionResult(
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
                snapshot=None,
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
                book = app.books.open(
                    str(self.workbook_path),
                    update_links=False,
                    read_only=False,
                )
                read_only = _book_is_read_only(book)
                if read_only:
                    return WorkbookWriteSessionResult(
                        preflight=WorkbookSessionPreflight(
                            workbook_path=str(self.workbook_path),
                            adapter_name="xlwings",
                            status="read_only_conflict",
                            attempt_count=attempt,
                            host_name=host_name,
                            process_id=process_id,
                            session_id=_build_session_id(),
                            opened_at_utc=utc_timestamp(),
                            read_only=True,
                            save_capable=False,
                        ),
                        snapshot=None,
                        discrepancy_code="workbook_open_readonly",
                        discrepancy_message="Workbook opened read-only when write intent was required.",
                        discrepancy_details={"workbook_path": str(self.workbook_path)},
                    )

                return WorkbookWriteSessionResult(
                    preflight=WorkbookSessionPreflight(
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
                        details={"capability_check": "opened_with_write_intent_without_mutation"},
                    ),
                    snapshot=_build_snapshot_from_book(book),
                )
            except Exception as exc:  # pragma: no cover - exercised through fakes/mocks
                message = str(exc).strip() or exc.__class__.__name__
                last_error = message
                normalized_message = message.lower()
                if any(token in normalized_message for token in READ_ONLY_TOKENS):
                    return WorkbookWriteSessionResult(
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
                        snapshot=None,
                        discrepancy_code="workbook_open_readonly",
                        discrepancy_message="Workbook could not be opened for write intent because it fell back to read-only mode.",
                        discrepancy_details={"workbook_path": str(self.workbook_path), "error": message},
                    )
                if any(token in normalized_message for token in LOCK_CONFLICT_TOKENS):
                    return WorkbookWriteSessionResult(
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
                        snapshot=None,
                        discrepancy_code="workbook_lock_conflict",
                        discrepancy_message="Workbook lock or conflicting open session prevented write-intent preflight.",
                        discrepancy_details={"workbook_path": str(self.workbook_path), "error": message},
                    )
            finally:
                if book is not None:
                    book.close()
                if app is not None:
                    app.quit()

        return WorkbookWriteSessionResult(
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
            snapshot=None,
            discrepancy_code="excel_adapter_unavailable",
            discrepancy_message="Excel adapter was unavailable after the retry envelope.",
            discrepancy_details={
                "workbook_path": str(self.workbook_path),
                "attempt_count": attempts,
                "last_error": last_error,
            },
        )


def _resolve_operator_runtime(operator_context: OperatorContext | None) -> tuple[str, int]:
    if operator_context is None:
        return (os.environ.get("COMPUTERNAME", "unknown-host"), os.getpid())
    return (operator_context.host_name, operator_context.process_id)


def _book_is_read_only(book) -> bool:
    api = getattr(book, "api", None)
    read_only = getattr(api, "ReadOnly", None)
    if isinstance(read_only, bool):
        return read_only
    book_read_only = getattr(book, "read_only", None)
    if isinstance(book_read_only, bool):
        return book_read_only
    return False


def _build_session_id() -> str:
    return f"excel-session-{uuid.uuid4().hex[:12]}"
