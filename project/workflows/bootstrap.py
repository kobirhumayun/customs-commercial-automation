from __future__ import annotations

import getpass
from dataclasses import dataclass
from datetime import datetime

from project import __version__
from project.config import ResolvedWorkflowConfig
from project.models import MailMovePhaseStatus, PrintPhaseStatus, ProcessingJob, RunReport, WritePhaseStatus
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
from project.utils.time import utc_timestamp, validate_timezone
from project.workflows.registry import WorkflowDescriptor


@dataclass(slots=True, frozen=True)
class InitializedWorkflowRun:
    descriptor: WorkflowDescriptor
    config: ResolvedWorkflowConfig
    rule_pack: LoadedRulePack
    processing_job: ProcessingJob
    run_report: RunReport
    artifact_paths: RunArtifactPaths
    master_workbook_path: str


def initialize_workflow_run(
    descriptor: WorkflowDescriptor,
    config: ResolvedWorkflowConfig,
    rule_pack: LoadedRulePack,
) -> InitializedWorkflowRun:
    workflow_timezone = validate_timezone(config.state_timezone)
    workflow_year = datetime.now(tz=workflow_timezone).year
    run_id = build_run_id(descriptor.workflow_id)
    artifact_paths = create_run_artifact_layout(
        run_artifact_root=config.run_artifact_root,
        backup_root=config.backup_root,
        workflow_id=descriptor.workflow_id.value,
        run_id=run_id,
    )
    master_workbook_path = config.resolve_master_workbook_path(workflow_year)
    current_workbook_hash = sha256_file(master_workbook_path)
    backup_hash = copy_workbook_backup(master_workbook_path, artifact_paths.backup_workbook_path)
    staged_write_plan_hash = canonical_json_hash([])
    started_at_utc = utc_timestamp()

    processing_job = ProcessingJob(
        run_id=run_id,
        workflow_id=descriptor.workflow_id,
        started_at_utc=started_at_utc,
        operator_id=getpass.getuser(),
        mail_iteration_order=[],
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
        mail_iteration_order=[],
        print_group_order=[],
        write_phase_status=WritePhaseStatus.NOT_STARTED,
        print_phase_status=PrintPhaseStatus.NOT_STARTED,
        mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
        hash_algorithm=HASH_ALGORITHM,
        run_start_backup_hash=backup_hash,
        current_workbook_hash=current_workbook_hash,
        staged_write_plan_hash=staged_write_plan_hash,
        summary={"pass": 0, "warning": 0, "hard_block": 0},
    )
    initialize_run_artifacts(
        paths=artifact_paths,
        run_metadata=to_jsonable(run_report),
    )
    return InitializedWorkflowRun(
        descriptor=descriptor,
        config=config,
        rule_pack=rule_pack,
        processing_job=processing_job,
        run_report=run_report,
        artifact_paths=artifact_paths,
        master_workbook_path=str(master_workbook_path),
    )
