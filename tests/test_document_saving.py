from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project.erp import ERPFamily
from project.models import FinalDecision
from project.storage import (
    SimulatedAttachmentContentProvider,
    Win32ComAttachmentContentProvider,
    build_export_attachment_directory,
    save_export_mail_documents,
)
from project.workflows.snapshot import SourceAttachmentRecord, SourceEmailRecord, build_email_snapshot


class DocumentSavingTests(unittest.TestCase):
    def test_build_export_attachment_directory_uses_canonical_export_hierarchy(self) -> None:
        destination = build_export_attachment_directory(
            Path("C:/exports"),
            ERPFamily(
                lc_sc_number='LC:0038/01',
                buyer_name='ANANTA GARMENTS LTD.\\DHAKA',
                lc_sc_date="2026-01-10",
                folder_buyer_name="ANANTA GARMENTS LTD.",
            ),
        )

        self.assertEqual(
            destination.as_posix(),
            "C:/exports/2026/ANANTA GARMENTS LTD/LC_0038_01/All Attachments",
        )

    def test_save_export_mail_documents_saves_only_new_pdfs_and_skips_duplicate_filename(self) -> None:
        mail = build_email_snapshot(
            [
                SourceEmailRecord(
                    entry_id="entry-1",
                    received_time="2026-03-28T03:00:00Z",
                    subject_raw="LC-0038-ANANTA GARMENTS LTD",
                    sender_address="sender@example.com",
                    attachments=[
                        SourceAttachmentRecord(attachment_name="LC.pdf"),
                        SourceAttachmentRecord(attachment_name="notes.txt"),
                        SourceAttachmentRecord(attachment_name="LC.pdf"),
                    ],
                )
            ],
            state_timezone="Asia/Dhaka",
        )[0]

        content = b"%PDF-1.4\nnew lc\n"
        expected_hash = hashlib.sha256(content).hexdigest()

        with tempfile.TemporaryDirectory() as temp_dir:
            document_root = Path(temp_dir)
            provider = SimulatedAttachmentContentProvider(
                content_by_key={
                    (mail.entry_id, 0): content,
                    (mail.entry_id, 2): b"%PDF-1.4\nduplicate should not save\n",
                }
            )

            result = save_export_mail_documents(
                mail=mail,
                verified_family=ERPFamily(
                    lc_sc_number="LC-0038",
                    buyer_name="ANANTA GARMENTS LTD",
                    lc_sc_date="2026-01-10",
                ),
                document_root=document_root,
                provider=provider,
            )

            self.assertEqual(result.issues, [])
            self.assertEqual(len(result.saved_documents), 2)
            self.assertEqual(
                [document.save_decision for document in result.saved_documents],
                ["saved_new", "skipped_duplicate_filename"],
            )
            self.assertEqual(
                [document.normalized_filename for document in result.saved_documents],
                ["LC.pdf", "LC.pdf"],
            )
            self.assertEqual(result.saved_documents[0].file_sha256, expected_hash)
            saved_path = Path(result.saved_documents[0].destination_path)
            self.assertTrue(saved_path.exists())
            self.assertEqual(saved_path.read_bytes(), content)
            self.assertEqual(result.saved_documents[1].destination_path, result.saved_documents[0].destination_path)
            self.assertIn("Saved new attachment LC.pdf.", result.decision_reasons)
            self.assertIn("Skipped duplicate attachment filename LC.pdf.", result.decision_reasons)

    def test_build_export_attachment_directory_uses_name_only_for_folder_segment(self) -> None:
        destination = build_export_attachment_directory(
            Path("C:/exports"),
            ERPFamily(
                lc_sc_number="DPCBD1175392",
                buyer_name="CUTTING EDGE INDUSTRIES LTD\\1612",
                lc_sc_date="2026-03-30",
                folder_buyer_name="CUTTING EDGE INDUSTRIES LTD",
            ),
        )

        self.assertEqual(
            destination.as_posix(),
            "C:/exports/2026/CUTTING EDGE INDUSTRIES LTD/DPCBD1175392/All Attachments",
        )

    def test_save_export_mail_documents_skips_preexisting_pdf_and_saves_other_new_pdf(self) -> None:
        mail = build_email_snapshot(
            [
                SourceEmailRecord(
                    entry_id="entry-1",
                    received_time="2026-03-28T03:00:00Z",
                    subject_raw="LC-0038-ANANTA GARMENTS LTD",
                    sender_address="sender@example.com",
                    attachments=[
                        SourceAttachmentRecord(attachment_name="existing.pdf"),
                        SourceAttachmentRecord(attachment_name="new.pdf"),
                    ],
                )
            ],
            state_timezone="Asia/Dhaka",
        )[0]

        with tempfile.TemporaryDirectory() as temp_dir:
            document_root = Path(temp_dir)
            destination_directory = build_export_attachment_directory(
                document_root,
                ERPFamily(
                    lc_sc_number="LC-0038",
                    buyer_name="ANANTA GARMENTS LTD",
                    lc_sc_date="2026-01-10",
                ),
            )
            destination_directory.mkdir(parents=True, exist_ok=True)
            (destination_directory / "existing.pdf").write_bytes(b"%PDF-1.4\nalready here\n")
            provider = SimulatedAttachmentContentProvider(
                content_by_key={
                    (mail.entry_id, 0): b"%PDF-1.4\nshould not overwrite\n",
                    (mail.entry_id, 1): b"%PDF-1.4\nbrand new\n",
                }
            )

            result = save_export_mail_documents(
                mail=mail,
                verified_family=ERPFamily(
                    lc_sc_number="LC-0038",
                    buyer_name="ANANTA GARMENTS LTD",
                    lc_sc_date="2026-01-10",
                ),
                document_root=document_root,
                provider=provider,
            )

        self.assertEqual(result.issues, [])
        self.assertEqual(
            [document.save_decision for document in result.saved_documents],
            ["skipped_duplicate_filename", "saved_new"],
        )
        self.assertEqual(
            [document.normalized_filename for document in result.saved_documents],
            ["existing.pdf", "new.pdf"],
        )

    def test_save_export_mail_documents_hard_blocks_when_family_is_missing(self) -> None:
        mail = build_email_snapshot(
            [
                SourceEmailRecord(
                    entry_id="entry-1",
                    received_time="2026-03-28T03:00:00Z",
                    subject_raw="LC-0038-ANANTA GARMENTS LTD",
                    sender_address="sender@example.com",
                    attachments=[SourceAttachmentRecord(attachment_name="LC.pdf")],
                )
            ],
            state_timezone="Asia/Dhaka",
        )[0]

        result = save_export_mail_documents(
            mail=mail,
            verified_family=None,
            document_root=Path("C:/exports"),
            provider=SimulatedAttachmentContentProvider(),
        )

        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0].code, "document_storage_path_unresolved")
        self.assertEqual(result.issues[0].severity, FinalDecision.HARD_BLOCK)

    def test_save_export_mail_documents_returns_runtime_error_when_attachment_save_fails(self) -> None:
        mail = build_email_snapshot(
            [
                SourceEmailRecord(
                    entry_id="entry-1",
                    received_time="2026-03-28T03:00:00Z",
                    subject_raw="LC-0038-ANANTA GARMENTS LTD",
                    sender_address="sender@example.com",
                    attachments=[SourceAttachmentRecord(attachment_name="LC.pdf")],
                )
            ],
            state_timezone="Asia/Dhaka",
        )[0]

        with tempfile.TemporaryDirectory() as temp_dir:
            result = save_export_mail_documents(
                mail=mail,
                verified_family=ERPFamily(
                    lc_sc_number="LC-0038",
                    buyer_name="ANANTA GARMENTS LTD",
                    lc_sc_date="2026-01-10",
                ),
                document_root=Path(temp_dir),
                provider=SimulatedAttachmentContentProvider(),
            )

        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0].code, "document_save_runtime_error")
        self.assertEqual(result.issues[0].severity, FinalDecision.HARD_BLOCK)
        self.assertEqual(result.saved_documents, [])

    def test_win32com_attachment_content_provider_saves_requested_attachment_index(self) -> None:
        mail = build_email_snapshot(
            [
                SourceEmailRecord(
                    entry_id="entry-1",
                    received_time="2026-03-28T03:00:00Z",
                    subject_raw="LC-0038-ANANTA GARMENTS LTD",
                    sender_address="sender@example.com",
                )
            ],
            state_timezone="Asia/Dhaka",
        )[0]

        class FakeAttachment:
            def __init__(self) -> None:
                self.saved_path: str | None = None

            def SaveAsFile(self, path: str) -> None:
                self.saved_path = path

        class FakeAttachments:
            def __init__(self) -> None:
                self.items = {1: FakeAttachment(), 2: FakeAttachment()}

            def Item(self, index: int) -> FakeAttachment:
                return self.items[index]

        class FakeMailItem:
            def __init__(self) -> None:
                self.Attachments = FakeAttachments()

        class FakeNamespace:
            def __init__(self) -> None:
                self.mail_item = FakeMailItem()

            def GetItemFromID(self, entry_id: str) -> FakeMailItem:
                if entry_id != "entry-1":
                    raise ValueError("unexpected entry id")
                return self.mail_item

        class FakeApplication:
            def __init__(self, namespace: FakeNamespace) -> None:
                self.namespace = namespace

            def GetNamespace(self, name: str) -> FakeNamespace:
                if name != "MAPI":
                    raise ValueError("unexpected namespace")
                return self.namespace

        class FakeClient:
            def __init__(self, namespace: FakeNamespace) -> None:
                self.namespace = namespace

            def Dispatch(self, name: str) -> FakeApplication:
                if name != "Outlook.Application":
                    raise ValueError("unexpected application")
                return FakeApplication(self.namespace)

        fake_namespace = FakeNamespace()
        provider = Win32ComAttachmentContentProvider(outlook_profile=None)
        with patch("project.storage.providers._load_win32com_client_module", return_value=FakeClient(fake_namespace)):
            provider.save_attachment(
                mail=mail,
                attachment_index=1,
                destination_path=Path("C:/exports/LC.pdf"),
            )

        self.assertEqual(
            fake_namespace.mail_item.Attachments.items[2].saved_path,
            "C:\\exports\\LC.pdf",
        )


if __name__ == "__main__":
    unittest.main()
