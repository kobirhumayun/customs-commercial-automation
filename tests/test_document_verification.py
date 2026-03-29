from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
from project.storage import create_run_artifact_layout
from project.workflows.document_verification import build_document_manual_verification_bundle


class DocumentVerificationTests(unittest.TestCase):
    def test_build_document_manual_verification_bundle_writes_audit_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-123",
            )
            run_report = RunReport(
                run_id="run-123",
                workflow_id=WorkflowId.EXPORT_LC_SC,
                tool_version="0.1.0",
                rule_pack_id="export_lc_sc.default",
                rule_pack_version="1.0.0",
                started_at_utc="2026-03-29T00:00:00Z",
                completed_at_utc=None,
                state_timezone="Asia/Dhaka",
                mail_iteration_order=["mail-1"],
                print_group_order=[],
                write_phase_status=WritePhaseStatus.NOT_STARTED,
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
                    subject_raw="LC-0038-ANANTA GARMENTS LTD",
                    sender_address="sender@example.com",
                    saved_documents=[
                        {
                            "saved_document_id": "doc-1",
                            "mail_id": "mail-1",
                            "attachment_name": "LC.pdf",
                            "normalized_filename": "LC.pdf",
                            "destination_path": "C:/docs/LC.pdf",
                            "file_sha256": "d" * 64,
                            "save_decision": "saved_new",
                            "analysis_basis": "pymupdf_text",
                        }
                    ],
                )
            ]

            with patch(
                "project.workflows.document_verification.extract_saved_document_raw_report",
                return_value={
                    "mode": "layered",
                    "document_path": "C:/docs/LC.pdf",
                    "page_count": 1,
                    "combined_text": "LC-0038",
                    "pages": [{"page_number": 1, "text": "LC-0038"}],
                },
            ):
                result = build_document_manual_verification_bundle(
                    run_report=run_report,
                    mail_outcomes=mail_outcomes,
                    artifact_paths=paths,
                    extraction_mode="layered",
                )

            bundle = json.loads(paths.manual_document_verification_path.read_text(encoding="utf-8"))
            audit_path = Path(bundle["documents"][0]["audit_report_path"])
            audit_payload = json.loads(audit_path.read_text(encoding="utf-8"))

        self.assertEqual(result.document_count, 1)
        self.assertEqual(result.audit_ready_count, 1)
        self.assertEqual(bundle["manual_verification_required"], True)
        self.assertEqual(bundle["documents"][0]["manual_verification_status"], "pending")
        self.assertEqual(bundle["documents"][0]["audit_status"], "ready")
        self.assertEqual(audit_payload["combined_text"], "LC-0038")


if __name__ == "__main__":
    unittest.main()
