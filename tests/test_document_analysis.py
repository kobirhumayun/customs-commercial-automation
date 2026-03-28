from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project.documents import JsonManifestSavedDocumentAnalysisProvider, PyMuPDFSavedDocumentAnalysisProvider
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
        self.assertEqual(analysis.extracted_pi_number, "PDL-26-0042-R1")
        self.assertEqual(analysis.clause_related_lc_sc_number, "LC-0038")
        self.assertEqual(analysis.clause_confidence, 1.0)
        self.assertIn("LC-0038", analysis.clause_excerpt)


if __name__ == "__main__":
    unittest.main()
