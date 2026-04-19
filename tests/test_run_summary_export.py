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
from project.workflows.run_summary_export import build_run_summary_export


class RunSummaryExportTests(unittest.TestCase):
    def test_build_run_summary_export_combines_status_artifacts_and_precheck(self) -> None:
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
                        "manual_verification_required": True,
                        "document_count": 1,
                        "pending_document_count": 1,
                        "documents": [{"manual_verification_status": "pending", "audit_status": "ready"}],
                    }
                ),
                encoding="utf-8",
            )
            paths.staged_write_plan_path.write_text("[]\n", encoding="utf-8")
            paths.run_metadata_path.write_text('{"run_id":"run-123"}\n', encoding="utf-8")
            paths.backup_workbook_path.write_bytes(b"fake workbook")
            paths.backup_hash_path.write_text("abcd\n", encoding="utf-8")

            run_report = RunReport(
                run_id="run-123",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                tool_version="0.1.0",
                rule_pack_id="export_lc_sc.default",
                rule_pack_version="1.0.0",
                started_at_utc="2026-03-30T00:00:00Z",
                completed_at_utc=None,
                state_timezone="Asia/Dhaka",
                mail_iteration_order=["mail-1"],
                print_group_order=[],
                write_phase_status=WritePhaseStatus.UNCERTAIN_NOT_COMMITTED,
                print_phase_status=PrintPhaseStatus.NOT_STARTED,
                mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
                hash_algorithm="sha256",
                run_start_backup_hash="a" * 64,
                current_workbook_hash="b" * 64,
                staged_write_plan_hash="c" * 64,
                summary={"pass": 1, "warning": 0, "hard_block": 0},
            )
            mail_outcomes = [
                MailOutcomeRecord(
                    run_id="run-123",
                    mail_id="mail-1",
                    workflow_id=WorkflowId.EXPORT_LC_SC,
                    snapshot_index=0,
                    processing_status=MailProcessingStatus.VALIDATED,
                    final_decision=FinalDecision.PASS,
                    decision_reasons=[],
                    eligible_for_write=False,
                    eligible_for_print=False,
                    eligible_for_mail_move=False,
                    source_entry_id="entry-1",
                    subject_raw="subject",
                    sender_address="a@example.com",
                )
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

            payload = build_run_summary_export(
                run_report=run_report,
                mail_outcomes=mail_outcomes,
                staged_write_plan=staged_write_plan,
                artifact_paths=paths,
            )

        self.assertEqual(payload["run_id"], "run-123")
        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["run_status"]["phases"]["write"]["status"], "uncertain_not_committed")
        self.assertTrue(payload["recovery_precheck"]["needs_recovery_gate"])
        self.assertEqual(payload["summary_counts"]["mail_count"], 1)
        self.assertEqual(payload["summary_counts"]["staged_write_operation_count"], 1)
        self.assertEqual(payload["summary_counts"]["manual_verification_pending_count"], 1)


if __name__ == "__main__":
    unittest.main()
