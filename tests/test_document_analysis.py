from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project.documents import (
    JsonManifestSavedDocumentAnalysisProvider,
    LayeredSavedDocumentAnalysisProvider,
    OCRSavedDocumentAnalysisProvider,
    PyMuPDFSavedDocumentAnalysisProvider,
)
from project.models import SavedDocument


class SavedDocumentAnalysisProviderTests(unittest.TestCase):
    def test_json_manifest_provider_matches_by_destination_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "document-analysis.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "destination_path": "C:/docs/supporting.pdf",
                            "extracted_pi_number": "PDL-26-0042",
                            "clause_related_lc_sc_number": "LC-0038",
                            "clause_excerpt": "PI ref PDL-26-0042 under LC-0038",
                            "clause_confidence": 0.99,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            analysis = JsonManifestSavedDocumentAnalysisProvider(manifest_path).analyze(
                saved_document=SavedDocument(
                    saved_document_id="doc-1",
                    mail_id="mail-1",
                    attachment_name="supporting.pdf",
                    normalized_filename="supporting.pdf",
                    destination_path="C:/docs/supporting.pdf",
                    file_sha256="a" * 64,
                    save_decision="saved_new",
                )
            )

        self.assertEqual(analysis.analysis_basis, "json_manifest")
        self.assertEqual(analysis.extracted_pi_number, "PDL-26-0042")
        self.assertEqual(analysis.clause_related_lc_sc_number, "LC-0038")
        self.assertEqual(analysis.clause_confidence, 0.99)

    def test_pymupdf_provider_extracts_lc_sc_and_pi_from_saved_pdf_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "saved.pdf"
            pdf_path.write_bytes(b"fake pdf bytes")

            class FakePage:
                def get_text(self, mode: str) -> str:
                    self.last_mode = mode
                    return "Shipment under LC-0038 with PI PDL-26-42-R1 approved."

            class FakeDocument:
                def __iter__(self):
                    return iter([FakePage()])

                def close(self) -> None:
                    self.closed = True

            class FakeFitz:
                @staticmethod
                def open(path: str) -> FakeDocument:
                    self = FakeDocument()
                    self.opened_path = path
                    return self

            with patch("project.documents.providers._load_pymupdf_module", return_value=FakeFitz()):
                analysis = PyMuPDFSavedDocumentAnalysisProvider().analyze(
                    saved_document=SavedDocument(
                        saved_document_id="doc-1",
                        mail_id="mail-1",
                        attachment_name="saved.pdf",
                        normalized_filename="saved.pdf",
                        destination_path=str(pdf_path),
                        file_sha256="a" * 64,
                        save_decision="saved_new",
                    )
                )

        self.assertEqual(analysis.analysis_basis, "pymupdf_text")
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0038")
        self.assertEqual(analysis.extracted_lc_sc_confidence, 1.0)
        self.assertEqual(analysis.extracted_pi_number, "PDL-26-0042-R1")
        self.assertEqual(analysis.extracted_pi_confidence, 1.0)
        self.assertEqual(analysis.clause_related_lc_sc_number, "LC-0038")
        self.assertEqual(analysis.clause_confidence, 1.0)
        self.assertIn("LC-0038", analysis.clause_excerpt)

    def test_ocr_provider_extracts_identifiers_from_rendered_page_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "scan.pdf"
            pdf_path.write_bytes(b"fake scanned pdf")

            class FakePixmap:
                def tobytes(self, image_format: str) -> bytes:
                    self.last_format = image_format
                    return b"png-bytes"

            class FakePage:
                def get_pixmap(self) -> FakePixmap:
                    return FakePixmap()

            class FakeDocument:
                def __iter__(self):
                    return iter([FakePage()])

                def close(self) -> None:
                    self.closed = True

            class FakeFitz:
                @staticmethod
                def open(path: str) -> FakeDocument:
                    return FakeDocument()

            class FakeImageModule:
                @staticmethod
                def open(stream) -> object:
                    return {"opened": True, "stream_type": type(stream).__name__}

            class FakeTesseract:
                class Output:
                    DICT = "DICT"

                @staticmethod
                def image_to_data(image, output_type=None):
                    return {
                        "text": ["Scanned", "LC-0038", "PDL-26-0042"],
                        "conf": ["91", "99", "97"],
                    }

            with patch("project.documents.providers._load_pymupdf_module", return_value=FakeFitz()):
                with patch("project.documents.providers._load_pil_image_module", return_value=FakeImageModule()):
                    with patch("project.documents.providers._load_pytesseract_module", return_value=FakeTesseract()):
                        analysis = OCRSavedDocumentAnalysisProvider().analyze(
                            saved_document=SavedDocument(
                                saved_document_id="doc-2",
                                mail_id="mail-1",
                                attachment_name="scan.pdf",
                                normalized_filename="scan.pdf",
                                destination_path=str(pdf_path),
                                file_sha256="b" * 64,
                                save_decision="saved_new",
                            )
                        )

        self.assertEqual(analysis.analysis_basis, "ocr_text")
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0038")
        self.assertEqual(analysis.extracted_lc_sc_confidence, 0.99)
        self.assertEqual(analysis.extracted_pi_number, "PDL-26-0042")
        self.assertEqual(analysis.extracted_pi_confidence, 0.97)
        self.assertAlmostEqual(analysis.clause_confidence, 0.9567, places=4)

    def test_layered_provider_falls_back_to_ocr_when_text_analysis_has_no_identifiers(self) -> None:
        saved_document = SavedDocument(
            saved_document_id="doc-3",
            mail_id="mail-1",
            attachment_name="scan.pdf",
            normalized_filename="scan.pdf",
            destination_path="C:/docs/scan.pdf",
            file_sha256="c" * 64,
            save_decision="saved_new",
        )

        class EmptyTextProvider:
            def analyze(self, *, saved_document: SavedDocument):
                del saved_document
                from project.documents import SavedDocumentAnalysis

                return SavedDocumentAnalysis(analysis_basis="pymupdf_text_empty")

        class OCRProvider:
            def analyze(self, *, saved_document: SavedDocument):
                del saved_document
                from project.documents import SavedDocumentAnalysis

                return SavedDocumentAnalysis(
                    analysis_basis="ocr_text",
                    extracted_pi_number="PDL-26-0042",
                    extracted_pi_confidence=0.97,
                    clause_confidence=0.97,
                )

        analysis = LayeredSavedDocumentAnalysisProvider(
            text_provider=EmptyTextProvider(),
            ocr_provider=OCRProvider(),
        ).analyze(saved_document=saved_document)

        self.assertEqual(analysis.analysis_basis, "ocr_text")
        self.assertEqual(analysis.extracted_pi_number, "PDL-26-0042")


if __name__ == "__main__":
    unittest.main()
