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

    def test_layered_raw_report_degrades_gracefully_when_img2table_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "hybrid-img2table-error.pdf"
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

            class FakeImg2TablePDF:
                def __init__(self, src: str):
                    self.src = src

                @staticmethod
                def extract_tables(**kwargs):
                    del kwargs
                    raise RuntimeError("img2table exploded")

            class FakeImg2TableOCR:
                def __init__(self, **kwargs):
                    self.kwargs = kwargs

            with patch("project.documents.providers._load_pymupdf_module", return_value=FakeFitz()):
                with patch("project.documents.providers._load_pdfplumber_module", return_value=FakePDFPlumber()):
                    with patch("project.documents.providers._load_img2table_pdf_class", return_value=FakeImg2TablePDF):
                        with patch(
                            "project.documents.providers._load_img2table_tesseract_ocr_class",
                            return_value=FakeImg2TableOCR,
                        ):
                            with patch("project.documents.providers._load_pil_image_module", return_value=FakeImageModule()):
                                with patch("project.documents.providers._load_pytesseract_module", return_value=FakeTesseract()):
                                    report = extract_saved_document_raw_report(
                                        saved_document=SavedDocument(
                                            saved_document_id="doc-raw-2b",
                                            mail_id="mail-1",
                                            attachment_name="hybrid-img2table-error.pdf",
                                            normalized_filename="hybrid-img2table-error.pdf",
                                            destination_path=str(pdf_path),
                                            file_sha256="b" * 64,
                                            save_decision="saved_new",
                                        ),
                                        mode="layered",
                                    )

        self.assertEqual(report["page_count"], 2)
        self.assertEqual(report["pages"][0]["selected_source"], "text")
        self.assertEqual(report["pages"][1]["selected_source"], "ocr")
        self.assertEqual(report["categories"]["img2table"]["status"], "error")
        self.assertIn("img2table exploded", report["categories"]["img2table"]["error"])
        self.assertEqual(FakeTesseract.call_count, 1)
        self.assertIn("Scanned PDL-26-0042", report["combined_text"])

    def test_layered_raw_report_surfaces_img2table_category_before_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "scanned-table-layered.pdf"
            pdf_path.write_bytes(b"fake hybrid pdf")

            class FakePixmap:
                @staticmethod
                def tobytes(image_format: str) -> bytes:
                    self.assertEqual(image_format, "png")
                    return b"png-bytes"

            class FakePage:
                @staticmethod
                def get_text(mode: str):
                    if mode == "words":
                        return []
                    if mode == "text":
                        return ""
                    raise AssertionError(f"Unexpected mode: {mode}")

                @staticmethod
                def get_pixmap():
                    raise AssertionError("OCR should not run when img2table produced table evidence.")

            class FakeDocument:
                def __iter__(self):
                    return iter([FakePage()])

                def __len__(self):
                    return 1

                def __getitem__(self, index: int):
                    self.assertEqual(index, 0)
                    return FakePage()

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
                pages = [FakePDFPage()]

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

            class FakePDFPlumber:
                @staticmethod
                def open(path: str) -> FakePDF:
                    self.assertEqual(path, str(pdf_path))
                    return FakePDF()

            class FakeValues:
                @staticmethod
                def tolist():
                    return [["Reference", "Value"], ["PI No.", "PDL-26-0042"]]

            class FakeDataFrame:
                values = FakeValues()

                def fillna(self, _value: str):
                    return self

            class FakeExtractedTable:
                df = FakeDataFrame()

            class FakeImg2TablePDF:
                def __init__(self, src: str):
                    self.src = src

                @staticmethod
                def extract_tables(**kwargs):
                    _ = kwargs["ocr"]
                    return {0: [FakeExtractedTable()]}

            class FakeImg2TableOCR:
                def __init__(self, **kwargs):
                    self.kwargs = kwargs

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
                    return {"text": ["fallback"], "conf": ["50"]}

            with patch("project.documents.providers._load_pymupdf_module", return_value=FakeFitz()):
                with patch("project.documents.providers._load_pdfplumber_module", return_value=FakePDFPlumber()):
                    with patch("project.documents.providers._load_img2table_pdf_class", return_value=FakeImg2TablePDF):
                        with patch("project.documents.providers._load_img2table_tesseract_ocr_class", return_value=FakeImg2TableOCR):
                            with patch("project.documents.providers._load_pil_image_module", return_value=FakeImageModule()):
                                with patch("project.documents.providers._load_pytesseract_module", return_value=FakeTesseract()):
                                    report = extract_saved_document_raw_report(
                                        saved_document=SavedDocument(
                                            saved_document_id="doc-raw-img2table",
                                            mail_id="mail-1",
                                            attachment_name="scanned-table-layered.pdf",
                                            normalized_filename="scanned-table-layered.pdf",
                                            destination_path=str(pdf_path),
                                            file_sha256="g" * 64,
                                            save_decision="saved_new",
                                        ),
                                        mode="layered",
                                    )

        self.assertEqual(report["page_count"], 1)
        self.assertEqual(report["pages"][0]["selected_source"], "img2table")
        self.assertFalse(report["pages"][0]["ocr_attempted"])
        self.assertEqual(report["pages"][0]["img2table"]["combined_text"], "Reference | Value\nPI No. | PDL-26-0042")
        self.assertEqual(report["categories"]["img2table"]["mode"], "img2table")
        self.assertEqual(FakeTesseract.call_count, 0)
        self.assertIn("PDL-26-0042", report["combined_text"])

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

    def test_raw_img2table_report_supports_table_mode_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "img2table-mode.pdf"
            pdf_path.write_bytes(b"fake scanned table pdf")

            class FakeValues:
                @staticmethod
                def tolist():
                    return [["L/C No.", "LC-0038"]]

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
                    report = extract_saved_document_raw_report(
                        saved_document=SavedDocument(
                            saved_document_id="doc-raw-img2table-mode",
                            mail_id="mail-1",
                            attachment_name="img2table-mode.pdf",
                            normalized_filename="img2table-mode.pdf",
                            destination_path=str(pdf_path),
                            file_sha256="h" * 64,
                            save_decision="saved_new",
                        ),
                        mode="img2table",
                    )

        self.assertEqual(report["mode"], "img2table")
        self.assertEqual(report["page_count"], 1)
        self.assertEqual(report["pages"][0]["page_number"], 1)
        self.assertEqual(report["combined_text"], "L/C No. | LC-0038")

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

    def test_json_manifest_provider_loads_ud_specific_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "document-analysis.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "normalized_filename": "UD-LC-0043-ANANTA.pdf",
                            "document_number": "UD-LC-0043-ANANTA",
                            "document_number_confidence": 0.99,
                            "document_date": "2026-04-01",
                            "document_date_confidence": 0.98,
                            "lc_sc_number": "LC-0043",
                            "lc_sc_number_confidence": 1.0,
                            "quantity": "1000",
                            "quantity_unit": "YDS",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            analysis = JsonManifestSavedDocumentAnalysisProvider(manifest_path).analyze(
                saved_document=SavedDocument(
                    saved_document_id="doc-1",
                    mail_id="mail-1",
                    attachment_name="UD-LC-0043-ANANTA.pdf",
                    normalized_filename="UD-LC-0043-ANANTA.pdf",
                    destination_path="C:/docs/UD-LC-0043-ANANTA.pdf",
                    file_sha256="a" * 64,
                    save_decision="saved_new",
                )
            )

        self.assertEqual(analysis.analysis_basis, "json_manifest")
        self.assertEqual(analysis.extracted_document_number, "UD-LC-0043-ANANTA")
        self.assertEqual(analysis.extracted_document_date, "2026-04-01")
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0043")
        self.assertEqual(analysis.extracted_quantity, "1000")
        self.assertEqual(analysis.extracted_quantity_unit, "YDS")

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

    def test_pdfplumber_provider_extracts_ud_specific_fields_from_table_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "ud-table.pdf"
            pdf_path.write_bytes(b"fake ud table pdf")

            class FakePage:
                @staticmethod
                def extract_tables():
                    return [
                        [
                            ["Field", "Value"],
                            ["UD No.", "UD-LC-0043-ANANTA"],
                            ["UD Date", "01-Apr-2026"],
                            ["L/C No.", "LC-0043"],
                            ["Quantity", "1,000 Yards"],
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
                        saved_document_id="doc-ud-table",
                        mail_id="mail-1",
                        attachment_name="ud-table.pdf",
                        normalized_filename="ud-table.pdf",
                        destination_path=str(pdf_path),
                        file_sha256="u" * 64,
                        save_decision="saved_new",
                    )
                )

        self.assertEqual(analysis.analysis_basis, "pdfplumber_table")
        self.assertEqual(analysis.extracted_document_number, "UD-LC-0043-ANANTA")
        self.assertEqual(analysis.extracted_document_date, "2026-04-01")
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0043")
        self.assertEqual(analysis.extracted_quantity, "1000")
        self.assertEqual(analysis.extracted_quantity_unit, "YDS")

    def test_pdfplumber_provider_extracts_ud_specific_fields_from_table_rows_with_spacing_variants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "ud-table-variant.pdf"
            pdf_path.write_bytes(b"fake ud table variant pdf")

            class FakePage:
                @staticmethod
                def extract_tables():
                    return [
                        [
                            ["Field", "Value"],
                            ["UD Number", "ud lc 0043 ananta"],
                            ["UD Date", "01/04/2026"],
                            ["L/C Number", "LC 0043"],
                            ["Qty", "2,500 Mtrs"],
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
                        saved_document_id="doc-ud-table-variant",
                        mail_id="mail-1",
                        attachment_name="ud-table-variant.pdf",
                        normalized_filename="ud-table-variant.pdf",
                        destination_path=str(pdf_path),
                        file_sha256="v" * 64,
                        save_decision="saved_new",
                    )
                )

        self.assertEqual(analysis.analysis_basis, "pdfplumber_table")
        self.assertEqual(analysis.extracted_document_number, "UD-LC-0043-ANANTA")
        self.assertEqual(analysis.extracted_document_date, "2026-04-01")
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0043")
        self.assertEqual(analysis.extracted_quantity, "2500")
        self.assertEqual(analysis.extracted_quantity_unit, "MTR")

    def test_pdfplumber_provider_prefers_ud_date_over_lc_issue_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "ud-table-with-lc-date.pdf"
            pdf_path.write_bytes(b"fake ud table with lc date pdf")

            class FakePage:
                @staticmethod
                def extract_tables():
                    return [
                        [
                            ["Field", "Value"],
                            ["UD No.", "UD-LC-0043-ANANTA"],
                            ["L/C Issue Date", "2026-01-10"],
                            ["UD Date", "01-Apr-2026"],
                            ["L/C No.", "LC-0043"],
                            ["Quantity", "1,000 Yards"],
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
                        saved_document_id="doc-ud-table-lc-date",
                        mail_id="mail-1",
                        attachment_name="ud-table-with-lc-date.pdf",
                        normalized_filename="ud-table-with-lc-date.pdf",
                        destination_path=str(pdf_path),
                        file_sha256="y" * 64,
                        save_decision="saved_new",
                    )
                )

        self.assertEqual(analysis.analysis_basis, "pdfplumber_table")
        self.assertEqual(analysis.extracted_document_number, "UD-LC-0043-ANANTA")
        self.assertEqual(analysis.extracted_document_date, "2026-04-01")
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0043")
        self.assertEqual(analysis.extracted_quantity, "1000")
        self.assertEqual(analysis.extracted_quantity_unit, "YDS")

    def test_pdfplumber_provider_does_not_use_lc_issue_date_as_ud_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "ud-table-lc-date-only.pdf"
            pdf_path.write_bytes(b"fake ud table lc date only pdf")

            class FakePage:
                @staticmethod
                def extract_tables():
                    return [
                        [
                            ["Field", "Value"],
                            ["UD No.", "UD-LC-0043-ANANTA"],
                            ["L/C Issue Date", "2026-01-10"],
                            ["L/C No.", "LC-0043"],
                            ["Quantity", "1,000 Yards"],
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
                        saved_document_id="doc-ud-table-lc-date-only",
                        mail_id="mail-1",
                        attachment_name="ud-table-lc-date-only.pdf",
                        normalized_filename="ud-table-lc-date-only.pdf",
                        destination_path=str(pdf_path),
                        file_sha256="1" * 64,
                        save_decision="saved_new",
                    )
                )

        self.assertEqual(analysis.analysis_basis, "pdfplumber_table")
        self.assertEqual(analysis.extracted_document_number, "UD-LC-0043-ANANTA")
        self.assertIsNone(analysis.extracted_document_date)
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0043")
        self.assertEqual(analysis.extracted_quantity, "1000")
        self.assertEqual(analysis.extracted_quantity_unit, "YDS")

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

    def test_pymupdf_provider_extracts_ud_specific_fields_from_saved_pdf_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "saved-ud.pdf"
            pdf_path.write_bytes(b"fake ud pdf bytes")

            class FakePage:
                def get_text(self, mode: str) -> str:
                    self.last_mode = mode
                    return (
                        "UD No: UD-LC-0043-ANANTA\n"
                        "UD Date: 01-Apr-2026\n"
                        "L/C No: LC-0043\n"
                        "Quantity: 1,000 Yards\n"
                    )

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
                        saved_document_id="doc-ud-text",
                        mail_id="mail-1",
                        attachment_name="saved-ud.pdf",
                        normalized_filename="saved-ud.pdf",
                        destination_path=str(pdf_path),
                        file_sha256="f" * 64,
                        save_decision="saved_new",
                    )
                )

        self.assertEqual(analysis.analysis_basis, "pymupdf_text")
        self.assertEqual(analysis.extracted_document_number, "UD-LC-0043-ANANTA")
        self.assertEqual(analysis.extracted_document_date, "2026-04-01")
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0043")
        self.assertEqual(analysis.extracted_quantity, "1000")
        self.assertEqual(analysis.extracted_quantity_unit, "YDS")
        self.assertEqual(analysis.extracted_document_number_provenance["page_number"], 1)

    def test_pymupdf_provider_prefers_ud_date_over_lc_issue_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "saved-ud-with-lc-date.pdf"
            pdf_path.write_bytes(b"fake ud pdf bytes")

            class FakePage:
                def get_text(self, mode: str) -> str:
                    self.last_mode = mode
                    return (
                        "UD No: UD-LC-0043-ANANTA\n"
                        "L/C Issue Date: 2026-01-10\n"
                        "UD Date: 01-Apr-2026\n"
                        "L/C No: LC-0043\n"
                        "Quantity: 1,000 Yards\n"
                    )

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
                        saved_document_id="doc-ud-text-lc-date",
                        mail_id="mail-1",
                        attachment_name="saved-ud-with-lc-date.pdf",
                        normalized_filename="saved-ud-with-lc-date.pdf",
                        destination_path=str(pdf_path),
                        file_sha256="w" * 64,
                        save_decision="saved_new",
                    )
                )

        self.assertEqual(analysis.analysis_basis, "pymupdf_text")
        self.assertEqual(analysis.extracted_document_number, "UD-LC-0043-ANANTA")
        self.assertEqual(analysis.extracted_document_date, "2026-04-01")
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0043")

    def test_pymupdf_provider_handles_inline_ud_issue_date_without_overcapture(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "saved-ud-inline-issue-date.pdf"
            pdf_path.write_bytes(b"fake ud pdf bytes")

            class FakePage:
                def get_text(self, mode: str) -> str:
                    self.last_mode = mode
                    return (
                        "UD No: UD-LC-0043-ANANTA UD Issue Date: 01-Apr-2026 "
                        "L/C No: LC-0043 Quantity: 1,000 Yards"
                    )

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
                        saved_document_id="doc-ud-text-inline-issue-date",
                        mail_id="mail-1",
                        attachment_name="saved-ud-inline-issue-date.pdf",
                        normalized_filename="saved-ud-inline-issue-date.pdf",
                        destination_path=str(pdf_path),
                        file_sha256="x" * 64,
                        save_decision="saved_new",
                    )
                )

        self.assertEqual(analysis.analysis_basis, "pymupdf_text")
        self.assertEqual(analysis.extracted_document_number, "UD-LC-0043-ANANTA")
        self.assertEqual(analysis.extracted_document_date, "2026-04-01")
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0043")
        self.assertEqual(analysis.extracted_quantity, "1000")
        self.assertEqual(analysis.extracted_quantity_unit, "YDS")

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

    def test_ocr_provider_extracts_ud_specific_fields_from_rendered_page_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "scan-ud.pdf"
            pdf_path.write_bytes(b"fake scanned ud pdf")

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
                        "text": [
                            "UD",
                            "No",
                            "UD-LC-0043-ANANTA",
                            "UD",
                            "Date",
                            "01-Apr-2026",
                            "L/C",
                            "No",
                            "LC-0043",
                            "Quantity",
                            "1000",
                            "Yards",
                        ],
                        "conf": ["95", "95", "99", "94", "94", "98", "93", "93", "99", "96", "96", "96"],
                    }

            with patch("project.documents.providers._load_pymupdf_module", return_value=FakeFitz()):
                with patch("project.documents.providers._load_pil_image_module", return_value=FakeImageModule()):
                    with patch("project.documents.providers._load_pytesseract_module", return_value=FakeTesseract()):
                        analysis = OCRSavedDocumentAnalysisProvider().analyze(
                            saved_document=SavedDocument(
                                saved_document_id="doc-ud-ocr",
                                mail_id="mail-1",
                                attachment_name="scan-ud.pdf",
                                normalized_filename="scan-ud.pdf",
                                destination_path=str(pdf_path),
                                file_sha256="g" * 64,
                                save_decision="saved_new",
                            )
                        )

        self.assertEqual(analysis.analysis_basis, "ocr_text")
        self.assertEqual(analysis.extracted_document_number, "UD-LC-0043-ANANTA")
        self.assertEqual(analysis.extracted_document_date, "2026-04-01")
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0043")
        self.assertEqual(analysis.extracted_quantity, "1000")
        self.assertEqual(analysis.extracted_quantity_unit, "YDS")
        self.assertEqual(analysis.extracted_document_number_provenance["extraction_method"], "ocr")

    def test_ocr_provider_extracts_ud_specific_fields_from_rendered_page_data_with_number_and_qty_variants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "scan-ud-variant.pdf"
            pdf_path.write_bytes(b"fake scanned ud variant pdf")

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
                        "text": [
                            "UD",
                            "Number",
                            "UD",
                            "LC",
                            "0043",
                            "ANANTA",
                            "UD",
                            "Date",
                            "01/04/2026",
                            "L/C",
                            "Number",
                            "LC",
                            "0043",
                            "Qty",
                            "2500",
                            "Mtrs",
                        ],
                        "conf": [
                            "95",
                            "95",
                            "99",
                            "99",
                            "99",
                            "99",
                            "94",
                            "94",
                            "98",
                            "93",
                            "93",
                            "99",
                            "99",
                            "96",
                            "96",
                            "96",
                        ],
                    }

            with patch("project.documents.providers._load_pymupdf_module", return_value=FakeFitz()):
                with patch("project.documents.providers._load_pil_image_module", return_value=FakeImageModule()):
                    with patch("project.documents.providers._load_pytesseract_module", return_value=FakeTesseract()):
                        analysis = OCRSavedDocumentAnalysisProvider().analyze(
                            saved_document=SavedDocument(
                                saved_document_id="doc-ud-ocr-variant",
                                mail_id="mail-1",
                                attachment_name="scan-ud-variant.pdf",
                                normalized_filename="scan-ud-variant.pdf",
                                destination_path=str(pdf_path),
                                file_sha256="h" * 64,
                                save_decision="saved_new",
                            )
                        )

        self.assertEqual(analysis.analysis_basis, "ocr_text")
        self.assertEqual(analysis.extracted_document_number, "UD-LC-0043-ANANTA")
        self.assertEqual(analysis.extracted_document_date, "2026-04-01")
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0043")
        self.assertEqual(analysis.extracted_quantity, "2500")
        self.assertEqual(analysis.extracted_quantity_unit, "MTR")
        self.assertEqual(analysis.extracted_document_number_provenance["extraction_method"], "ocr")

    def test_ocr_provider_prefers_ud_date_over_lc_issue_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "scan-ud-with-lc-date.pdf"
            pdf_path.write_bytes(b"fake scanned ud with lc date pdf")

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
                        "text": [
                            "UD",
                            "No",
                            "UD-LC-0043-ANANTA",
                            "L/C",
                            "Issue",
                            "Date",
                            "2026-01-10",
                            "UD",
                            "Date",
                            "01-Apr-2026",
                            "L/C",
                            "No",
                            "LC-0043",
                            "Quantity",
                            "1000",
                            "Yards",
                        ],
                        "conf": [
                            "95",
                            "95",
                            "99",
                            "94",
                            "94",
                            "94",
                            "98",
                            "94",
                            "94",
                            "98",
                            "93",
                            "93",
                            "99",
                            "96",
                            "96",
                            "96",
                        ],
                    }

            with patch("project.documents.providers._load_pymupdf_module", return_value=FakeFitz()):
                with patch("project.documents.providers._load_pil_image_module", return_value=FakeImageModule()):
                    with patch("project.documents.providers._load_pytesseract_module", return_value=FakeTesseract()):
                        analysis = OCRSavedDocumentAnalysisProvider().analyze(
                            saved_document=SavedDocument(
                                saved_document_id="doc-ud-ocr-lc-date",
                                mail_id="mail-1",
                                attachment_name="scan-ud-with-lc-date.pdf",
                                normalized_filename="scan-ud-with-lc-date.pdf",
                                destination_path=str(pdf_path),
                                file_sha256="z" * 64,
                                save_decision="saved_new",
                            )
                        )

        self.assertEqual(analysis.analysis_basis, "ocr_text")
        self.assertEqual(analysis.extracted_document_number, "UD-LC-0043-ANANTA")
        self.assertEqual(analysis.extracted_document_date, "2026-04-01")
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0043")
        self.assertEqual(analysis.extracted_quantity, "1000")
        self.assertEqual(analysis.extracted_quantity_unit, "YDS")

    def test_ocr_provider_does_not_use_lc_issue_date_as_ud_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "scan-ud-lc-date-only.pdf"
            pdf_path.write_bytes(b"fake scanned ud lc date only pdf")

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
                        "text": [
                            "UD",
                            "No",
                            "UD-LC-0043-ANANTA",
                            "L/C",
                            "Issue",
                            "Date",
                            "2026-01-10",
                            "L/C",
                            "No",
                            "LC-0043",
                            "Quantity",
                            "1000",
                            "Yards",
                        ],
                        "conf": [
                            "95",
                            "95",
                            "99",
                            "94",
                            "94",
                            "94",
                            "98",
                            "93",
                            "93",
                            "99",
                            "96",
                            "96",
                            "96",
                        ],
                    }

            with patch("project.documents.providers._load_pymupdf_module", return_value=FakeFitz()):
                with patch("project.documents.providers._load_pil_image_module", return_value=FakeImageModule()):
                    with patch("project.documents.providers._load_pytesseract_module", return_value=FakeTesseract()):
                        analysis = OCRSavedDocumentAnalysisProvider().analyze(
                            saved_document=SavedDocument(
                                saved_document_id="doc-ud-ocr-lc-date-only",
                                mail_id="mail-1",
                                attachment_name="scan-ud-lc-date-only.pdf",
                                normalized_filename="scan-ud-lc-date-only.pdf",
                                destination_path=str(pdf_path),
                                file_sha256="2" * 64,
                                save_decision="saved_new",
                            )
                        )

        self.assertEqual(analysis.analysis_basis, "ocr_text")
        self.assertEqual(analysis.extracted_document_number, "UD-LC-0043-ANANTA")
        self.assertIsNone(analysis.extracted_document_date)
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0043")
        self.assertEqual(analysis.extracted_quantity, "1000")
        self.assertEqual(analysis.extracted_quantity_unit, "YDS")

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

    def test_layered_provider_uses_ocr_to_complete_partial_ud_fields(self) -> None:
        saved_document = SavedDocument(
            saved_document_id="doc-5",
            mail_id="mail-1",
            attachment_name="ud-partial.pdf",
            normalized_filename="ud-partial.pdf",
            destination_path="C:/docs/ud-partial.pdf",
            file_sha256="i" * 64,
            save_decision="saved_new",
        )

        class TextProvider:
            def analyze(self, *, saved_document: SavedDocument):
                del saved_document
                from project.documents import SavedDocumentAnalysis

                return SavedDocumentAnalysis(
                    analysis_basis="pymupdf_text",
                    extracted_document_number="UD-LC-0043-ANANTA",
                    extracted_document_number_confidence=1.0,
                    extracted_document_number_provenance={
                        "page_number": 1,
                        "extraction_method": "plain_text",
                        "confidence": 1.0,
                    },
                    extracted_lc_sc_number="LC-0043",
                    extracted_lc_sc_confidence=1.0,
                    extracted_lc_sc_provenance={
                        "page_number": 1,
                        "extraction_method": "plain_text",
                        "confidence": 1.0,
                    },
                )

        class EmptyTableProvider:
            def analyze(self, *, saved_document: SavedDocument):
                del saved_document
                from project.documents import SavedDocumentAnalysis

                return SavedDocumentAnalysis(analysis_basis="pdfplumber_table_empty")

        class OCRProvider:
            def analyze(self, *, saved_document: SavedDocument):
                del saved_document
                from project.documents import SavedDocumentAnalysis

                return SavedDocumentAnalysis(
                    analysis_basis="ocr_text",
                    extracted_document_date="2026-04-01",
                    extracted_document_date_confidence=0.98,
                    extracted_document_date_provenance={
                        "page_number": 1,
                        "extraction_method": "ocr",
                        "confidence": 0.98,
                    },
                    extracted_quantity="1000",
                    extracted_quantity_unit="YDS",
                    extracted_quantity_provenance={
                        "page_number": 1,
                        "extraction_method": "ocr",
                        "confidence": 0.96,
                    },
                )

        analysis = LayeredSavedDocumentAnalysisProvider(
            text_provider=TextProvider(),
            table_provider=EmptyTableProvider(),
            ocr_provider=OCRProvider(),
        ).analyze(saved_document=saved_document)

        self.assertEqual(analysis.analysis_basis, "pymupdf_text+ocr_text")
        self.assertEqual(analysis.extracted_document_number, "UD-LC-0043-ANANTA")
        self.assertEqual(analysis.extracted_lc_sc_number, "LC-0043")
        self.assertEqual(analysis.extracted_document_date, "2026-04-01")
        self.assertEqual(analysis.extracted_quantity, "1000")
        self.assertEqual(analysis.extracted_quantity_unit, "YDS")
        self.assertEqual(analysis.extracted_document_number_provenance["extraction_method"], "plain_text")
        self.assertEqual(analysis.extracted_quantity_provenance["extraction_method"], "ocr")

    def test_layered_provider_skips_ocr_when_ud_bundle_is_already_complete(self) -> None:
        saved_document = SavedDocument(
            saved_document_id="doc-6",
            mail_id="mail-1",
            attachment_name="ud-complete.pdf",
            normalized_filename="ud-complete.pdf",
            destination_path="C:/docs/ud-complete.pdf",
            file_sha256="j" * 64,
            save_decision="saved_new",
        )

        class TextProvider:
            def analyze(self, *, saved_document: SavedDocument):
                del saved_document
                from project.documents import SavedDocumentAnalysis

                return SavedDocumentAnalysis(
                    analysis_basis="pymupdf_text",
                    extracted_document_number="UD-LC-0043-ANANTA",
                    extracted_lc_sc_number="LC-0043",
                    extracted_document_date="2026-04-01",
                    extracted_quantity="1000",
                    extracted_quantity_unit="YDS",
                )

        class EmptyTableProvider:
            def analyze(self, *, saved_document: SavedDocument):
                del saved_document
                from project.documents import SavedDocumentAnalysis

                return SavedDocumentAnalysis(analysis_basis="pdfplumber_table_empty")

        class OCRProvider:
            def analyze(self, *, saved_document: SavedDocument):
                raise AssertionError("OCR fallback should not run when the UD bundle is already complete.")

        analysis = LayeredSavedDocumentAnalysisProvider(
            text_provider=TextProvider(),
            table_provider=EmptyTableProvider(),
            ocr_provider=OCRProvider(),
        ).analyze(saved_document=saved_document)

        self.assertEqual(analysis.analysis_basis, "pymupdf_text")
        self.assertEqual(analysis.extracted_document_number, "UD-LC-0043-ANANTA")
        self.assertEqual(analysis.extracted_document_date, "2026-04-01")
        self.assertEqual(analysis.extracted_quantity, "1000")
        self.assertEqual(analysis.extracted_quantity_unit, "YDS")


if __name__ == "__main__":
    unittest.main()
