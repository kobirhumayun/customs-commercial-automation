from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project.workflows.print_marker_reporting import summarize_print_markers


class PrintMarkerReportingTests(unittest.TestCase):
    def test_summarize_print_markers_reads_receipt_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            marker_dir = Path(temp_dir) / "print_markers"
            marker_dir.mkdir(parents=True, exist_ok=True)
            (marker_dir / "group-1.json").write_text(
                """
                {
                  "print_group_id": "group-1",
                  "mail_id": "mail-1",
                  "completion_marker_id": "completion-1",
                  "printed_at_utc": "2026-03-30T00:00:00Z",
                  "manual_verification_summary": {"verified_count": 1},
                  "print_execution_receipt": {
                    "adapter_name": "acrobat",
                    "acknowledgment_mode": "process_exit_zero",
                    "executed_command_count": 2,
                    "blank_separator_printed": true
                  }
                }
                """,
                encoding="utf-8",
            )

            payload = summarize_print_markers(print_markers_dir=marker_dir)

        self.assertEqual(payload["marker_count"], 1)
        marker = payload["markers"][0]
        self.assertEqual(marker["print_group_id"], "group-1")
        self.assertEqual(marker["adapter_name"], "acrobat")
        self.assertEqual(marker["acknowledgment_mode"], "process_exit_zero")
        self.assertEqual(marker["executed_command_count"], 2)
        self.assertEqual(marker["blank_separator_printed"], True)


if __name__ == "__main__":
    unittest.main()
