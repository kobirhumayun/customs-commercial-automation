from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project.models import PrintBatch
from project.printing import (
    AcrobatPrintProvider,
    inspect_acrobat_print_adapter,
    PrintAdapterUnavailableError,
)


class PrintingProviderTests(unittest.TestCase):
    def test_acrobat_print_provider_invokes_acrobat_for_documents_and_blank_separator(self) -> None:
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
            with patch("project.printing.providers.subprocess.run") as run_mock:
                run_mock.return_value = type(
                    "Completed",
                    (),
                    {"returncode": 0, "stdout": "", "stderr": ""},
                )()
                receipt = provider.print_group(batch, blank_page_after_group=True)

        self.assertEqual(run_mock.call_count, 2)
        first_command = run_mock.call_args_list[0].args[0]
        second_command = run_mock.call_args_list[1].args[0]
        self.assertEqual(first_command[0], str(acrobat_path))
        self.assertIn(str(document_path), first_command)
        self.assertEqual(first_command[-1], "Office Printer")
        self.assertTrue(str(second_command[6]).endswith("cca-blank-separator-page.pdf"))
        self.assertEqual(receipt.adapter_name, "acrobat")
        self.assertEqual(receipt.acknowledgment_mode, "process_exit_zero")
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
        self.assertEqual(payload["acknowledgment_mode"], "process_exit_zero")
        self.assertEqual(payload["resolved_executable_path"], str(acrobat_path))
        self.assertEqual(payload["printer_name"], "Office Printer")
        self.assertEqual(payload["timeout_seconds"], 45)
        self.assertEqual(payload["blank_separator_exists"], True)


if __name__ == "__main__":
    unittest.main()
