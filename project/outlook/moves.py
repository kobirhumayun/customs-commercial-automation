from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from project.models import MailMoveOperation


class MailMoveSourceLocationError(RuntimeError):
    """Raised when a mail is no longer in the expected source folder."""


class MailMoveDestinationVerificationError(RuntimeError):
    """Raised when a moved mail cannot be verified in the expected destination."""


class MailMoveAdapterUnavailableError(RuntimeError):
    """Raised when the live Outlook adapter cannot be initialized or queried."""


class MailMoveProvider(Protocol):
    def move_mail(self, operation: MailMoveOperation) -> None:
        """Move one mail according to the deterministic move operation."""


@dataclass(slots=True)
class SimulatedMailMoveProvider:
    current_folder_by_entry_id: dict[str, str] = field(default_factory=dict)

    def move_mail(self, operation: MailMoveOperation) -> None:
        current_folder = self.current_folder_by_entry_id.get(operation.entry_id)
        if current_folder is not None and current_folder != operation.source_folder:
            raise MailMoveSourceLocationError(
                f"Mail {operation.entry_id} expected in {operation.source_folder}, found {current_folder}."
            )
        self.current_folder_by_entry_id[operation.entry_id] = operation.destination_folder


@dataclass(slots=True)
class Win32ComMailMoveProvider:
    outlook_profile: str | None = None
    _namespace: object | None = field(default=None, init=False, repr=False)
    _destination_folder_cache: dict[str, object] = field(default_factory=dict, init=False, repr=False)

    def move_mail(self, operation: MailMoveOperation) -> None:
        namespace = self._get_namespace()
        mail_item = self._resolve_mail_item(namespace, operation.entry_id)
        current_folder_entry_id = _extract_parent_folder_entry_id(mail_item)
        if current_folder_entry_id != operation.source_folder:
            raise MailMoveSourceLocationError(
                f"Mail {operation.entry_id} expected in {operation.source_folder}, found {current_folder_entry_id}."
            )

        destination_folder = self._resolve_folder(namespace, operation.destination_folder)
        try:
            moved_item = mail_item.Move(destination_folder)
        except Exception as exc:  # pragma: no cover - exercised through unit fakes
            raise MailMoveAdapterUnavailableError(
                f"Outlook move operation failed for {operation.entry_id}: {exc}"
            ) from exc

        resolved_parent_entry_id = _extract_parent_folder_entry_id(moved_item)
        if resolved_parent_entry_id != operation.destination_folder:
            raise MailMoveDestinationVerificationError(
                "Mail move completed but destination folder verification did not match the planned destination."
            )

    def _get_namespace(self):
        if self._namespace is not None:
            return self._namespace
        win32_client = _load_win32com_client_module()
        try:
            application = win32_client.Dispatch("Outlook.Application")
            namespace = application.GetNamespace("MAPI")
            profile_name = (self.outlook_profile or "").strip()
            if profile_name:
                namespace.Logon(Profile=profile_name, ShowDialog=False, NewSession=False)
        except Exception as exc:  # pragma: no cover - exercised through unit fakes
            raise MailMoveAdapterUnavailableError(f"Outlook session initialization failed: {exc}") from exc
        self._namespace = namespace
        return namespace

    def _resolve_mail_item(self, namespace, entry_id: str):
        try:
            return namespace.GetItemFromID(entry_id)
        except Exception as exc:  # pragma: no cover - exercised through unit fakes
            raise MailMoveSourceLocationError(
                f"Mail {entry_id} could not be resolved by Outlook EntryID."
            ) from exc

    def _resolve_folder(self, namespace, entry_id: str):
        cached = self._destination_folder_cache.get(entry_id)
        if cached is not None:
            return cached
        try:
            folder = namespace.GetFolderFromID(entry_id)
        except Exception as exc:  # pragma: no cover - exercised through unit fakes
            raise MailMoveAdapterUnavailableError(
                f"Destination folder {entry_id} could not be resolved by Outlook EntryID."
            ) from exc
        self._destination_folder_cache[entry_id] = folder
        return folder


def _extract_parent_folder_entry_id(mail_item: object) -> str:
    parent = getattr(mail_item, "Parent", None)
    entry_id = getattr(parent, "EntryID", "")
    normalized = str(entry_id).strip()
    if not normalized:
        raise MailMoveAdapterUnavailableError("Outlook mail item parent folder EntryID is unavailable.")
    return normalized


def _load_win32com_client_module():
    try:
        from win32com import client  # type: ignore
    except ImportError as exc:
        raise MailMoveAdapterUnavailableError("pywin32 is required for live Outlook mail moves.") from exc
    return client
