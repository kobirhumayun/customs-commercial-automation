from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project.models import (
    FinalDecision,
    MailMovePhaseStatus,
    MailOutcomeRecord,
    MailProcessingStatus,
    PrintPhaseStatus,
    RunReport,
    WorkflowId,
    WritePhaseStatus,
)
from project.outlook import SimulatedMailMoveProvider
from project.storage import create_run_artifact_layout
from project.workflows.mail_moves import execute_mail_moves


class MailMoveExecutionTests(unittest.TestCase):
    def test_execute_mail_moves_completes_and_writes_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-1",
            )
            run_report = _build_run_report(
                print_phase_status=PrintPhaseStatus.COMPLETED,
                mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
            )
            mail_outcomes = [_build_mail_outcome()]
            phase_updates: list[str] = []

            executed_report, executed_outcomes, move_operations, discrepancies = execute_mail_moves(
                run_report=run_report,
                mail_outcomes=mail_outcomes,
                artifact_paths=artifact_paths,
                provider=SimulatedMailMoveProvider(),
                run_report_persistor=lambda report: phase_updates.append(report.mail_move_phase_status.value),
            )

            marker_path = artifact_paths.mail_move_markers_dir / f"{move_operations[0].mail_move_operation_id}.json"
            marker_exists = marker_path.exists()

        self.assertEqual(phase_updates, ["moving", "completed"])
        self.assertEqual(executed_report.mail_move_phase_status, MailMovePhaseStatus.COMPLETED)
        self.assertEqual(executed_outcomes[0].processing_status, MailProcessingStatus.MOVED)
        self.assertFalse(executed_outcomes[0].eligible_for_mail_move)
        self.assertTrue(marker_exists)
        self.assertEqual(discrepancies, [])

    def test_execute_mail_moves_hard_blocks_when_print_gate_is_unsatisfied(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-1",
            )
            run_report = _build_run_report(
                print_phase_status=PrintPhaseStatus.PLANNED,
                mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
            )

            executed_report, executed_outcomes, _move_operations, discrepancies = execute_mail_moves(
                run_report=run_report,
                mail_outcomes=[_build_mail_outcome()],
                artifact_paths=artifact_paths,
                provider=SimulatedMailMoveProvider(),
            )

        self.assertEqual(executed_report.mail_move_phase_status, MailMovePhaseStatus.HARD_BLOCKED)
        self.assertEqual(discrepancies[0].code, "mail_move_gate_unsatisfied")
        self.assertFalse(executed_outcomes[0].eligible_for_mail_move)

    def test_execute_mail_moves_hard_blocks_source_folder_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-1",
            )
            run_report = _build_run_report(
                print_phase_status=PrintPhaseStatus.COMPLETED,
                mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
            )

            executed_report, executed_outcomes, _move_operations, discrepancies = execute_mail_moves(
                run_report=run_report,
                mail_outcomes=[_build_mail_outcome()],
                artifact_paths=artifact_paths,
                provider=SimulatedMailMoveProvider(current_folder_by_entry_id={"entry-1": "other-folder"}),
            )

        self.assertEqual(executed_report.mail_move_phase_status, MailMovePhaseStatus.HARD_BLOCKED)
        self.assertEqual(discrepancies[0].code, "mail_source_location_mismatch")
        self.assertFalse(executed_outcomes[0].eligible_for_mail_move)


def _build_run_report(
    *,
    print_phase_status: PrintPhaseStatus,
    mail_move_phase_status: MailMovePhaseStatus,
) -> RunReport:
    return RunReport(
        run_id="run-1",
        workflow_id=WorkflowId.EXPORT_LC_SC,
        tool_version="0.1.0",
        rule_pack_id="export_lc_sc.default",
        rule_pack_version="1.0.0",
        started_at_utc="2026-03-28T00:00:00Z",
        completed_at_utc=None,
        state_timezone="Asia/Dhaka",
        mail_iteration_order=["mail-1"],
        print_group_order=["group-1"],
        write_phase_status=WritePhaseStatus.COMMITTED,
        print_phase_status=print_phase_status,
        mail_move_phase_status=mail_move_phase_status,
        hash_algorithm="sha256",
        run_start_backup_hash="a" * 64,
        current_workbook_hash="b" * 64,
        staged_write_plan_hash="c" * 64,
        summary={"pass": 1, "warning": 0, "hard_block": 0},
        resolved_source_folder_entry_id="src-folder",
        resolved_destination_folder_entry_id="dst-folder",
        folder_resolution_mode="entry_id",
    )


def _build_mail_outcome() -> MailOutcomeRecord:
    return MailOutcomeRecord(
        run_id="run-1",
        mail_id="mail-1",
        workflow_id=WorkflowId.EXPORT_LC_SC,
        snapshot_index=0,
        processing_status=MailProcessingStatus.PRINTED,
        final_decision=FinalDecision.PASS,
        decision_reasons=[],
        eligible_for_write=False,
        eligible_for_print=False,
        eligible_for_mail_move=True,
        source_entry_id="entry-1",
        subject_raw="subject",
        sender_address="a@example.com",
        print_group_id="group-1",
    )


if __name__ == "__main__":
    unittest.main()
