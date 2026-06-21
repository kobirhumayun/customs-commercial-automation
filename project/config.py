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
    "import_document_root",
    "playwright_storage_state_path",
    "acrobat_executable_path",
}
BOOLEAN_KEYS = {"print_enabled", "playwright_headless"}
INTEGER_KEYS = {
    "excel_lock_timeout_seconds",
    "erp_download_timeout_seconds",
    "print_command_timeout_seconds",
    "bb_dashboard_timeout_seconds",
    "erp_report_default_lookback_days",
    "erp_report_default_from_input_index",
    "erp_report_default_to_input_index",
    "bb_dashboard_snapshot_settle_timeout_ms",
    "bb_dashboard_snapshot_stability_window_ms",
    "bb_dashboard_snapshot_poll_interval_ms",
}
OPTIONAL_CONFIG_DEFAULTS: dict[str, Any] = {
    "erp_lc_register_relative_url": "/RptCommercialExport/DateWiseLCRegisterForDocuments",
    "import_erp_pi_register_relative_url": "/RptExportPInLC/PIRegisterCustomsPDL",
    "playwright_headless": True,
    "erp_download_timeout_seconds": 120,
    "erp_report_submit_selector": 'role=button[name="Submit"]',
    "erp_report_post_submit_wait_selector": ".dx-menu-item-popout",
    "erp_report_download_menu_selector": ".dx-menu-item-popout",
    "erp_report_download_format_selector": '.dxrd-preview-export-item-text:text-is("CSV")',
    "erp_report_table_selector": "table",
    "erp_report_default_lookback_days": 365,
    "erp_report_default_from_input_selector": ".dx-texteditor-input",
    "erp_report_default_to_input_selector": ".dx-texteditor-input",
    "erp_report_default_from_input_index": 2,
    "erp_report_default_to_input_index": 3,
    "bb_dashboard_login_url": "https://exp.bb.org.bd/ords/oims/r/import/75",
    "bb_dashboard_timeout_seconds": 120,
    "bb_dashboard_back_link_text": "Back",
    "bb_dashboard_search_edit_link_text": "Inland BTB LC/Contract Search/Edit",
    "bb_dashboard_login_path_fragment": "/ords/oims/r/import/login",
    "bb_dashboard_reset_intermediate_url_pattern": "**/350?session=*",
    "bb_dashboard_reset_search_url_pattern": "**/75?clear=75**",
    "bb_dashboard_snapshot_settle_timeout_ms": 2_000,
    "bb_dashboard_snapshot_stability_window_ms": 200,
    "bb_dashboard_snapshot_poll_interval_ms": 100,
    "print_command_timeout_seconds": 120,
}
OPTIONAL_ENV_CONFIG_KEYS = tuple(
    sorted(
        {
            *OPTIONAL_CONFIG_DEFAULTS.keys(),
            "print_printer_name",
            "print_printer_driver",
            "print_printer_port",
            "playwright_storage_state_path",
            "acrobat_executable_path",
            "bb_dashboard_username",
            "bb_dashboard_password",
            "bb_dashboard_username_selector",
            "bb_dashboard_password_selector",
            "bb_dashboard_submit_selector",
            "bb_dashboard_post_login_wait_selector",
            "bb_dashboard_search_input_selector",
            "bb_dashboard_search_button_selector",
            "bb_dashboard_detail_ready_selector",
            "bb_dashboard_no_result_selector",
            "bb_dashboard_beneficiary_selector",
            "bb_dashboard_irc_selector",
            "bb_dashboard_erc_selector",
            "bb_dashboard_lc_date_selector",
            "bb_dashboard_last_shipment_selector",
            "bb_dashboard_expiry_date_selector",
            "bb_dashboard_lc_value_selector",
            "bb_dashboard_foreign_lc_selector",
            "bb_dashboard_quantity_selector",
            "import_amount_currency",
            "import_destination_success_entry_id",
            "import_document_root",
            "import_erp_base_url",
            "import_erp_pi_register_relative_url",
            "import_erp_username",
            "import_erp_password",
            "erp_username",
            "erp_password",
            "source_working_folder_display_name",
            "destination_success_display_name",
        }
    )
)


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

    def resolve_existing_master_workbook_path(self, year: int) -> Path:
        resolved = self.resolve_master_workbook_path(year)
        if not resolved.exists():
            raise ConfigError(
                "Expected master workbook for workflow year "
                f"{year} was not found: {resolved}. "
                "Place the real yearly workbook in the configured workbooks folder before running live workbook commands."
            )
        if not resolved.is_file():
            raise ConfigError(
                "Expected master workbook path for workflow year "
                f"{year} is not a file: {resolved}"
            )
        return resolved


