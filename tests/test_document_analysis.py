from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project.documents import (
    extract_saved_document_raw_report,
    Img2TableSavedDocumentAnalysisProvider,
    JsonManifestSavedDocumentAnalysisProvider,
    LayeredSavedDocumentAnalysisProvider,
    LayeredTableSavedDocumentAnalysisProvider,
    OCRSavedDocumentAnalysisProvider,
    PDFPlumberSavedDocumentAnalysisProvider,
    PyMuPDFSavedDocumentAnalysisProvider,
)
from project.models import SavedDocument


class SavedDocumentAnalysisProviderTests(unittest.TestCase):
    def test_raw_text_report_reconstructs_visual_line_despite_large_gap_split(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "visual-gap.pdf"
            pdf_path.write_bytes(b"fake pdf bytes")

            class FakePage:
                @staticmethod
                def get_text(mode: str):
                    if mode == "words":
                        return [
                            (10, 10, 50, 20, "Invoice", 0, 0, 0),
                            (160, 10, 220, 20, "LC-0038", 0, 1, 0),
                            (260, 10, 340, 20, "PDL-26-42", 0, 1, 1),
                            (10, 40, 40, 50, "Next", 0, 2, 0),
                        ]
                    if mode == "text":
                        return "fallback text"
                    raise AssertionError(f"Unexpected mode: {mode}")

            class FakeDocument:
                def __iter__(self):
                    return iter([FakePage()])

                def close(self) -> None:
                    return None

            class FakeFitz:
                @staticmethod
                def open(path: str) -> FakeDocument:
                    self.assertEqual(path, str(pdf_path))
                    return FakeDocument()

            with patch("project.documents.providers._load_pymupdf_module", return_value=FakeFitz()):
                report = extract_saved_document_raw_report(
                    saved_document=SavedDocument(
                        saved_document_id="doc-raw-1",
                        mail_id="mail-1",
                        attachment_name="visual-gap.pdf",
                        normalized_filename="visual-gap.pdf",
                        destination_path=str(pdf_path),
                        file_sha256="a" * 64,
                        save_decision="saved_new",
                    ),
                    mode="text",
                )

        self.assertEqual(report["page_count"], 1)
        self.assertEqual(report["pages"][0]["strategy"], "words_reconstructed")
        self.assertEqual(report["pages"][0]["line_count"], 2)
        self.assertIn("Invoice    LC-0038    PDL-26-42", report["pages"][0]["text"])

    def test_layered_raw_report_uses_ocr_only_for_low_yield_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "hybrid.pdf"
            pdf_path.write_bytes(b"fake hybrid pdf")

            class FakePixmap:
                @staticmethod
                def tobytes(image_format: str) -> bytes:
                    self.assertEqual(image_format, "png")
                    return b"png-bytes"

            class FakeTextPage:
                @staticmethod
                def get_text(mode: str):
                    if mode == "words":
                        return [
                            (10, 10, 80, 20, "Layered", 0, 0, 0),
                            (90, 10, 140, 20, "page", 0, 0, 1),
                            (150, 10, 220, 20, "LC-0038", 0, 0, 2),
                        ]
                    if mode == "text":
                        return "Layered page LC-0038"
                    raise AssertionError(f"Unexpected mode: {mode}")

                @staticmethod
                def get_pixmap():
                    raise AssertionError("OCR should not run for a page with sufficient layered text.")

            class FakeScannedPage:
                @staticmethod
                def get_text(mode: str):
                    if mode == "words":
                        return []
                    if mode == "text":
                        return ""
                    raise AssertionError(f"Unexpected mode: {mode}")

                @staticmethod
                def get_pixmap():
                    return FakePixmap()

            class FakeDocument:
                def __iter__(self):
                    return iter([FakeTextPage(), FakeScannedPage()])

                def __len__(self):
                    return 2

                def __getitem__(self, index: int):
                    return [FakeTextPage(), FakeScannedPage()][index]

                def close(self) -> None:
                    return None

            class FakeFitz:
                @staticmethod
                def open(path: str) -> FakeDocument:
                    self.assertEqual(path, str(pdf_path))
                    return FakeDocument()

            class FakePDFPage:
                @staticmethod
                def extract_tables():
                    return []

            class FakePDF:
                pages = [FakePDFPage(), FakePDFPage()]

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

            class FakePDFPlumber:
                @staticmethod
                def open(path: str) -> FakePDF:
                    self.assertEqual(path, str(pdf_path))
                    return FakePDF()

            class FakeImageModule:
                @staticmethod
                def open(stream) -> object:
                    del stream
                    return {"opened": True}

            class FakeTesseract:
                call_count = 0

                class Output:
                    DICT = "DICT"

                @classmethod
                def image_to_data(cls, image, output_type=None):
                    del image, output_type
                    cls.call_count += 1
                    return {
                        "text": ["Scanned", "PDL-26-0042"],
                        "conf": ["92", "97"],
                    }

            with patch("project.documents.providers._load_pymupdf_module", return_value=FakeFitz()):
                with patch("project.documents.providers._load_pdfplumber_module", return_value=FakePDFPlumber()):
                    with patch("project.documents.providers._load_pil_image_module", return_value=FakeImageModule()):
                        with patch("project.documents.providers._load_pytesseract_module", return_value=FakeTesseract()):
                            report = extract_saved_document_raw_report(
                                saved_document=SavedDocument(
                                    saved_document_id="doc-raw-2",
                                    mail_id="mail-1",
                                    attachment_name="hybrid.pdf",
                                    normalized_filename="hybrid.pdf",
                                    destination_path=str(pdf_path),
                                    file_sha256="b" * 64,
                                    save_decision="saved_new",
                                ),
                                mode="layered",
                            )

        self.assertEqual(report["page_count"], 2)
        self.assertEqual(report["pages"][0]["selected_source"], "text")
        self.assertFalse(report["pages"][0]["ocr_attempted"])
        self.assertEqual(report["pages"][1]["selected_source"], "ocr")
        self.assertTrue(report["pages"][1]["ocr_attempted"])
        self.assertEqual(report["categories"]["ocr"]["attempted_page_numbers"], [2])
        self.assertEqual(FakeTesseract.call_count, 1)
        self.assertIn("Layered page LC-0038", report["combined_text"])
        self.assertIn("Scanned PDL-26-0042", report["combined_text"])

    def test_raw_report_search_respects_page_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "search.pdf"
            pdf_path.write_bytes(b"fake pdf bytes")

            class FakePage:
                def __init__(self, words):
                    self._words = words

                def get_text(self, mode: str):
                    if mode == "words":
                        return self._words
                    if mode == "text":
                        return ""
                    raise AssertionError(f"Unexpected mode: {mode}")

            class FakeDocument:
                def __iter__(self):
                    return iter(
                        [
                            FakePage([(10, 10, 50, 20, "alpha", 0, 0, 0)]),
                            FakePage([(10, 10, 50, 20, "target", 0, 0, 0)]),
                            FakePage([(10, 10, 50, 20, "target", 0, 0, 0)]),
                        ]
                    )

                def close(self) -> None:
                    return None

            class FakeFitz:
                @staticmethod
                def open(path: str) -> FakeDocument:
                    self.assertEqual(path, str(pdf_path))
                    return FakeDocument()

            with patch("project.documents.providers._load_pymupdf_module", return_value=FakeFitz()):
                report = extract_saved_document_raw_report(
                    saved_document=SavedDocument(
                        saved_document_id="doc-raw-3",
                        mail_id="mail-1",
                        attachment_name="search.pdf",
                        normalized_filename="search.pdf",
                        destination_path=str(pdf_path),
                        file_sha256="c" * 64,
                        save_decision="saved_new",
                    ),
                    mode="text",
                    search_text="target",
                    page_from=2,
                    page_to=2,
                )

        self.assertEqual(report["search"]["match_count"], 1)
        self.assertEqual(report["search"]["page_from"], 2)
        self.assertEqual(report["search"]["page_to"], 2)
        self.assertEqual(len(report["search"]["matches"]), 1)
        self.assertEqual(report["search"]["matches"][0]["page_number"], 2)

    def test_json_manifest_provider_matches_by_destination_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "document-analysis.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "destination_path": "C:/docs/supporting.pdf",
                            "extracted_pi_number": "PDL-26-0042",
                            "extracted_pi_page_number": 3,
                            "extracted_pi_extraction_method": "json_manifest",
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
        self.assertEqual(analysis.extracted_pi_provenance["page_number"], 3)
        self.assertEqual(analysis.extracted_pi_provenance["extraction_method"], "json_manifest")
        self.assertEqual(analysis.clause_provenance["confidence"], 0.99)

    def test_pdfplumber_provider_extracts_identifiers_and_amendment_from_table_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "table.pdf"
            pdf_path.write_bytes(b"fake table pdf")

            class FakePage:
                @staticmethod
                def extract_tables():
                    return [
                        [
                            ["Reference", "Value"],
                            ["L/C No.", "LC-0038"],
                            ["PI No.", "PDL-26-0042"],
                            ["Amendment No.", "05"],
                        ]
                    ]

            class FakePDF:
                pages = [FakePage()]

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

            class FakePDFPlumber:
                @staticmethod
                def open(path: str) -> FakePDF:
                    self.assertEqual(path, str(pdf_path))
                    return FakePDF()

            with patch("project.documents.providers._load_pdfplumber_module", return_value=FakePDFPlumber()):
                analysis = PDFPlumberSavedDocumentAnalysisProvider().analyze(
                    saved_document=SavedDocument(
                        saved_document_id="doc-table",
                        mail_id="mail-1",
                        attachment_name="table.pdf",
                        normalized_filename="table.pdf",
                        destination_path=str(pdf_path),
                        file_sha256="d" * 64,
                        save_decision="saved_new",
                    )
                )

        self.assertEqual(analysis.analysis_basis, "pdfplumber_table")
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0038")
        self.assertEqual(analysis.extracted_pi_number, "PDL-26-0042")
        self.assertEqual(analysis.extracted_amendment_number, "5")

    def test_img2table_provider_extracts_identifiers_from_scanned_table_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "scanned-table.pdf"
            pdf_path.write_bytes(b"fake scanned table pdf")

            class FakeValues:
                @staticmethod
                def tolist():
                    return [
                        ["Reference", "Value"],
                        ["L/C No.", "LC-0038"],
                        ["PI No.", "PDL-26-0042"],
                        ["Amendment No.", "05"],
                    ]

            class FakeDataFrame:
                values = FakeValues()

                def fillna(self, _value: str):
                    return self

            class FakeExtractedTable:
                df = FakeDataFrame()

            class FakePDF:
                def __init__(self, src: str):
                    self.src = src

                @staticmethod
                def extract_tables(**kwargs):
                    _ = kwargs["ocr"]
                    return {0: [FakeExtractedTable()]}

            class FakeOCR:
                def __init__(self, **kwargs):
                    self.kwargs = kwargs

            with patch("project.documents.providers._load_img2table_pdf_class", return_value=FakePDF):
                with patch("project.documents.providers._load_img2table_tesseract_ocr_class", return_value=FakeOCR):
                    analysis = Img2TableSavedDocumentAnalysisProvider().analyze(
                        saved_document=SavedDocument(
                            saved_document_id="doc-img2table",
                            mail_id="mail-1",
                            attachment_name="scanned-table.pdf",
                            normalized_filename="scanned-table.pdf",
                            destination_path=str(pdf_path),
                            file_sha256="e" * 64,
                            save_decision="saved_new",
                        )
                    )

        self.assertEqual(analysis.analysis_basis, "img2table_table")
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0038")
        self.assertEqual(analysis.extracted_pi_number, "PDL-26-0042")
        self.assertEqual(analysis.extracted_amendment_number, "5")
        self.assertEqual(analysis.extracted_lc_sc_provenance["page_number"], 1)
        self.assertEqual(analysis.extracted_lc_sc_provenance["extraction_method"], "img2table")

    def test_layered_table_provider_falls_back_to_img2table_when_pdfplumber_is_empty(self) -> None:
        saved_document = SavedDocument(
            saved_document_id="doc-table-fallback",
            mail_id="mail-1",
            attachment_name="scan.pdf",
            normalized_filename="scan.pdf",
            destination_path="C:/docs/scan.pdf",
            file_sha256="f" * 64,
            save_decision="saved_new",
        )

        class EmptyTableProvider:
            def analyze(self, *, saved_document: SavedDocument):
                del saved_document
                from project.documents import SavedDocumentAnalysis

                return SavedDocumentAnalysis(analysis_basis="pdfplumber_table_empty")

        class Img2TableProvider:
            def analyze(self, *, saved_document: SavedDocument):
                del saved_document
                from project.documents import SavedDocumentAnalysis

                return SavedDocumentAnalysis(
                    analysis_basis="img2table_table",
                    extracted_amendment_number="5",
                    extracted_amendment_provenance={
                        "page_number": 2,
                        "extraction_method": "img2table",
                        "confidence": 1.0,
                    },
                )

        analysis = LayeredTableSavedDocumentAnalysisProvider(
            primary_provider=EmptyTableProvider(),
            fallback_provider=Img2TableProvider(),
        ).analyze(saved_document=saved_document)

        self.assertEqual(analysis.analysis_basis, "img2table_table")
        self.assertEqual(analysis.extracted_amendment_number, "5")
        self.assertEqual(analysis.extracted_amendment_provenance["extraction_method"], "img2table")

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
        self.assertEqual(analysis.extracted_amendment_number, None)
        self.assertEqual(analysis.clause_related_lc_sc_number, "LC-0038")
        self.assertEqual(analysis.clause_confidence, 1.0)
        self.assertIn("LC-0038", analysis.clause_excerpt)
        self.assertEqual(analysis.extracted_lc_sc_provenance["page_number"], 1)
        self.assertEqual(analysis.extracted_lc_sc_provenance["extraction_method"], "plain_text")
        self.assertEqual(analysis.extracted_pi_provenance["page_number"], 1)

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
        self.assertEqual(analysis.extracted_lc_sc_provenance["page_number"], 1)
        self.assertEqual(analysis.extracted_lc_sc_provenance["extraction_method"], "ocr")
        self.assertEqual(analysis.extracted_pi_provenance["confidence"], 0.97)

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

    def test_layered_provider_merges_table_amendment_context_with_text_identifiers(self) -> None:
        saved_document = SavedDocument(
            saved_document_id="doc-4",
            mail_id="mail-1",
            attachment_name="amendment.pdf",
            normalized_filename="amendment.pdf",
            destination_path="C:/docs/amendment.pdf",
            file_sha256="e" * 64,
            save_decision="saved_new",
        )

        class TextProvider:
            def analyze(self, *, saved_document: SavedDocument):
                del saved_document
                from project.documents import SavedDocumentAnalysis

                return SavedDocumentAnalysis(
                    analysis_basis="pymupdf_text",
                    extracted_lc_sc_number="LC-0038",
                    extracted_lc_sc_confidence=1.0,
                    extracted_lc_sc_provenance={
                        "page_number": 1,
                        "extraction_method": "plain_text",
                        "confidence": 1.0,
                    },
                )

        class TableProvider:
            def analyze(self, *, saved_document: SavedDocument):
                del saved_document
                from project.documents import SavedDocumentAnalysis

                return SavedDocumentAnalysis(
                    analysis_basis="pdfplumber_table",
                    extracted_amendment_number="5",
                    extracted_amendment_provenance={
                        "page_number": 2,
                        "extraction_method": "table",
                        "confidence": 1.0,
                    },
                )

        class OCRProvider:
            def analyze(self, *, saved_document: SavedDocument):
                raise AssertionError("OCR fallback should not run when text-plus-table analysis is sufficient.")

        analysis = LayeredSavedDocumentAnalysisProvider(
            text_provider=TextProvider(),
            table_provider=TableProvider(),
            ocr_provider=OCRProvider(),
        ).analyze(saved_document=saved_document)

        self.assertEqual(analysis.analysis_basis, "pymupdf_text+pdfplumber_table")
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0038")
        self.assertEqual(analysis.extracted_amendment_number, "5")
        self.assertEqual(analysis.extracted_lc_sc_provenance["extraction_method"], "plain_text")
        self.assertEqual(analysis.extracted_amendment_provenance["extraction_method"], "table")


if __name__ == "__main__":
    unittest.main()
