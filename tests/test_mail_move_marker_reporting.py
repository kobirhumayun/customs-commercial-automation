from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project.workflows.mail_move_marker_reporting import summarize_mail_move_markers


class MailMoveMarkerReportingTests(unittest.TestCase):
    def test_summarize_mail_move_markers_reads_receipt_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            marker_dir = Path(temp_dir) / "mail_move_markers"
            marker_dir.mkdir(parents=True, exist_ok=True)
            (marker_dir / "move-1.json").write_text(
                """
                {
                  "mail_move_operation_id": "move-1",
                  "mail_id": "mail-1",
                  "entry_id": "entry-1",
                  "source_folder": "src-folder",
                  "destination_folder": "dst-folder",
                  "move_status": "moved",
                  "moved_at_utc": "2026-03-30T00:00:00Z",
                  "manual_verification_summary": {"verified_count": 1},
                  "move_execution_receipt": {
                    "adapter_name": "win32com_outlook",
                    "acknowledgment_mode": "parent_folder_entry_id_verification",
                    "acknowledged_source_folder": "src-folder",
                    "acknowledged_destination_folder": "dst-folder"
                  }
                }
                """,
                encoding="utf-8",
            )

            payload = summarize_mail_move_markers(mail_move_markers_dir=marker_dir)

        self.assertEqual(payload["marker_count"], 1)
        marker = payload["markers"][0]
        self.assertEqual(marker["mail_move_operation_id"], "move-1")
        self.assertEqual(marker["adapter_name"], "win32com_outlook")
        self.assertEqual(marker["acknowledgment_mode"], "parent_folder_entry_id_verification")
        self.assertEqual(marker["acknowledged_source_folder"], "src-folder")
        self.assertEqual(marker["acknowledged_destination_folder"], "dst-folder")


if __name__ == "__main__":
    unittest.main()
