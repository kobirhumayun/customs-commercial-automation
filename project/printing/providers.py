from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from project.models import PrintBatch
from project.utils.time import utc_timestamp


@dataclass(slots=True, frozen=True)
class PrintCommandReceipt:
    adapter_name: str
    document_path: str
    command: list[str]
    started_at_utc: str
    completed_at_utc: str
    elapsed_ms: int
    returncode: int | None
    stdout_excerpt: str | None
    stderr_excerpt: str | None
    acknowledgment_mode: str
    blank_separator: bool = False


@dataclass(slots=True, frozen=True)
class PrintGroupReceipt:
    adapter_name: str
    acknowledgment_mode: str
    executed_command_count: int
    blank_separator_printed: bool
    command_receipts: list[PrintCommandReceipt]


class PrintProvider(Protocol):
    def print_group(self, batch: PrintBatch, *, blank_page_after_group: bool) -> PrintGroupReceipt | None:
        """Print one deterministic mail-group payload."""


class PrintAdapterUnavailableError(RuntimeError):
    """Raised when the configured live print adapter is unavailable."""


class PartialPrintExecutionError(RuntimeError):
    """Raised when a print group only partially completed before failing."""

    def __init__(
        self,
        *,
        message: str,
        completed_command_receipts: list[PrintCommandReceipt],
        blank_separator_printed: bool,
    ) -> None:
        super().__init__(message)
        self.completed_command_receipts = list(completed_command_receipts)
        self.blank_separator_printed = blank_separator_printed


@dataclass(slots=True, frozen=True)
class SimulatedPrintProvider:
    def print_group(self, batch: PrintBatch, *, blank_page_after_group: bool) -> PrintGroupReceipt:
        del blank_page_after_group
        for document_path in batch.document_paths:
            if not Path(document_path).exists():
                raise FileNotFoundError(document_path)
        return PrintGroupReceipt(
            adapter_name="simulated",
            acknowledgment_mode="filesystem_exists",
            executed_command_count=len(batch.document_paths),
            blank_separator_printed=False,
            command_receipts=[],
        )


@dataclass(slots=True, frozen=True)
class AcrobatPrintProvider:
    acrobat_executable_path: Path | None = None
    printer_name: str | None = None
    printer_driver: str | None = None
    printer_port: str | None = None
    timeout_seconds: int = 120

    def print_group(self, batch: PrintBatch, *, blank_page_after_group: bool) -> PrintGroupReceipt:
        executable_path = _resolve_acrobat_executable(self.acrobat_executable_path)
        command_receipts: list[PrintCommandReceipt] = []
        try:
            for document_path in batch.document_paths:
                resolved_path = Path(document_path)
                if not resolved_path.exists():
                    raise FileNotFoundError(document_path)
                command_receipts.append(
                    _print_pdf_with_acrobat(
                        executable_path=executable_path,
                        document_path=resolved_path,
                        printer_name=self.printer_name,
                        printer_driver=self.printer_driver,
                        printer_port=self.printer_port,
                        timeout_seconds=self.timeout_seconds,
                    )
                )
            blank_separator_printed = False
            if blank_page_after_group:
                blank_page_path = _ensure_blank_separator_pdf()
                command_receipts.append(
                    _print_pdf_with_acrobat(
                        executable_path=executable_path,
                        document_path=blank_page_path,
                        printer_name=self.printer_name,
                        printer_driver=self.printer_driver,
                        printer_port=self.printer_port,
                        timeout_seconds=self.timeout_seconds,
                        blank_separator=True,
                    )
                )
                blank_separator_printed = True
        except Exception as exc:
            raise PartialPrintExecutionError(
                message=str(exc),
                completed_command_receipts=command_receipts,
                blank_separator_printed=(
                    bool(command_receipts) and bool(command_receipts[-1].blank_separator)
                ),
            ) from exc
        return PrintGroupReceipt(
            adapter_name="acrobat",
            acknowledgment_mode="process_exit_zero",
            executed_command_count=len(command_receipts),
            blank_separator_printed=blank_separator_printed,
            command_receipts=command_receipts,
        )


