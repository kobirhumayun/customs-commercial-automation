from __future__ import annotations

import unittest
from pathlib import Path

from project.storage import plan_attachment_saves
from project.workflows.snapshot import SourceAttachmentRecord, SourceEmailRecord, build_email_snapshot


class StoragePlanningTests(unittest.TestCase):
    def test_plan_attachment_saves_preserves_attachment_order_and_duplicate_decisions(self) -> None:
        mail = build_email_snapshot(
            [
                SourceEmailRecord(
                    entry_id="entry-1",
                    received_time="2026-03-28T03:00:00Z",
                    subject_raw="subject",
                    sender_address="sender@example.com",
                    attachments=[
                        SourceAttachmentRecord(attachment_name="PI.pdf"),
                        SourceAttachmentRecord(attachment_name="LC.pdf"),
                        SourceAttachmentRecord(attachment_name="PI.pdf"),
                    ],
                )
            ],
            state_timezone="Asia/Dhaka",
        )[0]

        plans = plan_attachment_saves(
            mail=mail,
            destination_directory=Path("C:/exports/all-attachments"),
            existing_filenames={"LC.pdf"},
        )

        self.assertEqual([plan.attachment_name for plan in plans], ["PI.pdf", "LC.pdf", "PI.pdf"])
        self.assertEqual(
            [plan.save_decision for plan in plans],
            ["planned_new", "planned_skip_duplicate_filename", "planned_skip_duplicate_filename"],
        )
        self.assertTrue(plans[0].destination_path.endswith("PI.pdf"))


if __name__ == "__main__":
    unittest.main()
