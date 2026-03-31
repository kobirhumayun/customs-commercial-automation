from __future__ import annotations

import unittest
from unittest.mock import patch

from project.outlook import Win32ComOutlookFolderCatalogProvider


class OutlookFolderCatalogTests(unittest.TestCase):
    def test_win32_provider_lists_folder_paths_and_entry_ids(self) -> None:
        class FakeFolder:
            def __init__(self, name: str, entry_id: str, children=None) -> None:
                self.Name = name
                self.EntryID = entry_id
                self.Folders = children or []

        class FakeNamespace:
            def __init__(self) -> None:
                self.Folders = [
                    FakeFolder(
                        "Mailbox - Operations",
                        "store-1",
                        [
                            FakeFolder(
                                "Inbox",
                                "inbox-1",
                                [
                                    FakeFolder("Export", "export-1", [FakeFolder("Working", "working-1")]),
                                ],
                            )
                        ],
                    )
                ]

            def Logon(self, **_kwargs) -> None:
                return None

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
        with patch("project.outlook.folders._load_win32com_client_module", return_value=FakeClient(namespace)):
            records = Win32ComOutlookFolderCatalogProvider(outlook_profile="Operations").list_folders()

        self.assertEqual(len(records), 4)
        self.assertEqual(records[0].folder_path, "Mailbox - Operations")
        self.assertEqual(records[-1].folder_path, "Mailbox - Operations / Inbox / Export / Working")
        self.assertEqual(records[-1].entry_id, "working-1")
        self.assertEqual(records[-1].parent_entry_id, "export-1")

    def test_win32_provider_supports_filter_and_depth_limit(self) -> None:
        class FakeFolder:
            def __init__(self, name: str, entry_id: str, children=None) -> None:
                self.Name = name
                self.EntryID = entry_id
                self.Folders = children or []

        class FakeNamespace:
            def __init__(self) -> None:
                self.Folders = [
                    FakeFolder(
                        "Mailbox - Operations",
                        "store-1",
                        [
                            FakeFolder("Inbox", "inbox-1", [FakeFolder("Export", "export-1")]),
                            FakeFolder("Archive", "archive-1"),
                        ],
                    )
                ]

        class FakeClient:
            def __init__(self, namespace) -> None:
                self.namespace = namespace

            def Dispatch(self, _app_name: str):
                return type(
                    "FakeApplication",
                    (),
                    {"GetNamespace": lambda _self, _namespace_name: self.namespace},
                )()

        namespace = FakeNamespace()
        with patch("project.outlook.folders._load_win32com_client_module", return_value=FakeClient(namespace)):
            filtered = Win32ComOutlookFolderCatalogProvider().list_folders(contains="export", max_depth=2)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].display_name, "Export")
        self.assertEqual(filtered[0].depth, 2)


if __name__ == "__main__":
    unittest.main()
