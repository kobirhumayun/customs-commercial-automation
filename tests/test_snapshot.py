from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project.workflows.snapshot import build_email_snapshot, load_snapshot_manifest


class SnapshotTests(unittest.TestCase):
    def test_build_email_snapshot_orders_by_received_time_then_entry_id(self) -> None:
        source_messages = [
            {
                "entry_id": "B",
                "received_time": "2026-03-28T03:00:00Z",
                "subject_raw": "Second",
                "sender_address": "b@example.com",
            },
            {
                "entry_id": "A",
                "received_time": "2026-03-28T03:00:00Z",
                "subject_raw": "First",
                "sender_address": "a@example.com",
            },
            {
                "entry_id": "C",
                "received_time": "2026-03-28T02:59:59Z",
                "subject_raw": "Earlier",
                "sender_address": "c@example.com",
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "snapshot.json"
            manifest_path.write_text(json.dumps(source_messages), encoding="utf-8")
            manifest = load_snapshot_manifest(manifest_path)

        snapshot = build_email_snapshot(manifest, state_timezone="Asia/Dhaka")

        self.assertEqual([mail.entry_id for mail in snapshot], ["C", "A", "B"])
        self.assertEqual([mail.snapshot_index for mail in snapshot], [0, 1, 2])
        self.assertEqual(snapshot[0].received_time_workflow_tz, "2026-03-28T08:59:59+06:00")


if __name__ == "__main__":
    unittest.main()
