from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from project.intake import EmptyMailSnapshotProvider, JsonManifestMailSnapshotProvider, Win32ComMailSnapshotProvider
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

    def test_live_outlook_snapshot_provider_reads_folder_and_preserves_deterministic_order(self) -> None:
        class FakeNamespace:
            def __init__(self) -> None:
                self.folder = type(
                    "FakeFolder",
                    (),
                    {
                        "Items": [
                            type(
                                "FakeMail",
                                (),
                                {
                                    "EntryID": "B",
                                    "ReceivedTime": datetime(2026, 3, 28, 9, 0, 0),
                                    "Subject": "Second",
                                    "SenderEmailAddress": "b@example.com",
                                    "Body": "body-b",
                                },
                            )(),
                            type(
                                "FakeMail",
                                (),
                                {
                                    "EntryID": "A",
                                    "ReceivedTime": datetime(2026, 3, 28, 9, 0, 0),
                                    "Subject": "First",
                                    "SenderEmailAddress": "a@example.com",
                                    "Body": "body-a",
                                },
                            )(),
                            type(
                                "FakeMail",
                                (),
                                {
                                    "EntryID": "C",
                                    "ReceivedTime": datetime(2026, 3, 28, 8, 59, 59),
                                    "Subject": "Earlier",
                                    "SenderEmailAddress": "c@example.com",
                                    "Body": "body-c",
                                },
                            )(),
                        ]
                    },
                )()

            def Logon(self, **_kwargs) -> None:
                return None

            def GetFolderFromID(self, entry_id: str):
                self.requested_folder_id = entry_id
                return self.folder

        class FakeClient:
            def __init__(self, namespace) -> None:
                self.namespace = namespace

            def Dispatch(self, app_name: str):
                self.app_name = app_name
                return type(
                    "FakeApplication",
                    (),
                    {"GetNamespace": lambda _self, namespace_name: self.namespace},
                )()

        namespace = FakeNamespace()

        with patch("project.intake.providers._load_win32com_client_module", return_value=FakeClient(namespace)):
            snapshot = Win32ComMailSnapshotProvider(
                source_folder_entry_id="src-folder",
                outlook_profile="Operations",
            ).load_snapshot(state_timezone="Asia/Dhaka")

        self.assertEqual(namespace.requested_folder_id, "src-folder")
        self.assertEqual([mail.entry_id for mail in snapshot], ["C", "A", "B"])
        self.assertEqual(snapshot[0].body_text, "body-c")
        self.assertEqual(snapshot[1].received_time_workflow_tz, "2026-03-28T09:00:00+06:00")


if __name__ == "__main__":
    unittest.main()
