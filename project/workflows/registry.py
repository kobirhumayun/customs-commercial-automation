from __future__ import annotations

from dataclasses import dataclass, field

from project.models.enums import WorkflowId


SHARED_REQUIRED_CONFIG_KEYS = (
    "state_timezone",
    "report_root",
    "run_artifact_root",
    "backup_root",
    "outlook_profile",
    "master_workbook_root",
    "erp_base_url",
    "playwright_browser_channel",
)

WRITE_REQUIRED_CONFIG_KEYS = (
    "master_workbook_path_template",
    "excel_lock_timeout_seconds",
    "print_enabled",
)


@dataclass(slots=True, frozen=True)
class WorkflowDescriptor:
    workflow_id: WorkflowId
    command_name: str
    description: str
    required_config_keys: tuple[str, ...] = field(default_factory=tuple)
    requires_mail_folders: bool = True
    supports_print: bool = True
    write_capable: bool = True

    @property
    def all_required_config_keys(self) -> tuple[str, ...]:
        required = list(SHARED_REQUIRED_CONFIG_KEYS)
        if self.write_capable:
            required.extend(WRITE_REQUIRED_CONFIG_KEYS)
        required.extend(self.required_config_keys)
        return tuple(dict.fromkeys(required))


WORKFLOW_REGISTRY = {
    WorkflowId.EXPORT_LC_SC: WorkflowDescriptor(
        workflow_id=WorkflowId.EXPORT_LC_SC,
        command_name="export-lc-sc",
        description="Initialize export LC/SC workflow run artifacts and startup validation.",
        required_config_keys=("source_working_folder_entry_id", "destination_success_entry_id"),
    ),
    WorkflowId.UD_IP_EXP: WorkflowDescriptor(
        workflow_id=WorkflowId.UD_IP_EXP,
        command_name="ud-ip-exp",
        description="Initialize UD/IP/EXP workflow run artifacts and startup validation.",
        required_config_keys=("source_working_folder_entry_id", "destination_success_entry_id"),
    ),
    WorkflowId.IMPORT_BTB_LC: WorkflowDescriptor(
        workflow_id=WorkflowId.IMPORT_BTB_LC,
        command_name="import-btb-lc",
        description="Initialize import/BTB LC workflow run artifacts and startup validation.",
        required_config_keys=("source_working_folder_entry_id", "destination_success_entry_id"),
    ),
    WorkflowId.BB_DASHBOARD_VERIFICATION: WorkflowDescriptor(
        workflow_id=WorkflowId.BB_DASHBOARD_VERIFICATION,
        command_name="bb-dashboard-verification",
        description="Initialize Bangladesh Bank dashboard verification workflow run artifacts and startup validation.",
        requires_mail_folders=False,
        supports_print=False,
    ),
}


def get_workflow_descriptor(workflow_id: WorkflowId) -> WorkflowDescriptor:
    return WORKFLOW_REGISTRY[workflow_id]
