from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from project.models import EmailMessage


class AttachmentContentProvider(Protocol):
    def save_attachment(
        self,
        *,
        mail: EmailMessage,
        attachment_index: int,
        destination_path: Path,
    ) -> None:
        """Persist the requested attachment bytes to the destination path."""


@dataclass(slots=True)
class SimulatedAttachmentContentProvider:
    content_by_key: dict[tuple[str, int], bytes] = field(default_factory=dict)

    def save_attachment(
        self,
        *,
        mail: EmailMessage,
        attachment_index: int,
        destination_path: Path,
    ) -> None:
        key = (mail.entry_id, attachment_index)
        if key not in self.content_by_key:
            raise ValueError(f"No simulated attachment bytes registered for {key}.")
        destination_path.write_bytes(self.content_by_key[key])


@dataclass(slots=True)
class Win32ComAttachmentContentProvider:
    outlook_profile: str | None = None
    _namespace: object | None = field(default=None, init=False, repr=False)

    def save_attachment(
        self,
        *,
        mail: EmailMessage,
        attachment_index: int,
        destination_path: Path,
    ) -> None:
        namespace = self._get_namespace()
        try:
            mail_item = namespace.GetItemFromID(mail.entry_id)
            attachments = mail_item.Attachments
            attachment = attachments.Item(attachment_index + 1)
            attachment.SaveAsFile(str(destination_path))
        except Exception as exc:  # pragma: no cover - exercised via unit fakes
            raise ValueError(
                f"Outlook attachment save failed for mail {mail.entry_id} attachment index {attachment_index}: {exc}"
            ) from exc

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
        except Exception as exc:  # pragma: no cover - exercised via unit fakes
            raise ValueError(f"Outlook session initialization failed: {exc}") from exc
        self._namespace = namespace
        return namespace


def _load_win32com_client_module():
    try:
        from win32com import client  # type: ignore
    except ImportError as exc:
        raise ValueError("pywin32 is required for live Outlook attachment saving") from exc
    return client
