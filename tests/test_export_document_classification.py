from __future__ import annotations

import unittest

from project.models import SavedDocument
from project.workflows.export_lc_sc.document_classification import classify_saved_export_documents
from project.workflows.export_lc_sc.payloads import build_export_mail_payload
from project.workflows.snapshot import SourceAttachmentRecord, SourceEmailRecord, build_email_snapshot


class ExportDocumentClassificationTests(unittest.TestCase):
    def test_classify_saved_export_documents_marks_lc_and_pi_candidates_for_print(self) -> None:
        mail = build_email_snapshot(
            [
                SourceEmailRecord(
                    entry_id="entry-1",
                    received_time="2026-03-28T03:00:00Z",
                    subject_raw="LC-0038-ANANTA GARMENTS LTD",
                    sender_address="sender@example.com",
                    body_text="Please process file P/26/0042.",
                    attachments=[
                        SourceAttachmentRecord(attachment_name="LC-0038-ANANTA GARMENTS LTD.pdf"),
                        SourceAttachmentRecord(attachment_name="PDL-26-0042-R1.pdf"),
                        SourceAttachmentRecord(attachment_name="cover-letter.pdf"),
                    ],
                )
            ],
            state_timezone="Asia/Dhaka",
        )[0]
        payload = build_export_mail_payload(mail)

        classified = classify_saved_export_documents(
            payload=payload,
            saved_documents=[
                SavedDocument(
                    saved_document_id="doc-1",
                    mail_id=mail.mail_id,
                    attachment_name="LC-0038-ANANTA GARMENTS LTD.pdf",
                    normalized_filename="LC-0038-ANANTA GARMENTS LTD.pdf",
                    destination_path="C:/docs/LC-0038-ANANTA GARMENTS LTD.pdf",
                    file_sha256="a" * 64,
                    save_decision="saved_new",
                ),
                SavedDocument(
                    saved_document_id="doc-2",
                    mail_id=mail.mail_id,
                    attachment_name="PDL-26-0042-R1.pdf",
                    normalized_filename="PDL-26-0042-R1.pdf",
                    destination_path="C:/docs/PDL-26-0042-R1.pdf",
                    file_sha256="b" * 64,
                    save_decision="saved_new",
                ),
                SavedDocument(
                    saved_document_id="doc-3",
                    mail_id=mail.mail_id,
                    attachment_name="cover-letter.pdf",
                    normalized_filename="cover-letter.pdf",
                    destination_path="C:/docs/cover-letter.pdf",
                    file_sha256="c" * 64,
                    save_decision="saved_new",
                ),
            ],
        )

        self.assertEqual(
            [document.document_type for document in classified.saved_documents],
            ["export_lc_sc_document", "export_pi_document", "non_print_supporting_pdf"],
        )
        self.assertEqual(
            [document.print_eligible for document in classified.saved_documents],
            [True, True, False],
        )


if __name__ == "__main__":
    unittest.main()
