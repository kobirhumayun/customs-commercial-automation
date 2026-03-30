from __future__ import annotations

import json
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
    WriteOperation,
    WritePhaseStatus,
)
from project.storage import create_run_artifact_layout
from project.workflows.run_reporting import summarize_run_status


class RunReportingTests(unittest.TestCase):
    def test_summarize_run_status_reports_phase_and_artifact_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-123",
            )
            paths.manual_document_verification_path.write_text(
                json.dumps(
                    {
                        "run_id": "run-123",
                        "workflow_id": "export_lc_sc",
                        "manual_verification_required": True,
                        "document_count": 2,
                        "documents": [
                            {
                                "saved_document": {"saved_document_id": "doc-1"},
                                "manual_verification_status": "verified",
                                "audit_status": "ready",
                            },
                            {
                                "saved_document": {"saved_document_id": "doc-2"},
                                "manual_verification_status": "pending",
                                "audit_status": "ready",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            paths.target_probes_path.write_text('{"probe":1}\n{"probe":2}\n', encoding="utf-8")
            paths.discrepancies_path.write_text('{"code":"x"}\n', encoding="utf-8")
            paths.commit_marker_path.write_text('{"committed":true}\n', encoding="utf-8")
            (paths.print_markers_dir / "group-1.json").write_text("{}", encoding="utf-8")
            (paths.mail_move_markers_dir / "move-1.json").write_text("{}", encoding="utf-8")

            run_report = RunReport(
                run_id="run-123",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                tool_version="0.1.0",
                rule_pack_id="export_lc_sc.default",
                rule_pack_version="1.0.0",
                started_at_utc="2026-03-29T00:00:00Z",
                completed_at_utc=None,
                state_timezone="Asia/Dhaka",
                mail_iteration_order=["mail-1", "mail-2"],
                print_group_order=["group-1"],
                write_phase_status=WritePhaseStatus.COMMITTED,
                print_phase_status=PrintPhaseStatus.COMPLETED,
                mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
                hash_algorithm="sha256",
                run_start_backup_hash="a" * 64,
                current_workbook_hash="b" * 64,
                staged_write_plan_hash="c" * 64,
                summary={"pass": 2, "warning": 0, "hard_block": 0},
            )
            mail_outcomes = [
                MailOutcomeRecord(
                    run_id="run-123",
                    mail_id="mail-1",
                    workflow_id=WorkflowId.EXPORT_LC_SC,
                    snapshot_index=0,
                    processing_status=MailProcessingStatus.MOVED,
                    final_decision=FinalDecision.PASS,
                    decision_reasons=[],
                    eligible_for_write=False,
                    eligible_for_print=False,
                    eligible_for_mail_move=False,
                    source_entry_id="entry-1",
                    subject_raw="subject-1",
                    sender_address="sender@example.com",
                    print_group_id="group-1",
                    mail_move_operation_id="move-1",
                    manual_document_verification_summary={
                        "document_count": 2,
                        "verified_count": 1,
                        "pending_count": 1,
                        "untracked_count": 0,
                    },
                ),
                MailOutcomeRecord(
                    run_id="run-123",
                    mail_id="mail-2",
                    workflow_id=WorkflowId.EXPORT_LC_SC,
                    snapshot_index=1,
                    processing_status=MailProcessingStatus.WRITTEN,
                    final_decision=FinalDecision.PASS,
                    decision_reasons=[],
                    eligible_for_write=False,
                    eligible_for_print=True,
                    eligible_for_mail_move=False,
                    source_entry_id="entry-2",
                    subject_raw="subject-2",
                    sender_address="sender@example.com",
                ),
            ]
            staged_write_plan = [
                WriteOperation(
                    write_operation_id="op-1",
                    run_id="run-123",
                    mail_id="mail-1",
                    operation_index_within_mail=0,
                    sheet_name="Sheet1",
                    row_index=3,
                    column_key="file_no",
                    expected_pre_write_value=None,
                    expected_post_write_value="P/26/0042",
                    row_eligibility_checks=[],
                )
            ]

            summary = summarize_run_status(
                run_report=run_report,
                mail_outcomes=mail_outcomes,
                staged_write_plan=staged_write_plan,
                artifact_paths=paths,
            )

        self.assertEqual(summary["phases"]["write"]["status"], "committed")
        self.assertEqual(summary["phases"]["write"]["staged_write_operation_count"], 1)
        self.assertEqual(summary["phases"]["write"]["target_probe_count"], 2)
        self.assertTrue(summary["phases"]["write"]["commit_marker_present"])
        self.assertEqual(summary["phases"]["write"]["successful_mail_count"], 2)
        self.assertEqual(summary["phases"]["print"]["planned_group_count"], 1)
        self.assertEqual(summary["phases"]["print"]["completion_marker_count"], 1)
        self.assertEqual(summary["phases"]["print"]["successful_mail_count"], 1)
        self.assertEqual(summary["phases"]["mail_moves"]["planned_operation_count"], 1)
        self.assertEqual(summary["phases"]["mail_moves"]["completion_marker_count"], 1)
        self.assertEqual(summary["phases"]["mail_moves"]["successful_mail_count"], 1)
        self.assertEqual(summary["artifact_counts"]["discrepancy_count"], 1)
        self.assertEqual(summary["manual_verification"]["bundle"]["document_count"], 2)


if __name__ == "__main__":
    unittest.main()
