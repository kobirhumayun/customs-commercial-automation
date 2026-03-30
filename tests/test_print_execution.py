from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project.models import (
    FinalDecision,
    MailMovePhaseStatus,
    MailOutcomeRecord,
    MailProcessingStatus,
    PrintBatch,
    PrintPhaseStatus,
    RunReport,
    WorkflowId,
    WritePhaseStatus,
)
from project.storage import create_run_artifact_layout
from project.workflows.print_execution import execute_print_batches, summarize_print_batch_manual_verification


class PrintExecutionTests(unittest.TestCase):
    def test_execute_print_batches_completes_and_writes_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-1",
            )
            document_path = root / "doc.pdf"
            document_path.write_text("fake pdf", encoding="utf-8")
            run_report = _build_run_report(print_phase_status=PrintPhaseStatus.PLANNED)
            mail_outcomes = [_build_mail_outcome(document_path=str(document_path))]
            print_batches = [
                PrintBatch(
                    print_group_id="group-1",
                    run_id="run-1",
                    mail_id="mail-1",
                    print_group_index=0,
                    document_paths=[str(document_path)],
                    document_path_hashes=["hash-1"],
                    completion_marker_id="completion-1",
                    manual_verification_summary={
                        "document_count": 1,
                        "verified_count": 1,
                        "pending_count": 0,
                        "untracked_count": 0,
                    },
                )
            ]
            phase_updates: list[str] = []

            executed_report, executed_outcomes, discrepancies = execute_print_batches(
                run_report=run_report,
                mail_outcomes=mail_outcomes,
                print_batches=print_batches,
                artifact_paths=artifact_paths,
                provider=FakePrintProvider(),
                run_report_persistor=lambda report: phase_updates.append(report.print_phase_status.value),
            )

            marker_path = artifact_paths.print_markers_dir / "group-1.json"
            marker_exists = marker_path.exists()
            marker_payload = (
                __import__("json").loads(marker_path.read_text(encoding="utf-8"))
                if marker_exists
                else {}
            )

        self.assertEqual(phase_updates, ["printing", "completed"])
        self.assertEqual(executed_report.print_phase_status, PrintPhaseStatus.COMPLETED)
        self.assertEqual(executed_outcomes[0].processing_status, MailProcessingStatus.PRINTED)
        self.assertIn("Manual PDF verification status at print time", executed_outcomes[0].decision_reasons[-1])
        self.assertTrue(marker_exists)
        self.assertEqual(marker_payload["manual_verification_summary"]["verified_count"], 1)
        self.assertEqual(discrepancies, [])

    def test_execute_print_batches_marks_uncertain_when_document_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-1",
            )
            missing_path = root / "missing.pdf"
            run_report = _build_run_report(print_phase_status=PrintPhaseStatus.PLANNED)
            mail_outcomes = [_build_mail_outcome(document_path=str(missing_path))]
            print_batches = [
                PrintBatch(
                    print_group_id="group-1",
                    run_id="run-1",
                    mail_id="mail-1",
                    print_group_index=0,
                    document_paths=[str(missing_path)],
                    document_path_hashes=["hash-1"],
                    completion_marker_id="completion-1",
                    manual_verification_summary={},
                )
            ]

            executed_report, executed_outcomes, discrepancies = execute_print_batches(
                run_report=run_report,
                mail_outcomes=mail_outcomes,
                print_batches=print_batches,
                artifact_paths=artifact_paths,
                provider=FakePrintProvider(),
            )

        self.assertEqual(executed_report.print_phase_status, PrintPhaseStatus.UNCERTAIN_INCOMPLETE)
        self.assertEqual(discrepancies[0].code, "print_source_document_missing")
        self.assertFalse(executed_outcomes[0].eligible_for_print)
        self.assertFalse(executed_outcomes[0].eligible_for_mail_move)

    def test_execute_print_batches_hard_blocks_marker_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-1",
            )
            document_path = root / "doc.pdf"
            document_path.write_text("fake pdf", encoding="utf-8")
            marker_path = artifact_paths.print_markers_dir / "group-1.json"
            marker_path.write_text(
                '{"completion_marker_id":"different","mail_id":"mail-1"}',
                encoding="utf-8",
            )
            run_report = _build_run_report(print_phase_status=PrintPhaseStatus.PLANNED)
            mail_outcomes = [_build_mail_outcome(document_path=str(document_path))]
            print_batches = [
                PrintBatch(
                    print_group_id="group-1",
                    run_id="run-1",
                    mail_id="mail-1",
                    print_group_index=0,
                    document_paths=[str(document_path)],
                    document_path_hashes=["hash-1"],
                    completion_marker_id="completion-1",
                    manual_verification_summary={},
                )
            ]

            executed_report, _executed_outcomes, discrepancies = execute_print_batches(
                run_report=run_report,
                mail_outcomes=mail_outcomes,
                print_batches=print_batches,
                artifact_paths=artifact_paths,
                provider=FakePrintProvider(),
            )

        self.assertEqual(executed_report.print_phase_status, PrintPhaseStatus.HARD_BLOCKED)
        self.assertEqual(discrepancies[0].code, "print_marker_mismatch")

    def test_summarize_print_batch_manual_verification_aggregates_counts(self) -> None:
        summary = summarize_print_batch_manual_verification(
            [
                PrintBatch(
                    print_group_id="group-1",
                    run_id="run-1",
                    mail_id="mail-1",
                    print_group_index=0,
                    document_paths=["C:/docs/a.pdf"],
                    document_path_hashes=["hash-a"],
                    completion_marker_id="completion-a",
                    manual_verification_summary={
                        "document_count": 1,
                        "verified_count": 1,
                        "pending_count": 0,
                        "untracked_count": 0,
                    },
                ),
                PrintBatch(
                    print_group_id="group-2",
                    run_id="run-1",
                    mail_id="mail-2",
                    print_group_index=1,
                    document_paths=["C:/docs/b.pdf", "C:/docs/c.pdf"],
                    document_path_hashes=["hash-b", "hash-c"],
                    completion_marker_id="completion-b",
                    manual_verification_summary={
                        "document_count": 2,
                        "verified_count": 1,
                        "pending_count": 1,
                        "untracked_count": 0,
                    },
                ),
            ]
        )

        self.assertEqual(summary["document_count"], 3)
        self.assertEqual(summary["verified_count"], 2)
        self.assertEqual(summary["pending_count"], 1)
        self.assertEqual(summary["untracked_count"], 0)


