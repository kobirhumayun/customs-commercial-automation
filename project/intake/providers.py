from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from project.models import EmailMessage
from project.workflows.snapshot import build_email_snapshot, load_snapshot_manifest


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
