from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from project.config import ResolvedWorkflowConfig


@dataclass(slots=True, frozen=True)
class ResolvedFolder:
    entry_id: str
    display_name: str | None


@dataclass(slots=True, frozen=True)
class FolderResolutionResult:
    source_working_folder: ResolvedFolder | None
    destination_success_folder: ResolvedFolder | None
    resolution_mode: str | None


class OutlookFolderGateway(Protocol):
    def resolve_configured_folders(
        self,
        *,
        config: ResolvedWorkflowConfig,
    ) -> FolderResolutionResult:
        """Resolve workflow folder identities for audit and later mail moves."""


@dataclass(slots=True, frozen=True)
class ConfiguredFolderGateway:
    def resolve_configured_folders(
        self,
        *,
        config: ResolvedWorkflowConfig,
    ) -> FolderResolutionResult:
        source_entry_id = str(config.values.get("source_working_folder_entry_id", "")).strip()
        destination_entry_id = str(config.values.get("destination_success_entry_id", "")).strip()
        source_display_name = _normalize_optional_string(
            config.values.get("source_working_folder_display_name")
        )
        destination_display_name = _normalize_optional_string(
            config.values.get("destination_success_display_name")
        )
        return FolderResolutionResult(
            source_working_folder=ResolvedFolder(source_entry_id, source_display_name)
            if source_entry_id
            else None,
            destination_success_folder=ResolvedFolder(
                destination_entry_id,
                destination_display_name,
            )
            if destination_entry_id
            else None,
            resolution_mode="entry_id" if source_entry_id or destination_entry_id else None,
        )


def _normalize_optional_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
