from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from project.models import EmailMessage
from project.outlook.session import create_outlook_namespace
from project.utils.time import validate_timezone
from project.workflows.snapshot import (
    SourceAttachmentRecord,
    SourceEmailRecord,
    build_email_snapshot,
    load_snapshot_manifest,
)


class MailSnapshotProvider(Protocol):
    def load_snapshot(self, *, state_timezone: str) -> list[EmailMessage]:
        """Return a deterministic source-mail snapshot for the active run."""


@dataclass(slots=True, frozen=True)
class EmptyMailSnapshotProvider:
    def load_snapshot(self, *, state_timezone: str) -> list[EmailMessage]:
        del state_timezone
        return []


@dataclass(slots=True, frozen=True)
class JsonManifestMailSnapshotProvider:
    manifest_path: Path

    def load_snapshot(self, *, state_timezone: str) -> list[EmailMessage]:
        records = load_snapshot_manifest(self.manifest_path)
        return build_email_snapshot(records, state_timezone=state_timezone)


@dataclass(slots=True)
class Win32ComMailSnapshotProvider:
    source_folder_entry_id: str
    outlook_profile: str | None = None
    _namespace: object | None = field(default=None, init=False, repr=False)

    def load_snapshot(self, *, state_timezone: str) -> list[EmailMessage]:
        source_folder_entry_id = self.source_folder_entry_id.strip()
        if not source_folder_entry_id:
            raise ValueError("Live Outlook snapshot requires a non-empty source folder EntryID.")

        workflow_timezone = validate_timezone(state_timezone)
        folder = self._resolve_folder(source_folder_entry_id)
        try:
            items = list(folder.Items)
        except Exception as exc:  # pragma: no cover - exercised through unit fakes
            raise ValueError(f"Outlook source folder items could not be enumerated: {exc}") from exc

        records: list[SourceEmailRecord] = []
        for index, item in enumerate(items):
            entry_id = str(getattr(item, "EntryID", "")).strip()
            if not entry_id:
                raise ValueError(f"Outlook source folder item at index {index} is missing EntryID.")
            received_time = _serialize_received_time(getattr(item, "ReceivedTime", None), workflow_timezone)
            records.append(
                SourceEmailRecord(
                    entry_id=entry_id,
                    received_time=received_time,
                    subject_raw=_safe_string(getattr(item, "Subject", "")),
                    sender_address=_safe_string(getattr(item, "SenderEmailAddress", "")),
                    body_text=_safe_string(getattr(item, "Body", "")),
                    attachments=_load_attachment_records(getattr(item, "Attachments", None)),
                )
            )
        return build_email_snapshot(records, state_timezone=state_timezone)

    def _resolve_folder(self, entry_id: str):
        namespace = self._get_namespace()
        try:
            return namespace.GetFolderFromID(entry_id)
        except Exception as exc:  # pragma: no cover - exercised through unit fakes
            raise ValueError(f"Outlook source folder {entry_id} could not be resolved by EntryID.") from exc

    def _get_namespace(self):
        if self._namespace is not None:
            return self._namespace
        try:
            namespace = create_outlook_namespace(outlook_profile=self.outlook_profile)
        except Exception as exc:  # pragma: no cover - exercised through unit fakes
            raise ValueError(f"Outlook session initialization failed: {exc}") from exc
        self._namespace = namespace
        return namespace


def _serialize_received_time(value: object, workflow_timezone) -> str:
    if not isinstance(value, datetime):
        raise ValueError("Outlook mail item ReceivedTime is missing or not a datetime value.")
    if value.tzinfo is None:
        value = value.replace(tzinfo=workflow_timezone)
    return value.isoformat()


def _safe_string(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _load_attachment_records(value: object) -> list[SourceAttachmentRecord]:
    if value is None:
        return []

    count = getattr(value, "Count", None)
    if isinstance(count, int):
        items = [value.Item(index) for index in range(1, count + 1)]
    else:
        items = list(value)

    records: list[SourceAttachmentRecord] = []
    for item in items:
        attachment_name = _safe_string(getattr(item, "FileName", "")).strip()
        if not attachment_name:
            continue
        size_value = getattr(item, "Size", None)
        size_bytes = size_value if isinstance(size_value, int) and size_value >= 0 else None
        records.append(
            SourceAttachmentRecord(
                attachment_name=attachment_name,
                content_type=_safe_string(getattr(item, "MimeType", "")),
                size_bytes=size_bytes,
            )
        )
    return records