class FakePrintProvider:
    def print_group(self, batch: PrintBatch, *, blank_page_after_group: bool) -> None:
        for document_path in batch.document_paths:
            if not Path(document_path).exists():
                raise FileNotFoundError(document_path)


def _build_run_report(*, print_phase_status: PrintPhaseStatus) -> RunReport:
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
        mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
        hash_algorithm="sha256",
        run_start_backup_hash="a" * 64,
        current_workbook_hash="b" * 64,
        staged_write_plan_hash="c" * 64,
        summary={"pass": 1, "warning": 0, "hard_block": 0},
    )


def _build_mail_outcome(*, document_path: str) -> MailOutcomeRecord:
    return MailOutcomeRecord(
        run_id="run-1",
        mail_id="mail-1",
        workflow_id=WorkflowId.EXPORT_LC_SC,
        snapshot_index=0,
        processing_status=MailProcessingStatus.WRITTEN,
        final_decision=FinalDecision.PASS,
        decision_reasons=[],
        eligible_for_write=False,
        eligible_for_print=True,
        eligible_for_mail_move=True,
        source_entry_id="entry-1",
        subject_raw="subject",
        sender_address="a@example.com",
        print_group_id="group-1",
        saved_documents=[{"destination_path": document_path, "save_decision": "saved_new"}],
    )


if __name__ == "__main__":
    unittest.main()
