from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project.models import EmailAttachment, EmailMessage, WorkflowId
from project.storage import SimulatedAttachmentContentProvider
from project.workflows.live_smoke_test import (
    build_live_smoke_test_report,
    save_smoke_test_pdf_audits,
)


class LiveSmokeTestWorkflowTests(unittest.TestCase):
    def test_save_smoke_test_pdf_audits_saves_pdf_and_writes_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_root = Path(temp_dir) / "bundle"
            mail = EmailMessage(
                mail_id="mail-1",
                entry_id="entry-1",
                received_time_utc="2026-03-30T00:00:00Z",
                received_time_workflow_tz="2026-03-30T06:00:00+06:00",
                subject_raw="subject",
                sender_address="a@example.com",
                snapshot_index=0,
                attachments=[
                    EmailAttachment(
                        attachment_id="att-1",
                        attachment_index=0,
                        attachment_name="invoice.pdf",
                        normalized_filename="invoice.pdf",
                    ),
                    EmailAttachment(
                        attachment_id="att-2",
                        attachment_index=1,
                        attachment_name="notes.txt",
                        normalized_filename="notes.txt",
                    ),
                ],
            )
            provider = SimulatedAttachmentContentProvider(
                content_by_key={("entry-1", 0): b"%PDF-1.4\nfake\n"}
            )

            with patch(
                "project.workflows.live_smoke_test.extract_saved_document_raw_report",
                return_value={
                    "mode": "layered",
                    "page_count": 1,
                    "combined_text": "hello",
                    "pages": [{"page_number": 1, "text": "hello"}],
                },
            ):
                payload = save_smoke_test_pdf_audits(
                    snapshot=[mail],
                    bundle_root=bundle_root,
                    provider=provider,
                    audit_mode="layered",
                    max_pdf_attachments=2,
                )

            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["saved_pdf_count"], 1)
            self.assertEqual(payload["audited_pdf_count"], 1)
            self.assertEqual(payload["issue_count"], 0)
            saved_document = payload["saved_documents"][0]
            self.assertTrue(Path(saved_document["destination_path"]).exists())
            audit_dir = Path(payload["document_audit_directory"])
            audit_files = list(audit_dir.glob("*.layered.json"))
            self.assertEqual(len(audit_files), 1)
            audit_report = json.loads(audit_files[0].read_text(encoding="utf-8"))
            self.assertEqual(audit_report["combined_text"], "hello")

    def test_build_live_smoke_test_report_marks_attention_when_attachment_issues_exist(self) -> None:
        payload = build_live_smoke_test_report(
            workflow_id=WorkflowId.EXPORT_LC_SC,
            smoke_test_id="smoke-1",
            bundle_root=Path("C:/tmp/bundle"),
            readiness_report={
                "overall_status": "ready",
                "issue_section_count": 0,
            },
            attachment_audit_section={
                "status": "issue",
                "issue_count": 1,
                "saved_pdf_count": 0,
                "audited_pdf_count": 0,
            },
        )

        self.assertEqual(payload["overall_status"], "attention_required")
        self.assertEqual(payload["summary_counts"]["attachment_issue_count"], 1)


if __name__ == "__main__":
    unittest.main()