def inspect_acrobat_print_adapter(
    *,
    configured_executable_path: Path | None = None,
    printer_name: str | None = None,
    printer_driver: str | None = None,
    printer_port: str | None = None,
    timeout_seconds: int = 120,
) -> dict[str, object]:
    try:
        executable_path = _resolve_acrobat_executable(configured_executable_path)
        blank_separator_path = _ensure_blank_separator_pdf()
        return {
            "available": True,
            "adapter_name": "acrobat",
            "acknowledgment_mode": "process_exit_zero",
            "resolved_executable_path": str(executable_path),
            "printer_name": printer_name,
            "printer_driver": printer_driver,
            "printer_port": printer_port,
            "timeout_seconds": max(1, timeout_seconds),
            "blank_separator_path": str(blank_separator_path),
            "blank_separator_exists": blank_separator_path.exists(),
        }
    except PrintAdapterUnavailableError as exc:
        return {
            "available": False,
            "adapter_name": "acrobat",
            "acknowledgment_mode": "process_exit_zero",
            "resolved_executable_path": None,
            "printer_name": printer_name,
            "printer_driver": printer_driver,
            "printer_port": printer_port,
            "timeout_seconds": max(1, timeout_seconds),
            "blank_separator_path": None,
            "blank_separator_exists": False,
            "error": str(exc),
        }


def _resolve_acrobat_executable(configured_path: Path | None) -> Path:
    if configured_path is not None:
        resolved = Path(configured_path)
        if resolved.exists():
            return resolved
        raise PrintAdapterUnavailableError(f"Configured Acrobat executable path does not exist: {resolved}")

    candidates = _default_acrobat_candidates()
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise PrintAdapterUnavailableError(
        "No Acrobat desktop executable was found. Configure 'acrobat_executable_path' to enable live printing."
    )


def _default_acrobat_candidates() -> list[Path]:
    roots = [
        Path(os.environ.get("ProgramFiles", "")).expanduser(),
        Path(os.environ.get("ProgramFiles(x86)", "")).expanduser(),
    ]
    relative_candidates = (
        Path("Adobe/Acrobat DC/Acrobat/Acrobat.exe"),
        Path("Adobe/Acrobat 2020/Acrobat/Acrobat.exe"),
        Path("Adobe/Acrobat Reader DC/Reader/AcroRd32.exe"),
        Path("Adobe/Acrobat Reader/Reader/AcroRd32.exe"),
    )
    candidates: list[Path] = []
    for root in roots:
        if not str(root):
            continue
        for relative in relative_candidates:
            candidates.append(root / relative)
    return candidates


def _print_pdf_with_acrobat(
    *,
    executable_path: Path,
    document_path: Path,
    printer_name: str | None,
    printer_driver: str | None,
    printer_port: str | None,
    timeout_seconds: int,
    blank_separator: bool = False,
) -> PrintCommandReceipt:
    command = [
        str(executable_path),
        "/n",
        "/s",
        "/o",
        "/h",
        "/t",
        str(document_path),
    ]
    if printer_name:
        command.append(printer_name)
        if printer_driver:
            command.append(printer_driver)
            if printer_port:
                command.append(printer_port)
    started_monotonic = time.monotonic()
    started_at_utc = utc_timestamp()
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(1, timeout_seconds),
        )
    except FileNotFoundError as exc:
        raise PrintAdapterUnavailableError(f"Acrobat executable could not be started: {executable_path}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Acrobat print command timed out for {document_path}") from exc

    completed_at_utc = utc_timestamp()
    elapsed_ms = int((time.monotonic() - started_monotonic) * 1000)
    if completed.returncode not in (0, None):
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"exit_code={completed.returncode}"
        raise RuntimeError(f"Acrobat print command failed for {document_path}: {detail}")
    return PrintCommandReceipt(
        adapter_name="acrobat",
        document_path=str(document_path),
        command=list(command),
        started_at_utc=started_at_utc,
        completed_at_utc=completed_at_utc,
        elapsed_ms=max(0, elapsed_ms),
        returncode=completed.returncode,
        stdout_excerpt=_normalize_output_excerpt(completed.stdout),
        stderr_excerpt=_normalize_output_excerpt(completed.stderr),
        acknowledgment_mode="process_exit_zero",
        blank_separator=blank_separator,
    )


def _ensure_blank_separator_pdf() -> Path:
    blank_path = Path(tempfile.gettempdir()) / "cca-blank-separator-page.pdf"
    if blank_path.exists():
        return blank_path
    blank_path.write_bytes(_minimal_blank_pdf_bytes())
    return blank_path


def _minimal_blank_pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >> endobj\n"
        b"xref\n"
        b"0 4\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"trailer << /Size 4 /Root 1 0 R >>\n"
        b"startxref\n"
        b"186\n"
        b"%%EOF\n"
    )


def _normalize_output_excerpt(value: str | None, limit: int = 200) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    return text[:limit]
