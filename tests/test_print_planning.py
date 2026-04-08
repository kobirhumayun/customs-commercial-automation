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
    WriteOperation,
    WritePhaseStatus,
)
from project.workflows.print_planning import build_print_plan_payload, plan_print_batches


class PrintPlanningTests(unittest.TestCase):
    def test_plan_print_batches_orders_groups_by_earliest_written_row(self) -> None:
        run_report = RunReport(
            run_id="run-1",
            workflow_id=WorkflowId.EXPORT_LC_SC,
            tool_version="0.1.0",
            rule_pack_id="export_lc_sc.default",
            rule_pack_version="1.0.0",
            started_at_utc="2026-03-28T00:00:00Z",
            completed_at_utc=None,
            state_timezone="Asia/Dhaka",
            mail_iteration_order=["mail-a", "mail-b"],
            print_group_order=[],
            write_phase_status=WritePhaseStatus.COMMITTED,
            print_phase_status=PrintPhaseStatus.NOT_STARTED,
            mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
            hash_algorithm="sha256",
            run_start_backup_hash="a" * 64,
            current_workbook_hash="b" * 64,
            staged_write_plan_hash="c" * 64,
            summary={"pass": 2, "warning": 0, "hard_block": 0},
        )
        mail_outcomes = [
            MailOutcomeRecord(
                run_id="run-1",
                mail_id="mail-a",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                snapshot_index=0,
                processing_status=MailProcessingStatus.WRITTEN,
                final_decision=FinalDecision.PASS,
                decision_reasons=[],
                eligible_for_write=False,
                eligible_for_print=True,
                eligible_for_mail_move=True,
                source_entry_id="entry-a",
                subject_raw="mail a",
                sender_address="a@example.com",
                saved_documents=[
                    {
                        "destination_path": "C:/docs/a-first.pdf",
                        "save_decision": "saved_new",
                        "print_eligible": True,
                    },
                    {
                        "destination_path": "C:/docs/a-second.pdf",
                        "save_decision": "saved_new",
                        "print_eligible": True,
                    },
                ],
            ),
            MailOutcomeRecord(
                run_id="run-1",
                mail_id="mail-b",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                snapshot_index=1,
                processing_status=MailProcessingStatus.WRITTEN,
                final_decision=FinalDecision.PASS,
                decision_reasons=[],
                eligible_for_write=False,
                eligible_for_print=True,
                eligible_for_mail_move=True,
                source_entry_id="entry-b",
                subject_raw="mail b",
                sender_address="b@example.com",
                saved_documents=[
                    {
                        "destination_path": "C:/docs/b-only.pdf",
                        "save_decision": "saved_new",
                        "print_eligible": True,
                    }
                ],
            ),
        ]
        staged_write_plan = [
            WriteOperation(
                write_operation_id="op-a",
                run_id="run-1",
                mail_id="mail-a",
                operation_index_within_mail=0,
                sheet_name="Sheet1",
                row_index=5,
                column_key="file_no",
                expected_pre_write_value=None,
                expected_post_write_value="P/26/0042",
                row_eligibility_checks=[],
            ),
            WriteOperation(
                write_operation_id="op-b",
                run_id="run-1",
                mail_id="mail-b",
                operation_index_within_mail=0,
                sheet_name="Sheet1",
                row_index=3,
                column_key="file_no",
                expected_pre_write_value=None,
                expected_post_write_value="P/26/0043",
                row_eligibility_checks=[],
            ),
        ]

        result = plan_print_batches(
            run_report=run_report,
            mail_outcomes=mail_outcomes,
            staged_write_plan=staged_write_plan,
        )

        self.assertEqual(result.run_report.print_phase_status, PrintPhaseStatus.PLANNED)
        self.assertEqual([batch.mail_id for batch in result.print_batches], ["mail-b", "mail-a"])
        self.assertEqual(result.run_report.print_group_order, [batch.print_group_id for batch in result.print_batches])
        self.assertEqual(result.mail_outcomes[0].print_group_id, result.print_batches[1].print_group_id)
        self.assertEqual(result.mail_outcomes[1].print_group_id, result.print_batches[0].print_group_id)

        payload = build_print_plan_payload(result.print_batches)
        self.assertEqual(payload["print_group_order"], result.run_report.print_group_order)
        self.assertEqual(len(payload["print_groups"][1]["document_path_hashes"]), 2)

    def test_plan_print_batches_includes_saved_documents_without_print_eligible_field(self) -> None:
        run_report = RunReport(
            run_id="run-1",
            workflow_id=WorkflowId.EXPORT_LC_SC,
            tool_version="0.1.0",
            rule_pack_id="export_lc_sc.default",
            rule_pack_version="1.0.0",
            started_at_utc="2026-03-28T00:00:00Z",
            completed_at_utc=None,
            state_timezone="Asia/Dhaka",
            mail_iteration_order=["mail-a"],
            print_group_order=[],
            write_phase_status=WritePhaseStatus.COMMITTED,
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
                run_id="run-1",
                mail_id="mail-a",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                snapshot_index=0,
                processing_status=MailProcessingStatus.WRITTEN,
                final_decision=FinalDecision.PASS,
                decision_reasons=[],
                eligible_for_write=False,
                eligible_for_print=True,
                eligible_for_mail_move=True,
                source_entry_id="entry-a",
                subject_raw="mail a",
                sender_address="a@example.com",
                saved_documents=[
                    {
                        "destination_path": "C:/docs/not-printable.pdf",
                        "save_decision": "saved_new",
                    },
                    {
                        "destination_path": "C:/docs/printable.pdf",
                        "save_decision": "saved_new",
                        "print_eligible": True,
                    },
                ],
            )
        ]
        staged_write_plan = [
            WriteOperation(
                write_operation_id="op-a",
                run_id="run-1",
                mail_id="mail-a",
                operation_index_within_mail=0,
                sheet_name="Sheet1",
                row_index=5,
                column_key="file_no",
                expected_pre_write_value=None,
                expected_post_write_value="P/26/0042",
                row_eligibility_checks=[],
            )
        ]

        result = plan_print_batches(
            run_report=run_report,
            mail_outcomes=mail_outcomes,
            staged_write_plan=staged_write_plan,
        )

        self.assertEqual(len(result.print_batches), 1)
        self.assertEqual(
            result.print_batches[0].document_paths,
            ["C:/docs/not-printable.pdf", "C:/docs/printable.pdf"],
        )

    def test_plan_print_batches_carries_manual_verification_summary_without_gating(self) -> None:
        run_report = RunReport(
            run_id="run-1",
            workflow_id=WorkflowId.EXPORT_LC_SC,
            tool_version="0.1.0",
            rule_pack_id="export_lc_sc.default",
            rule_pack_version="1.0.0",
            started_at_utc="2026-03-28T00:00:00Z",
            completed_at_utc=None,
            state_timezone="Asia/Dhaka",
            mail_iteration_order=["mail-a"],
            print_group_order=[],
            write_phase_status=WritePhaseStatus.COMMITTED,
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
                run_id="run-1",
                mail_id="mail-a",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                snapshot_index=0,
                processing_status=MailProcessingStatus.WRITTEN,
                final_decision=FinalDecision.PASS,
                decision_reasons=[],
                eligible_for_write=False,
                eligible_for_print=True,
                eligible_for_mail_move=True,
                source_entry_id="entry-a",
                subject_raw="mail a",
                sender_address="a@example.com",
                saved_documents=[
                    {
                        "saved_document_id": "doc-1",
                        "normalized_filename": "printable.pdf",
                        "destination_path": "C:/docs/printable.pdf",
                        "save_decision": "saved_new",
                        "print_eligible": True,
                    }
                ],
            )
        ]
        staged_write_plan = [
            WriteOperation(
                write_operation_id="op-a",
                run_id="run-1",
                mail_id="mail-a",
                operation_index_within_mail=0,
                sheet_name="Sheet1",
                row_index=5,
                column_key="file_no",
                expected_pre_write_value=None,
                expected_post_write_value="P/26/0042",
                row_eligibility_checks=[],
            )
        ]
        manual_bundle = {
            "documents": [
                {
                    "saved_document": {"saved_document_id": "doc-1"},
                    "manual_verification_status": "verified",
                    "verified_by": "humayun",
                    "verified_at_utc": "2026-03-30T10:00:00Z",
                    "audit_report_path": "C:/runs/run-1/document_audits/doc-1.layered.json",
                }
            ]
        }

        result = plan_print_batches(
            run_report=run_report,
            mail_outcomes=mail_outcomes,
            staged_write_plan=staged_write_plan,
            manual_verification_bundle=manual_bundle,
        )

        self.assertEqual(result.print_batches[0].manual_verification_summary["verified_count"], 1)
        self.assertEqual(result.print_batches[0].manual_verification_summary["pending_count"], 0)
        self.assertIn("Manual PDF verification summary", result.mail_outcomes[0].decision_reasons[-1])
        payload = build_print_plan_payload(result.print_batches)
        self.assertEqual(payload["print_groups"][0]["manual_verification_summary"]["verified_count"], 1)

    def test_plan_print_batches_requires_committed_or_safe_resume_gate(self) -> None:
        run_report = RunReport(
            run_id="run-1",
            workflow_id=WorkflowId.EXPORT_LC_SC,
            tool_version="0.1.0",
            rule_pack_id="export_lc_sc.default",
            rule_pack_version="1.0.0",
            started_at_utc="2026-03-28T00:00:00Z",
            completed_at_utc=None,
            state_timezone="Asia/Dhaka",
            mail_iteration_order=[],
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

        with self.assertRaisesRegex(ValueError, "Print planning requires write_phase_status=committed"):
            plan_print_batches(run_report=run_report, mail_outcomes=[], staged_write_plan=[])

        recovered = plan_print_batches(
            run_report=run_report,
            mail_outcomes=[],
            staged_write_plan=[],
            recovery_outcome="safe_resume",
        )
        self.assertEqual(recovered.run_report.print_phase_status, PrintPhaseStatus.PLANNED)


if __name__ == "__main__":
    unittest.main()
