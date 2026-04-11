from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from project.models import PrintBatch
from project.printing import (
    AcrobatPrintProvider,
    inspect_acrobat_print_adapter,
    PrintAdapterUnavailableError,
)


class PrintingProviderTests(unittest.TestCase):
    def test_acrobat_print_provider_submits_documents_silently_via_ole_and_jsobject(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            acrobat_path = root / "Acrobat.exe"
            acrobat_path.write_text("fake", encoding="utf-8")
            document_path = root / "doc.pdf"
            document_path.write_text("fake pdf", encoding="utf-8")
            batch = PrintBatch(
                print_group_id="group-1",
                run_id="run-1",
                mail_id="mail-1",
                print_group_index=0,
                document_paths=[str(document_path)],
                document_path_hashes=["hash-1"],
                completion_marker_id="completion-1",
                manual_verification_summary={},
            )

            provider = AcrobatPrintProvider(
                acrobat_executable_path=acrobat_path,
                printer_name="Office Printer",
                timeout_seconds=30,
            )
            ole_client = _FakeWin32Client()
            pythoncom = _FakePythonCom()
            with patch("project.printing.providers._load_win32com_client_module", return_value=ole_client), patch(
                "project.printing.providers._load_pythoncom_module",
                return_value=pythoncom,
            ):
                receipt = provider.print_group(batch, blank_page_after_group=True)

        self.assertEqual(pythoncom.init_count, 1)
        self.assertEqual(pythoncom.uninit_count, 1)
        self.assertEqual(ole_client.app.hide_count, 3)
        self.assertTrue(ole_client.app.exited)
        self.assertEqual(len(ole_client.print_calls), 2)
        first_call, second_call = ole_client.print_calls
        self.assertEqual(first_call["document_path"], str(document_path))
        self.assertEqual(first_call["printer_name"], "Office Printer")
        self.assertTrue(second_call["document_path"].endswith("cca-blank-separator-page.pdf"))
        self.assertEqual(receipt.command_receipts[0].command[:3], ["AcroExch.App", "AcroExch.PDDoc", "JSObject.print"])
        self.assertEqual(receipt.adapter_name, "acrobat")
        self.assertEqual(receipt.acknowledgment_mode, "ole_jsobject_submission")
        self.assertEqual(receipt.executed_command_count, 2)
        self.assertTrue(receipt.blank_separator_printed)
        self.assertEqual(receipt.command_receipts[0].document_path, str(document_path))
        self.assertFalse(receipt.command_receipts[0].blank_separator)
        self.assertTrue(receipt.command_receipts[1].blank_separator)

    def test_acrobat_print_provider_raises_when_executable_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            document_path = root / "doc.pdf"
            document_path.write_text("fake pdf", encoding="utf-8")
            batch = PrintBatch(
                print_group_id="group-1",
                run_id="run-1",
                mail_id="mail-1",
                print_group_index=0,
                document_paths=[str(document_path)],
                document_path_hashes=["hash-1"],
                completion_marker_id="completion-1",
                manual_verification_summary={},
            )

            provider = AcrobatPrintProvider(
                acrobat_executable_path=root / "missing.exe",
            )
            with self.assertRaises(PrintAdapterUnavailableError):
                provider.print_group(batch, blank_page_after_group=False)

    def test_inspect_acrobat_print_adapter_reports_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            acrobat_path = root / "Acrobat.exe"
            acrobat_path.write_text("fake", encoding="utf-8")

            payload = inspect_acrobat_print_adapter(
                configured_executable_path=acrobat_path,
                printer_name="Office Printer",
                timeout_seconds=45,
            )

        self.assertEqual(payload["available"], True)
        self.assertEqual(payload["acknowledgment_mode"], "ole_jsobject_submission")
        self.assertEqual(payload["resolved_executable_path"], str(acrobat_path))
        self.assertEqual(payload["printer_name"], "Office Printer")
        self.assertEqual(payload["printer_selection_mode"], "jsobject_printer_name_or_default")
        self.assertEqual(payload["timeout_seconds"], 45)
        self.assertEqual(payload["blank_separator_exists"], True)


class _FakePythonCom:
    def __init__(self) -> None:
        self.init_count = 0
        self.uninit_count = 0

    def CoInitialize(self) -> None:
        self.init_count += 1

    def CoUninitialize(self) -> None:
        self.uninit_count += 1


class _FakeWin32Client:
    def __init__(self) -> None:
        self.app = _FakeAcrobatApp()
        self.print_calls: list[dict[str, str | None]] = []

    def Dispatch(self, prog_id: str):
        if prog_id == "AcroExch.App":
            return self.app
        if prog_id == "AcroExch.PDDoc":
            return _FakePDDoc(print_calls=self.print_calls)
        raise AssertionError(f"Unexpected prog id: {prog_id}")


class _FakeAcrobatApp:
    def __init__(self) -> None:
        self.hide_count = 0
        self.exited = False

    def Hide(self) -> None:
        self.hide_count += 1

    def CloseAllDocs(self) -> None:
        return None

    def Exit(self) -> None:
        self.exited = True


class _FakePDDoc:
    def __init__(self, *, print_calls: list[dict[str, str | None]]) -> None:
        self._print_calls = print_calls
        self._document_path: str | None = None

    def Open(self, document_path: str) -> bool:
        self._document_path = document_path
        return True

    def GetJSObject(self):
        if self._document_path is None:
            raise AssertionError("Document path should be set before GetJSObject")
        return _FakeJSObject(
            document_path=self._document_path,
            print_calls=self._print_calls,
        )

    def Close(self) -> None:
        return None


class _FakeJSObject:
    def __init__(self, *, document_path: str, print_calls: list[dict[str, str | None]]) -> None:
        self._document_path = document_path
        self._print_calls = print_calls
        self.constants = SimpleNamespace(
            interactionLevel=SimpleNamespace(silent="silent"),
        )

    def getPrintParams(self):
        return SimpleNamespace(
            constants=self.constants,
            interactive=None,
            printerName=None,
        )

    def print(self, print_params) -> None:
        self._print_calls.append(
            {
                "document_path": self._document_path,
                "printer_name": getattr(print_params, "printerName", None),
            }
        )


if __name__ == "__main__":
    unittest.main()