def load_workflow_config(
    descriptor: WorkflowDescriptor,
    config_path: Path | None,
    overrides: dict[str, str] | None = None,
    environment: dict[str, str] | None = None,
) -> ResolvedWorkflowConfig:
    file_values = _load_config_file(config_path) if config_path else {}
    env_values = _load_environment_values(
        descriptor.all_required_config_keys + OPTIONAL_ENV_CONFIG_KEYS,
        environment,
    )
    raw_values = {
        **OPTIONAL_CONFIG_DEFAULTS,
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
    _validate_existing_directory(
        "report_root",
        Path(values["report_root"]),
        create_if_missing=True,
    )
    _validate_existing_directory(
        "run_artifact_root",
        Path(values["run_artifact_root"]),
        create_if_missing=True,
    )
    _validate_existing_directory(
        "backup_root",
        Path(values["backup_root"]),
        create_if_missing=True,
    )
    _validate_existing_directory("master_workbook_root", Path(values["master_workbook_root"]))

    timeout = int(values["excel_lock_timeout_seconds"])
    if timeout <= 0:
        raise ConfigError("excel_lock_timeout_seconds must be greater than zero")
    _validate_positive_int(
        values=values,
        key="erp_download_timeout_seconds",
    )
    _validate_positive_int(
        values=values,
        key="print_command_timeout_seconds",
    )
    _validate_positive_int(
        values=values,
        key="bb_dashboard_timeout_seconds",
    )
    _validate_non_negative_int(
        values=values,
        key="erp_report_default_lookback_days",
    )
    _validate_non_negative_int(
        values=values,
        key="erp_report_default_from_input_index",
    )
    _validate_non_negative_int(
        values=values,
        key="erp_report_default_to_input_index",
    )
    _validate_positive_int(
        values=values,
        key="bb_dashboard_snapshot_settle_timeout_ms",
    )
    _validate_positive_int(
        values=values,
        key="bb_dashboard_snapshot_stability_window_ms",
    )
    _validate_positive_int(
        values=values,
        key="bb_dashboard_snapshot_poll_interval_ms",
    )

    if descriptor.requires_mail_folders:
        for key in ("source_working_folder_entry_id", "destination_success_entry_id"):
            if not str(values.get(key, "")).strip():
                raise ConfigError(f"Configuration key '{key}' must be a non-empty string")


def _validate_existing_directory(
    key: str,
    path: Path,
    *,
    create_if_missing: bool = False,
) -> None:
    if create_if_missing and not path.exists():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(f"Configured path for '{key}' could not be created: {path}") from exc
    if not path.exists():
        raise ConfigError(f"Configured path for '{key}' does not exist: {path}")
    if not path.is_dir():
        raise ConfigError(f"Configured path for '{key}' is not a directory: {path}")
    if not os.access(path, os.W_OK):
        raise ConfigError(f"Configured path for '{key}' is not writable: {path}")


def _validate_positive_int(*, values: dict[str, Any], key: str) -> None:
    value = int(values[key])
    if value <= 0:
        raise ConfigError(f"{key} must be greater than zero")


def _validate_non_negative_int(*, values: dict[str, Any], key: str) -> None:
    value = int(values[key])
    if value < 0:
        raise ConfigError(f"{key} must be zero or greater")
