from __future__ import annotations

import os
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
            with _AcrobatOlePrintSession(
                executable_path=executable_path,
                printer_name=self.printer_name,
                printer_driver=self.printer_driver,
                printer_port=self.printer_port,
                timeout_seconds=self.timeout_seconds,
            ) as session:
                for document_path in batch.document_paths:
                    resolved_path = Path(document_path)
                    if not resolved_path.exists():
                        raise FileNotFoundError(document_path)
                    command_receipts.append(
                        session.submit_pdf(
                            document_path=resolved_path,
                            blank_separator=False,
                        )
                    )
                blank_separator_printed = False
                if blank_page_after_group:
                    blank_page_path = _ensure_blank_separator_pdf()
                    command_receipts.append(
                        session.submit_pdf(
                            document_path=blank_page_path,
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
            acknowledgment_mode="ole_silent_submission",
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
            "acknowledgment_mode": "ole_silent_submission",
            "resolved_executable_path": str(executable_path),
            "printer_name": printer_name,
            "printer_driver": printer_driver,
            "printer_port": printer_port,
            "printer_selection_mode": "jsobject_printer_name_or_default_printer_fallback",
            "primary_submission_mode": "ole_jsobject_submission",
            "fallback_submission_mode": "ole_avdoc_silent_submission",
            "supports_printer_specific_submission": bool(printer_name),
            "timeout_seconds": max(1, timeout_seconds),
            "blank_separator_path": str(blank_separator_path),
            "blank_separator_exists": blank_separator_path.exists(),
        }
    except PrintAdapterUnavailableError as exc:
        return {
            "available": False,
            "adapter_name": "acrobat",
            "acknowledgment_mode": "ole_silent_submission",
            "resolved_executable_path": None,
            "printer_name": printer_name,
            "printer_driver": printer_driver,
            "printer_port": printer_port,
            "printer_selection_mode": "jsobject_printer_name_or_default_printer_fallback",
            "primary_submission_mode": "ole_jsobject_submission",
            "fallback_submission_mode": "ole_avdoc_silent_submission",
            "supports_printer_specific_submission": bool(printer_name),
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


@dataclass(slots=True)
class _AcrobatOlePrintSession:
    executable_path: Path
    printer_name: str | None
    printer_driver: str | None
    printer_port: str | None
    timeout_seconds: int
    _app: object | None = None
    _pythoncom: object | None = None
    _client: object | None = None

    def __enter__(self) -> "_AcrobatOlePrintSession":
        self._pythoncom = _load_pythoncom_module()
        self._pythoncom.CoInitialize()
        try:
            self._client = _load_win32com_client_module()
            self._app = self._client.Dispatch("AcroExch.App")
        except Exception as exc:
            self._close()
            raise PrintAdapterUnavailableError(
                f"Acrobat OLE automation could not be initialized from {self.executable_path}: {exc}"
            ) from exc
        _hide_acrobat_application(self._app)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        self._close()

    def submit_pdf(self, *, document_path: Path, blank_separator: bool) -> PrintCommandReceipt:
        if self._client is None:
            raise PrintAdapterUnavailableError("Acrobat OLE automation session is not initialized.")

        started_monotonic = time.monotonic()
        started_at_utc = utc_timestamp()
        submission_mode = "ole_jsobject_submission"
        command = [
            "AcroExch.App",
            "AcroExch.PDDoc",
            "JSObject.print",
            str(document_path),
        ]
        if self.printer_name:
            command.append(f"printer={self.printer_name}")
        elif self.printer_driver or self.printer_port:
            command.append("printer=<default>")
        pd_doc = None
        av_doc = None
        try:
            pd_doc = self._client.Dispatch("AcroExch.PDDoc")
            if not bool(pd_doc.Open(str(document_path))):
                raise RuntimeError(f"Acrobat could not open {document_path}")
            _hide_acrobat_application(self._app)
            js_object = pd_doc.GetJSObject()
            if js_object is not None:
                try:
                    print_params = _build_jsobject_print_params(
                        js_object=js_object,
                        printer_name=self.printer_name,
                    )
                    _submit_print_via_jsobject(
                        js_object=js_object,
                        print_params=print_params,
                    )
                except RuntimeError:
                    if self.printer_name:
                        raise
                    submission_mode = "ole_avdoc_silent_submission"
                    command = [
                        "AcroExch.App",
                        "AcroExch.AVDoc",
                        "AVDoc.PrintPagesSilent",
                        str(document_path),
                        "printer=<default>",
                    ]
                    _safe_close_pd_doc(pd_doc)
                    pd_doc = None
                    av_doc = self._client.Dispatch("AcroExch.AVDoc")
                    if not bool(av_doc.Open(str(document_path), "")):
                        raise RuntimeError(f"Acrobat could not open {document_path} through AVDoc")
                    _hide_acrobat_application(self._app)
                    _submit_print_via_avdoc(av_doc=av_doc)
            else:
                if self.printer_name:
                    raise RuntimeError("Acrobat JSObject bridge is unavailable for printer-specific silent printing")
                submission_mode = "ole_avdoc_silent_submission"
                command = [
                    "AcroExch.App",
                    "AcroExch.AVDoc",
                    "AVDoc.PrintPagesSilent",
                    str(document_path),
                    "printer=<default>",
                ]
                _safe_close_pd_doc(pd_doc)
                pd_doc = None
                av_doc = self._client.Dispatch("AcroExch.AVDoc")
                if not bool(av_doc.Open(str(document_path), "")):
                    raise RuntimeError(f"Acrobat could not open {document_path} through AVDoc")
                _hide_acrobat_application(self._app)
                _submit_print_via_avdoc(av_doc=av_doc)
        except Exception as exc:
            raise RuntimeError(f"Acrobat silent print submission failed for {document_path}: {exc}") from exc
        finally:
            _safe_close_av_doc(av_doc)
            _safe_close_pd_doc(pd_doc)

        completed_at_utc = utc_timestamp()
        elapsed_ms = int((time.monotonic() - started_monotonic) * 1000)
        return PrintCommandReceipt(
            adapter_name="acrobat",
            document_path=str(document_path),
            command=command,
            started_at_utc=started_at_utc,
            completed_at_utc=completed_at_utc,
            elapsed_ms=max(0, elapsed_ms),
            returncode=None,
            stdout_excerpt=None,
            stderr_excerpt=None,
            acknowledgment_mode=submission_mode,
            blank_separator=blank_separator,
        )

    def _close(self) -> None:
        _safe_exit_acrobat_application(self._app)
        self._app = None
        if self._pythoncom is not None:
            try:
                self._pythoncom.CoUninitialize()
            except Exception:
                pass
        self._pythoncom = None
        self._client = None


def _build_jsobject_print_params(*, js_object, printer_name: str | None):
    try:
        print_params = js_object.getPrintParams()
    except Exception as exc:
        raise RuntimeError("Acrobat JSObject.getPrintParams() was unavailable") from exc

    silent_level = _resolve_jsobject_silent_interaction_level(print_params=print_params, js_object=js_object)
    if silent_level is None:
        raise RuntimeError("Acrobat JSObject silent interaction level could not be resolved")
    try:
        print_params.interactive = silent_level
    except Exception as exc:
        raise RuntimeError("Acrobat JSObject print params could not be set to silent mode") from exc

    if printer_name:
        try:
            print_params.printerName = printer_name
        except Exception as exc:
            raise RuntimeError(f"Acrobat JSObject could not target printer '{printer_name}'") from exc

    return print_params


def _resolve_jsobject_silent_interaction_level(*, print_params, js_object) -> object | None:
    constants_candidates = [
        getattr(print_params, "constants", None),
        getattr(js_object, "constants", None),
    ]
    for constants in constants_candidates:
        interaction_level = getattr(constants, "interactionLevel", None)
        if interaction_level is None:
            continue
        silent = getattr(interaction_level, "silent", None)
        if silent is not None:
            return silent
    return None


def _submit_print_via_jsobject(*, js_object, print_params) -> None:
    method_names = ("print", "printWithParams")
    last_error: Exception | None = None
    for method_name in method_names:
        method = getattr(js_object, method_name, None)
        if method is None:
            continue
        try:
            method(print_params)
            return
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise RuntimeError(f"Acrobat JSObject print invocation failed: {last_error}") from last_error
    raise RuntimeError("Acrobat JSObject print method was unavailable")


def _submit_print_via_avdoc(*, av_doc) -> None:
    pd_doc = av_doc.GetPDDoc()
    if pd_doc is None:
        raise RuntimeError("Acrobat AVDoc.GetPDDoc() was unavailable")
    try:
        page_count = int(pd_doc.GetNumPages())
    except Exception as exc:
        raise RuntimeError("Acrobat PDDoc.GetNumPages() failed for AVDoc print submission") from exc
    if page_count < 1:
        raise RuntimeError("Acrobat AVDoc print submission requires at least one page")
    try:
        result = av_doc.PrintPagesSilent(
            0,
            page_count - 1,
            2,
            1,
            1,
        )
    except Exception as exc:
        raise RuntimeError(f"Acrobat AVDoc.PrintPagesSilent() failed: {exc}") from exc
    if not bool(result):
        raise RuntimeError("Acrobat AVDoc.PrintPagesSilent() reported failure")


def _hide_acrobat_application(app: object | None) -> None:
    if app is None:
        return
    try:
        app.Hide()
    except Exception:
        pass


def _safe_close_pd_doc(pd_doc: object | None) -> None:
    if pd_doc is None:
        return
    try:
        pd_doc.Close()
    except Exception:
        pass


def _safe_close_av_doc(av_doc: object | None) -> None:
    if av_doc is None:
        return
    try:
        av_doc.Close(1)
    except Exception:
        try:
            av_doc.Close()
        except Exception:
            pass


def _safe_exit_acrobat_application(app: object | None) -> None:
    if app is None:
        return
    try:
        app.CloseAllDocs()
    except Exception:
        pass
    try:
        app.Exit()
    except Exception:
        pass


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


def _load_win32com_client_module():
    try:
        from win32com import client  # type: ignore
    except ImportError as exc:
        raise PrintAdapterUnavailableError("pywin32 is required for live Acrobat OLE printing.") from exc
    return client


def _load_pythoncom_module():
    try:
        import pythoncom  # type: ignore
    except ImportError as exc:
        raise PrintAdapterUnavailableError("pywin32 pythoncom is required for live Acrobat OLE printing.") from exc
    return pythoncom
