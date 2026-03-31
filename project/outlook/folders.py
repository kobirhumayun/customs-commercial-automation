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


@dataclass(slots=True, frozen=True)
class OutlookFolderRecord:
    entry_id: str
    display_name: str
    folder_path: str
    depth: int
    store_name: str | None
    parent_entry_id: str | None


class OutlookFolderGateway(Protocol):
    def resolve_configured_folders(
        self,
        *,
        config: ResolvedWorkflowConfig,
        ) -> FolderResolutionResult:
        """Resolve workflow folder identities for audit and later mail moves."""


class OutlookFolderCatalogProvider(Protocol):
    def list_folders(
        self,
        *,
        contains: str | None = None,
        max_depth: int | None = None,
    ) -> list[OutlookFolderRecord]:
        """Enumerate Outlook folders with EntryIDs for operator inspection."""


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


@dataclass(slots=True)
class Win32ComOutlookFolderCatalogProvider:
    outlook_profile: str | None = None
    _namespace: object | None = None

    def list_folders(
        self,
        *,
        contains: str | None = None,
        max_depth: int | None = None,
    ) -> list[OutlookFolderRecord]:
        normalized_contains = (contains or "").strip().lower()
        if max_depth is not None and max_depth < 0:
            raise ValueError("max_depth must be zero or greater.")

        namespace = self._get_namespace()
        try:
            root_folders = list(namespace.Folders)
        except Exception as exc:  # pragma: no cover - exercised through unit fakes
            raise ValueError(f"Outlook root folders could not be enumerated: {exc}") from exc

        results: list[OutlookFolderRecord] = []
        for folder in root_folders:
            store_name = _safe_string(getattr(folder, "Name", "")).strip() or None
            self._collect_folder_records(
                folder=folder,
                depth=0,
                path_parts=[store_name or "(unnamed store)"],
                store_name=store_name,
                parent_entry_id=None,
                contains=normalized_contains,
                max_depth=max_depth,
                results=results,
            )
        return results

    def _collect_folder_records(
        self,
        *,
        folder: object,
        depth: int,
        path_parts: list[str],
        store_name: str | None,
        parent_entry_id: str | None,
        contains: str,
        max_depth: int | None,
        results: list[OutlookFolderRecord],
    ) -> None:
        display_name = _safe_string(getattr(folder, "Name", "")).strip() or "(unnamed folder)"
        entry_id = _safe_string(getattr(folder, "EntryID", "")).strip()
        folder_path = " / ".join(path_parts)
        record = OutlookFolderRecord(
            entry_id=entry_id,
            display_name=display_name,
            folder_path=folder_path,
            depth=depth,
            store_name=store_name,
            parent_entry_id=parent_entry_id,
        )
        searchable = f"{record.display_name}\n{record.folder_path}\n{record.entry_id}".lower()
        if not contains or contains in searchable:
            results.append(record)

        if max_depth is not None and depth >= max_depth:
            return

        try:
            child_folders = list(getattr(folder, "Folders", []))
        except Exception as exc:  # pragma: no cover - exercised through unit fakes
            raise ValueError(f"Outlook child folders for {folder_path} could not be enumerated: {exc}") from exc

        for child in child_folders:
            child_name = _safe_string(getattr(child, "Name", "")).strip() or "(unnamed folder)"
            self._collect_folder_records(
                folder=child,
                depth=depth + 1,
                path_parts=[*path_parts, child_name],
                store_name=store_name,
                parent_entry_id=entry_id or None,
                contains=contains,
                max_depth=max_depth,
                results=results,
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
            raise ValueError(f"Outlook session initialization failed: {exc}") from exc
        self._namespace = namespace
        return namespace


def _normalize_optional_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _safe_string(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _load_win32com_client_module():
    try:
        from win32com import client  # type: ignore
    except ImportError as exc:
        raise ValueError("pywin32 is required for Outlook folder inspection") from exc
    return client
