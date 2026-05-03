from __future__ import annotations

import unittest

from project.documents import SavedDocumentAnalysis
from project.models import SavedDocument
from project.workflows.ud_ip_exp import UDIPEXPDocumentKind
from project.workflows.ud_ip_exp.document_classification import (
    classify_saved_ud_ip_exp_documents,
    document_kind_from_filename,
)


class UDIPEXPDocumentClassificationTests(unittest.TestCase):
    def test_non_matching_filename_is_skipped_without_document_analysis(self) -> None:
        saved_document = SavedDocument(
            saved_document_id="saved-pi",
            mail_id="mail-1",
            attachment_name="PDL-26-1755.pdf",
            normalized_filename="PDL-26-1755.pdf",
            destination_path="D:/docs/PDL-26-1755.pdf",
            file_sha256="a" * 64,
            save_decision="saved_new",
            attachment_index=0,
        )

        class Provider:
            def analyze(self, *, saved_document):
                raise AssertionError("Non-matching filenames should be skipped before OCR analysis.")

        result = classify_saved_ud_ip_exp_documents(
            saved_documents=[saved_document],
            analysis_provider=Provider(),
        )

        self.assertEqual(result.documents, [])
        self.assertEqual(result.saved_documents[0].document_type, "supporting_pdf")
        self.assertEqual(
            result.saved_documents[0].classification_reason,
            "Filename does not match UD/IP/EXP workflow naming conventions; document was skipped for extraction.",
        )
        self.assertTrue(result.saved_documents[0].print_eligible)
        self.assertIsNone(result.saved_documents[0].extracted_document_number)

    def test_matching_ip_filename_uses_filename_when_ocr_identifier_is_low_confidence(self) -> None:
        saved_document = SavedDocument(
            saved_document_id="saved-ip",
            mail_id="mail-1",
            attachment_name="IP-LC-0113-ANANTA.pdf",
            normalized_filename="IP-LC-0113-ANANTA.pdf",
            destination_path="D:/docs/IP-LC-0113-ANANTA.pdf",
            file_sha256="b" * 64,
            save_decision="saved_new",
            attachment_index=0,
        )

        class Provider:
            def analyze(self, *, saved_document):
                return SavedDocumentAnalysis(
                    analysis_basis="ocr_text",
                    extracted_document_number="IP-AED-UR-TO-ND-TOY-GT-SEMICON",
                    extracted_document_number_confidence=0.3775,
                )

        result = classify_saved_ud_ip_exp_documents(
            saved_documents=[saved_document],
            analysis_provider=Provider(),
        )

        self.assertEqual(len(result.documents), 1)
        self.assertEqual(result.saved_documents[0].document_type, "ip_document")
        self.assertEqual(result.saved_documents[0].extracted_document_number, "IP-LC-0113-ANANTA")

    def test_matching_ud_filename_uses_ctg_structured_identifier_over_filename(self) -> None:
        saved_document = SavedDocument(
            saved_document_id="saved-ud",
            mail_id="mail-1",
            attachment_name="UD-LC-0127-COTTONEX FASHIONS LTD.pdf",
            normalized_filename="UD-LC-0127-COTTONEX FASHIONS LTD.pdf",
            destination_path="D:/docs/UD-LC-0127-COTTONEX FASHIONS LTD.pdf",
            file_sha256="c" * 64,
            save_decision="saved_new",
            attachment_index=0,
        )

        class Provider:
            def analyze(self, *, saved_document):
                return SavedDocumentAnalysis(
                    analysis_basis="structured_ud_layered_table",
                    extracted_document_number="BGMEA/CTG/AM/2026/6425/020-010",
                    extracted_document_number_confidence=1.0,
                    extracted_document_date="2026-04-16",
                    extracted_document_date_confidence=1.0,
                )

        result = classify_saved_ud_ip_exp_documents(
            saved_documents=[saved_document],
            analysis_provider=Provider(),
        )

        self.assertEqual(len(result.documents), 1)
        self.assertEqual(
            result.saved_documents[0].extracted_document_number,
            "BGMEA/CTG/AM/2026/6425/020-010",
        )
        self.assertEqual(result.saved_documents[0].extracted_document_date, "2026-04-16")
        self.assertEqual(result.documents[0].document_number.value, "BGMEA/CTG/AM/2026/6425/020-010")
        self.assertEqual(result.documents[0].document_date.value, "2026-04-16")

    def test_matching_ud_filename_does_not_fallback_to_filename_without_bgmea_identifier(self) -> None:
        saved_document = SavedDocument(
            saved_document_id="saved-ud",
            mail_id="mail-1",
            attachment_name="UD-LC-0127-COTTONEX FASHIONS LTD.pdf",
            normalized_filename="UD-LC-0127-COTTONEX FASHIONS LTD.pdf",
            destination_path="D:/docs/UD-LC-0127-COTTONEX FASHIONS LTD.pdf",
            file_sha256="d" * 64,
            save_decision="saved_new",
            attachment_index=0,
        )

        class Provider:
            def analyze(self, *, saved_document):
                return SavedDocumentAnalysis(
                    analysis_basis="layered_text",
                    extracted_document_number=None,
                    extracted_document_date="2026-04-16",
                    extracted_document_date_confidence=1.0,
                )

        result = classify_saved_ud_ip_exp_documents(
            saved_documents=[saved_document],
            analysis_provider=Provider(),
        )

        self.assertEqual(result.documents, [])
        self.assertIsNone(result.saved_documents[0].extracted_document_number)
        self.assertEqual([item.code for item in result.discrepancies], ["ud_document_number_pattern_mismatch"])
        self.assertEqual(
            result.discrepancies[0].details["normalized_filename"],
            "UD-LC-0127-COTTONEX FASHIONS LTD.pdf",
        )

    def test_matching_ud_filename_hard_blocks_invalid_bgmea_identifier_from_analysis(self) -> None:
        saved_document = SavedDocument(
            saved_document_id="saved-ud",
            mail_id="mail-1",
            attachment_name="UD-LC-0127-COTTONEX FASHIONS LTD.pdf",
            normalized_filename="UD-LC-0127-COTTONEX FASHIONS LTD.pdf",
            destination_path="D:/docs/UD-LC-0127-COTTONEX FASHIONS LTD.pdf",
            file_sha256="e" * 64,
            save_decision="saved_new",
            attachment_index=0,
        )

        class Provider:
            def analyze(self, *, saved_document):
                return SavedDocumentAnalysis(
                    analysis_basis="structured_ud_layered_table",
                    extracted_document_number="BGMEA/DHK/W/2026/5483/003",
                    extracted_document_number_confidence=1.0,
                    extracted_document_date="2026-04-16",
                    extracted_document_date_confidence=1.0,
                )

        result = classify_saved_ud_ip_exp_documents(
            saved_documents=[saved_document],
            analysis_provider=Provider(),
        )

        self.assertEqual(result.documents, [])
        self.assertIsNone(result.saved_documents[0].extracted_document_number)
        self.assertEqual([item.code for item in result.discrepancies], ["ud_document_number_pattern_mismatch"])
        self.assertEqual(
            result.discrepancies[0].details["extracted_document_number"],
            "BGMEA/DHK/W/2026/5483/003",
        )

    def test_exp_filename_must_end_strictly_with_exp_stem(self) -> None:
        self.assertEqual(document_kind_from_filename("123-EXP.pdf"), UDIPEXPDocumentKind.EXP)
        self.assertEqual(document_kind_from_filename("123-exp.PDF"), UDIPEXPDocumentKind.EXP)
        self.assertIsNone(document_kind_from_filename("123-EXP-INVOICE.pdf"))
        self.assertIsNone(document_kind_from_filename("123-EXP-SCAN.pdf"))


if __name__ == "__main__":
    unittest.main()
