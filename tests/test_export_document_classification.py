from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project.documents import JsonManifestSavedDocumentAnalysisProvider
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
        self.assertEqual(classified.discrepancies, [])

    def test_classify_saved_export_documents_uses_manifest_analysis_to_select_generic_pi_pdf(self) -> None:
        mail = build_email_snapshot(
            [
                SourceEmailRecord(
                    entry_id="entry-1",
                    received_time="2026-03-28T03:00:00Z",
                    subject_raw="LC-0038-ANANTA GARMENTS LTD",
                    sender_address="sender@example.com",
                    body_text="Please process file P/26/0042.",
                    attachments=[
                        SourceAttachmentRecord(attachment_name="LC-0038.pdf"),
                        SourceAttachmentRecord(attachment_name="supporting.pdf"),
                    ],
                )
            ],
            state_timezone="Asia/Dhaka",
        )[0]
        payload = build_export_mail_payload(mail)

        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "analysis.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "normalized_filename": "supporting.pdf",
                            "extracted_pi_number": "PDL-26-0042",
                            "clause_related_lc_sc_number": "LC-0038",
                            "clause_excerpt": "PI PDL-26-0042 is issued under LC-0038",
                            "clause_confidence": 0.98,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            classified = classify_saved_export_documents(
                payload=payload,
                saved_documents=[
                    SavedDocument(
                        saved_document_id="doc-1",
                        mail_id=mail.mail_id,
                        attachment_name="LC-0038.pdf",
                        normalized_filename="LC-0038.pdf",
                        destination_path="C:/docs/LC-0038.pdf",
                        file_sha256="a" * 64,
                        save_decision="saved_new",
                        attachment_index=0,
                    ),
                    SavedDocument(
                        saved_document_id="doc-2",
                        mail_id=mail.mail_id,
                        attachment_name="supporting.pdf",
                        normalized_filename="supporting.pdf",
                        destination_path="C:/docs/supporting.pdf",
                        file_sha256="b" * 64,
                        save_decision="saved_new",
                        attachment_index=1,
                    ),
                ],
                analysis_provider=JsonManifestSavedDocumentAnalysisProvider(manifest_path),
            )

        self.assertEqual(
            [document.document_type for document in classified.saved_documents],
            ["export_lc_sc_document", "export_pi_document"],
        )
        self.assertEqual(
            [document.print_eligible for document in classified.saved_documents],
            [True, True],
        )
        self.assertEqual(classified.saved_documents[1].analysis_basis, "json_manifest")
        self.assertEqual(classified.saved_documents[1].extracted_pi_number, "PDL-26-0042")
        self.assertEqual(classified.saved_documents[1].extracted_pi_confidence, None)

    def test_classify_saved_export_documents_marks_equal_cross_class_evidence_as_ambiguous(self) -> None:
        mail = build_email_snapshot(
            [
                SourceEmailRecord(
                    entry_id="entry-1",
                    received_time="2026-03-28T03:00:00Z",
                    subject_raw="LC-0038-ANANTA GARMENTS LTD",
                    sender_address="sender@example.com",
                    body_text="Please process file P/26/0042.",
                    attachments=[SourceAttachmentRecord(attachment_name="LC-0038-PDL-26-0042.pdf")],
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
                    attachment_name="LC-0038-PDL-26-0042.pdf",
                    normalized_filename="LC-0038-PDL-26-0042.pdf",
                    destination_path="C:/docs/LC-0038-PDL-26-0042.pdf",
                    file_sha256="a" * 64,
                    save_decision="saved_new",
                    attachment_index=0,
                )
            ],
        )

        self.assertEqual(classified.saved_documents[0].document_type, "ambiguous_export_pdf")
        self.assertFalse(classified.saved_documents[0].print_eligible)
        self.assertEqual([item.code for item in classified.discrepancies], ["attachment_classification_ambiguous"])

    def test_classify_saved_export_documents_hard_blocks_selected_ocr_pi_below_threshold(self) -> None:
        mail = build_email_snapshot(
            [
                SourceEmailRecord(
                    entry_id="entry-1",
                    received_time="2026-03-28T03:00:00Z",
                    subject_raw="LC-0038-ANANTA GARMENTS LTD",
                    sender_address="sender@example.com",
                    body_text="Please process file P/26/0042.",
                    attachments=[
                        SourceAttachmentRecord(attachment_name="LC-0038.pdf"),
                        SourceAttachmentRecord(attachment_name="scan.pdf"),
                    ],
                )
            ],
            state_timezone="Asia/Dhaka",
        )[0]
        payload = build_export_mail_payload(mail)

        class OCRLikeProvider:
            def analyze(self, *, saved_document: SavedDocument):
                from project.documents import SavedDocumentAnalysis

                if saved_document.normalized_filename == "scan.pdf":
                    return SavedDocumentAnalysis(
                        analysis_basis="ocr_text",
                        extracted_pi_number="PDL-26-0042",
                        extracted_pi_confidence=0.94,
                        clause_confidence=0.94,
                    )
                return SavedDocumentAnalysis(analysis_basis="pymupdf_text")

        classified = classify_saved_export_documents(
            payload=payload,
            saved_documents=[
                SavedDocument(
                    saved_document_id="doc-1",
                    mail_id=mail.mail_id,
                    attachment_name="LC-0038.pdf",
                    normalized_filename="LC-0038.pdf",
                    destination_path="C:/docs/LC-0038.pdf",
                    file_sha256="a" * 64,
                    save_decision="saved_new",
                    attachment_index=0,
                ),
                SavedDocument(
                    saved_document_id="doc-2",
                    mail_id=mail.mail_id,
                    attachment_name="scan.pdf",
                    normalized_filename="scan.pdf",
                    destination_path="C:/docs/scan.pdf",
                    file_sha256="b" * 64,
                    save_decision="saved_new",
                    attachment_index=1,
                ),
            ],
            analysis_provider=OCRLikeProvider(),
        )

        self.assertIn("ocr_required_field_below_threshold", [item.code for item in classified.discrepancies])
        self.assertFalse(any(document.print_eligible for document in classified.saved_documents))


if __name__ == "__main__":
    unittest.main()
