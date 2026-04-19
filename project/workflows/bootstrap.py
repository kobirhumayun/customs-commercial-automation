from __future__ import annotations

import getpass
import os
import socket
from dataclasses import dataclass
from datetime import datetime

from project import __version__
from project.config import ResolvedWorkflowConfig
from project.models import (
    EmailMessage,
    MailMovePhaseStatus,
    OperatorContext,
    PrintPhaseStatus,
    ProcessingJob,
    RunReport,
    WritePhaseStatus,
)
from project.outlook import ConfiguredFolderGateway, OutlookFolderGateway
from project.rules import LoadedRulePack
from project.storage import (
    RunArtifactPaths,
    copy_workbook_backup,
    create_run_artifact_layout,
    initialize_run_artifacts,
)
from project.utils.hashing import HASH_ALGORITHM, canonical_json_hash, sha256_file
from project.utils.ids import build_run_id
from project.utils.json import to_jsonable
from project.workflows.orchestrator import initialize_mail_outcomes
from project.utils.time import utc_timestamp, validate_timezone
from project.workflows.registry import WorkflowDescriptor
from project.workflows.runtime import WorkflowRuntimeState


@dataclass(slots=True, frozen=True)
class InitializedWorkflowRun:
    descriptor: WorkflowDescriptor
    config: ResolvedWorkflowConfig
    rule_pack: LoadedRulePack
    processing_job: ProcessingJob
    run_report: RunReport
    runtime_state: WorkflowRuntimeState
    artifact_paths: RunArtifactPaths
    master_workbook_path: str


def initialize_workflow_run(
    descriptor: WorkflowDescriptor,
    config: ResolvedWorkflowConfig,
    rule_pack: LoadedRulePack,
    mail_snapshot: list[EmailMessage] | None = None,
    folder_gateway: OutlookFolderGateway | None = None,
) -> InitializedWorkflowRun:
    workflow_timezone = validate_timezone(config.state_timezone)
    workflow_year = datetime.now(tz=workflow_timezone).year
    run_id = build_run_id(descriptor.workflow_id)
    snapshot = mail_snapshot or []
    operator_context = OperatorContext(
        operator_id=getpass.getuser(),
        username=getpass.getuser(),
        host_name=socket.gethostname(),
        process_id=os.getpid(),
    )
    resolved_folders = (folder_gateway or ConfiguredFolderGateway()).resolve_configured_folders(
        config=config
    )
    artifact_paths = create_run_artifact_layout(
        run_artifact_root=config.run_artifact_root,
        backup_root=config.backup_root,
        workflow_id=descriptor.workflow_id.value,
        run_id=run_id,
    )
    master_workbook_path = config.resolve_existing_master_workbook_path(workflow_year)
    current_workbook_hash = sha256_file(master_workbook_path)
    backup_hash = copy_workbook_backup(master_workbook_path, artifact_paths.backup_workbook_path)
    staged_write_plan_hash = canonical_json_hash([])
    started_at_utc = utc_timestamp()

    processing_job = ProcessingJob(
        run_id=run_id,
        workflow_id=descriptor.workflow_id,
        started_at_utc=started_at_utc,
        operator_id=operator_context.operator_id,
        mail_iteration_order=[mail.mail_id for mail in snapshot],
        hash_algorithm=HASH_ALGORITHM,
        run_start_backup_hash=backup_hash,
        staged_write_plan_hash=staged_write_plan_hash,
        write_phase_status=WritePhaseStatus.NOT_STARTED,
        print_phase_status=PrintPhaseStatus.NOT_STARTED,
        mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
    )
    run_report = RunReport(
        run_id=run_id,
        workflow_id=descriptor.workflow_id,
        tool_version=__version__,
        rule_pack_id=rule_pack.rule_pack_id,
        rule_pack_version=rule_pack.rule_pack_version,
        started_at_utc=started_at_utc,
        completed_at_utc=None,
        state_timezone=str(getattr(workflow_timezone, "key", config.state_timezone)),
        mail_iteration_order=[mail.mail_id for mail in snapshot],
        print_group_order=[],
        write_phase_status=WritePhaseStatus.NOT_STARTED,
        print_phase_status=PrintPhaseStatus.NOT_STARTED,
        mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
        hash_algorithm=HASH_ALGORITHM,
        run_start_backup_hash=backup_hash,
        current_workbook_hash=current_workbook_hash,
        staged_write_plan_hash=staged_write_plan_hash,
        summary={"pass": 0, "warning": 0, "hard_block": 0},
        operator_context=operator_context,
        mail_snapshot=snapshot,
        resolved_source_folder_entry_id=(
            resolved_folders.source_working_folder.entry_id
            if resolved_folders.source_working_folder is not None
            else None
        ),
        resolved_destination_folder_entry_id=(
            resolved_folders.destination_success_folder.entry_id
            if resolved_folders.destination_success_folder is not None
            else None
        ),
        folder_resolution_mode=resolved_folders.resolution_mode if descriptor.requires_mail_folders else None,
    )
    mail_outcomes = initialize_mail_outcomes(
        run_id=run_id,
        workflow_id=descriptor.workflow_id,
        mail_snapshot=snapshot,
    )
    runtime_state = WorkflowRuntimeState(mail_outcomes=mail_outcomes)
    initialize_run_artifacts(
        paths=artifact_paths,
        run_metadata=to_jsonable(run_report),
        mail_outcomes=to_jsonable(mail_outcomes),
    )
    return InitializedWorkflowRun(
        descriptor=descriptor,
        config=config,
        rule_pack=rule_pack,
        processing_job=processing_job,
        run_report=run_report,
        runtime_state=runtime_state,
        artifact_paths=artifact_paths,
        master_workbook_path=str(master_workbook_path),
    )
