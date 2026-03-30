from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project.exceptions import ConfigError
from project.models.enums import WorkflowId
from project.utils.time import validate_timezone
from project.workflows.registry import WorkflowDescriptor

ENV_PREFIX = "CCA_"
PATH_KEYS = {
    "report_root",
    "run_artifact_root",
    "backup_root",
    "master_workbook_root",
    "playwright_storage_state_path",
}
BOOLEAN_KEYS = {"print_enabled", "playwright_headless"}
INTEGER_KEYS = {"excel_lock_timeout_seconds", "erp_download_timeout_seconds"}


@dataclass(slots=True, frozen=True)
class ResolvedWorkflowConfig:
    workflow_id: WorkflowId
    values: dict[str, Any]

    @property
    def state_timezone(self) -> str:
        return str(self.values["state_timezone"])

    @property
    def report_root(self) -> Path:
        return Path(self.values["report_root"])

    @property
    def run_artifact_root(self) -> Path:
        return Path(self.values["run_artifact_root"])

    @property
    def backup_root(self) -> Path:
        return Path(self.values["backup_root"])

    @property
    def master_workbook_root(self) -> Path:
        return Path(self.values["master_workbook_root"])

    @property
    def print_enabled(self) -> bool:
        return bool(self.values["print_enabled"])

    def resolve_master_workbook_path(self, year: int) -> Path:
        template = str(self.values["master_workbook_path_template"])
        try:
            resolved = template.format(year=year, workflow_id=self.workflow_id.value)
        except KeyError as exc:
            raise ConfigError(
                "master_workbook_path_template contains an unsupported placeholder"
            ) from exc
        return Path(resolved)


def load_workflow_config(
    descriptor: WorkflowDescriptor,
    config_path: Path | None,
    overrides: dict[str, str] | None = None,
    environment: dict[str, str] | None = None,
) -> ResolvedWorkflowConfig:
    file_values = _load_config_file(config_path) if config_path else {}
    env_values = _load_environment_values(descriptor.all_required_config_keys, environment)
    raw_values = {
        **file_values,
        **env_values,
        **(overrides or {}),
    }
    raw_values["workflow_id"] = descriptor.workflow_id.value
    parsed_values = _normalize_values(raw_values)
    _validate_required_keys(descriptor, parsed_values)
    _validate_startup_contract(descriptor, parsed_values)
    return ResolvedWorkflowConfig(workflow_id=descriptor.workflow_id, values=parsed_values)


def _load_config_file(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise ConfigError(f"Configuration file does not exist: {config_path}")
    with config_path.open("rb") as handle:
        content = tomllib.load(handle)
    if not isinstance(content, dict):
        raise ConfigError("Configuration file must parse to a TOML table")
    return content


def _load_environment_values(
    required_keys: tuple[str, ...],
    environment: dict[str, str] | None,
) -> dict[str, str]:
    env = environment if environment is not None else os.environ
    values: dict[str, str] = {}
    for key in required_keys:
        env_key = f"{ENV_PREFIX}{key.upper()}"
        if env_key in env:
            values[key] = env[env_key]
    return values


def _normalize_values(values: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in values.items():
        if key in PATH_KEYS:
            normalized[key] = str(Path(str(value)))
        elif key in BOOLEAN_KEYS:
            normalized[key] = _parse_bool(key, value)
        elif key in INTEGER_KEYS:
            normalized[key] = _parse_int(key, value)
        else:
            normalized[key] = value
    return normalized


def _parse_bool(key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"Configuration key '{key}' must be a boolean value")


def _parse_int(key: str, value: Any) -> int:
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except ValueError as exc:
        raise ConfigError(f"Configuration key '{key}' must be an integer value") from exc


def _validate_required_keys(descriptor: WorkflowDescriptor, values: dict[str, Any]) -> None:
    missing = [key for key in descriptor.all_required_config_keys if key not in values]
    if missing:
        missing_keys = ", ".join(sorted(missing))
        raise ConfigError(f"Missing required configuration key(s): {missing_keys}")


def _validate_startup_contract(descriptor: WorkflowDescriptor, values: dict[str, Any]) -> None:
    validate_timezone(str(values["state_timezone"]))
    _validate_existing_directory("report_root", Path(values["report_root"]))
    _validate_existing_directory("run_artifact_root", Path(values["run_artifact_root"]))
    _validate_existing_directory("backup_root", Path(values["backup_root"]))
    _validate_existing_directory("master_workbook_root", Path(values["master_workbook_root"]))

    timeout = int(values["excel_lock_timeout_seconds"])
    if timeout <= 0:
        raise ConfigError("excel_lock_timeout_seconds must be greater than zero")

    if descriptor.requires_mail_folders:
        for key in ("source_working_folder_entry_id", "destination_success_entry_id"):
            if not str(values.get(key, "")).strip():
                raise ConfigError(f"Configuration key '{key}' must be a non-empty string")


def _validate_existing_directory(key: str, path: Path) -> None:
    if not path.exists():
        raise ConfigError(f"Configured path for '{key}' does not exist: {path}")
    if not path.is_dir():
        raise ConfigError(f"Configured path for '{key}' is not a directory: {path}")
    if not os.access(path, os.W_OK):
        raise ConfigError(f"Configured path for '{key}' is not writable: {path}")
