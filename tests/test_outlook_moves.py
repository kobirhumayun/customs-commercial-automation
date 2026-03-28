from __future__ import annotations

import unittest
from unittest.mock import patch

from project.models import MailMoveOperation
from project.outlook import (
    MailMoveDestinationVerificationError,
    MailMoveSourceLocationError,
    Win32ComMailMoveProvider,
)


class OutlookMoveProviderTests(unittest.TestCase):
    def test_win32_provider_moves_mail_after_entry_id_and_source_folder_verification(self) -> None:
        moved_items: list[FakeMailItem] = []

        class FakeNamespace:
            def __init__(self) -> None:
                self.destination = FakeFolder("dst-folder")
                self.mail = FakeMailItem("entry-1", "src-folder", moved_items)

            def Logon(self, **_kwargs) -> None:
                return None

            def GetItemFromID(self, entry_id: str):
                self.requested_entry_id = entry_id
                return self.mail

            def GetFolderFromID(self, entry_id: str):
                self.requested_folder_id = entry_id
                return self.destination

        namespace = FakeNamespace()
        fake_client = FakeClient(namespace)

        with patch("project.outlook.moves._load_win32com_client_module", return_value=fake_client):
            provider = Win32ComMailMoveProvider(outlook_profile="Operations")
            provider.move_mail(_build_operation())

        self.assertEqual(namespace.requested_entry_id, "entry-1")
        self.assertEqual(namespace.requested_folder_id, "dst-folder")
        self.assertEqual(len(moved_items), 1)
        self.assertEqual(moved_items[0].Parent.EntryID, "dst-folder")

    def test_win32_provider_hard_fails_when_source_folder_does_not_match(self) -> None:
        class FakeNamespace:
            def Logon(self, **_kwargs) -> None:
                return None

            def GetItemFromID(self, _entry_id: str):
                return FakeMailItem("entry-1", "other-folder", [])

            def GetFolderFromID(self, _entry_id: str):
                raise AssertionError("Destination folder lookup should not run on source mismatch")

        fake_client = FakeClient(FakeNamespace())

        with patch("project.outlook.moves._load_win32com_client_module", return_value=fake_client):
            provider = Win32ComMailMoveProvider(outlook_profile="Operations")
            with self.assertRaises(MailMoveSourceLocationError):
                provider.move_mail(_build_operation())

    def test_win32_provider_verifies_destination_folder_after_move(self) -> None:
        class FakeNamespace:
            def __init__(self) -> None:
                self.destination = FakeFolder("dst-folder")

            def Logon(self, **_kwargs) -> None:
                return None

            def GetItemFromID(self, _entry_id: str):
                return FakeMailItem("entry-1", "src-folder", [], moved_parent_entry_id="wrong-folder")

            def GetFolderFromID(self, _entry_id: str):
                return self.destination

        fake_client = FakeClient(FakeNamespace())

        with patch("project.outlook.moves._load_win32com_client_module", return_value=fake_client):
            provider = Win32ComMailMoveProvider(outlook_profile="Operations")
            with self.assertRaises(MailMoveDestinationVerificationError):
                provider.move_mail(_build_operation())


class FakeClient:
    def __init__(self, namespace) -> None:
        self._namespace = namespace

    def Dispatch(self, app_name: str):
        self.app_name = app_name
        return FakeApplication(self._namespace)


class FakeApplication:
    def __init__(self, namespace) -> None:
        self._namespace = namespace

    def GetNamespace(self, namespace_name: str):
        if namespace_name != "MAPI":
            raise AssertionError(f"Unexpected namespace requested: {namespace_name}")
        return self._namespace


class FakeFolder:
    def __init__(self, entry_id: str) -> None:
        self.EntryID = entry_id


class FakeMailItem:
    def __init__(
        self,
        entry_id: str,
        parent_entry_id: str,
        moved_items: list["FakeMailItem"],
        *,
        moved_parent_entry_id: str | None = None,
    ) -> None:
        self.EntryID = entry_id
        self.Parent = FakeFolder(parent_entry_id)
        self._moved_items = moved_items
        self._moved_parent_entry_id = moved_parent_entry_id

    def Move(self, destination_folder: FakeFolder):
        moved = FakeMailItem(
            self.EntryID,
            self._moved_parent_entry_id or destination_folder.EntryID,
            [],
        )
        self._moved_items.append(moved)
        return moved


def _build_operation() -> MailMoveOperation:
    return MailMoveOperation(
        mail_move_operation_id="move-1",
        run_id="run-1",
        mail_id="mail-1",
        entry_id="entry-1",
        source_folder="src-folder",
        destination_folder="dst-folder",
        moved_at_utc=None,
        move_status="pending",
    )


if __name__ == "__main__":
    unittest.main()
