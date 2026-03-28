from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project.intake import EmptyMailSnapshotProvider, JsonManifestMailSnapshotProvider
from project.workflows.snapshot import build_email_snapshot, load_snapshot_manifest


class SnapshotTests(unittest.TestCase):
    def test_snapshot_provider_boundary_supports_empty_and_manifest_sources(self) -> None:
        empty_snapshot = EmptyMailSnapshotProvider().load_snapshot(state_timezone="Asia/Dhaka")
        self.assertEqual(empty_snapshot, [])

        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "snapshot.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-001",
                            "received_time": "2026-03-28T03:00:00Z",
                            "subject_raw": "Only mail",
                            "sender_address": "only@example.com",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            snapshot = JsonManifestMailSnapshotProvider(manifest_path).load_snapshot(
                state_timezone="Asia/Dhaka"
            )

        self.assertEqual(len(snapshot), 1)
        self.assertEqual(snapshot[0].entry_id, "entry-001")

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
