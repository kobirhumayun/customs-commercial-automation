from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from project.cli import main
from project.reporting.schemas import REPORT_SCHEMA_VERSION
from project.workflows.import_btb_lc.extraction import (
    IMPORT_BTB_LC_EXTRACTION_SCHEMA_VERSION,
    ExtractedPage,
    _canonicalize_btb_number,
    _canonicalize_related_export_lc,
    extract_import_btb_lc_pdf,
    extract_import_btb_lc_path,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "import_btb_lc" / "representative_pdfs.json"
REPORTED_REGRESSION_FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "import_btb_lc"
    / "reported_regression_pdfs.json"
)
DEFAULT_SAMPLE_ROOT = Path(r"D:\customs-automation\workbooks\BTB LC for Extraction")
DEFAULT_REPORTED_REGRESSION_ROOT = Path(r"D:\customs-automation\BTB extraction test")


class RepresentativeImportBTBLCPDFTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.sample_root = Path(os.environ.get("BTB_LC_SAMPLE_DIR", DEFAULT_SAMPLE_ROOT))

    def test_0002228260403929(self) -> None:
        self._assert_representative_pdf("0002228260403929.pdf")

    def test_0002228260404700(self) -> None:
        self._assert_representative_pdf("0002228260404700.pdf")

    def test_0742260401049(self) -> None:
        self._assert_representative_pdf("0742260401049.pdf")

    def test_0742260401362(self) -> None:
        self._assert_representative_pdf("0742260401362.pdf")

    def test_1080260400315(self) -> None:
        self._assert_representative_pdf("1080260400315.pdf")

    def test_1080260400428(self) -> None:
        self._assert_representative_pdf("1080260400428.pdf")

    def test_3085260403867(self) -> None:
        self._assert_representative_pdf("3085260403867.pdf")

    def test_3085260404808(self) -> None:
        self._assert_representative_pdf("3085260404808.pdf")

    def test_411012607381_l(self) -> None:
        self._assert_representative_pdf("411012607381-L.pdf")

    def test_411012632898_l(self) -> None:
        self._assert_representative_pdf("411012632898-L.pdf")

    def _assert_representative_pdf(self, filename: str) -> None:
        source_path = self.sample_root / filename
        if not source_path.exists():
            self.skipTest(f"Representative PDF is not available: {source_path}")
        _assert_pdf_fixture(self, source_path, self.expected[filename])


class ReportedImportBTBLCRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected = json.loads(
            REPORTED_REGRESSION_FIXTURE_PATH.read_text(encoding="utf-8")
        )
        cls.sample_root = Path(
            os.environ.get(
                "BTB_LC_REPORTED_REGRESSION_DIR",
                DEFAULT_REPORTED_REGRESSION_ROOT,
            )
        )

    def test_0002228260404815(self) -> None:
        self._assert_reported_pdf("0002228260404815.pdf")

    def test_0742260401357(self) -> None:
        self._assert_reported_pdf("0742260401357.pdf")

    def test_0742260401365(self) -> None:
        self._assert_reported_pdf("0742260401365.pdf")

    def test_3085260404999(self) -> None:
        self._assert_reported_pdf("3085260404999.pdf")

    def test_3085260400651(self) -> None:
        self._assert_reported_pdf("3085260400651.pdf")

    def test_3085260400652(self) -> None:
        self._assert_reported_pdf("3085260400652.pdf")

    def test_3085260401001(self) -> None:
        self._assert_reported_pdf("3085260401001.pdf")

    def test_3085260401116(self) -> None:
        self._assert_reported_pdf("3085260401116.pdf")

    def test_3085260401317(self) -> None:
        self._assert_reported_pdf("3085260401317.pdf")

    def test_3085260401631(self) -> None:
        self._assert_reported_pdf("3085260401631.pdf")

    def test_3085260404791(self) -> None:
        self._assert_reported_pdf("3085260404791.pdf")

    def test_411012525148_l(self) -> None:
        self._assert_reported_pdf("411012525148-L.pdf")

    def _assert_reported_pdf(self, filename: str) -> None:
        source_path = self.sample_root / filename
        if not source_path.exists():
            self.skipTest(f"Reported regression PDF is not available: {source_path}")
        _assert_pdf_fixture(self, source_path, self.expected[filename])


class ImportBTBLCExtractionTests(unittest.TestCase):
    def test_all_bank_number_patterns_and_invalid_variants(self) -> None:
        valid_numbers = [
            "0742260401049",
            "0002228260403929",
            "1080260400315",
            "3085260403867",
            "411012607381-L",
        ]
        for value in valid_numbers:
            with self.subTest(valid=value):
                self.assertEqual(_canonicalize_btb_number(value, None), value)

        invalid_numbers = [
            "074226040104",
            "0743260401049",
            "0742260401049X",
            "0742 260401049",
            "000222826040392",
            "0002229260403929",
            "0002228260403929-L",
            "108026040031",
            "1081260400315",
            "1080260400315X",
            "308526040386",
            "3084260403867",
            "3085260403867X",
            "41101260738-L",
            "411022607381-L",
            "411012607381-X",
            "41101 2607381-L",
        ]
        for value in invalid_numbers:
            with self.subTest(invalid=value):
                self.assertIsNone(_canonicalize_btb_number(value, None))

    def test_missing_malformed_and_conflicting_fields_hard_block(self) -> None:
        cases = {
            "missing_pi": _sample_text(pi_text=""),
            "malformed_date": _sample_text(date_text="31/02/2026"),
            "malformed_pi": _sample_text(pi_text="BTL/26/001"),
            "conflicting_export_lc": _sample_text(
                related_text=(
                    "EXPORT L/C NO. 1234567890 DATE 01-01-2026 "
                    "EXPORT L/C NO. 9876543210 DATE 02-01-2026"
                )
            ),
        }
        expected_fields = {
            "missing_pi": "seller_pi_numbers",
            "malformed_date": "btb_lc_date",
            "malformed_pi": "seller_pi_numbers",
            "conflicting_export_lc": "related_export_lc_number",
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                artifact = _extract_synthetic(text)
                self.assertEqual(artifact["overall_extraction_decision"], "hard_block")
                self.assertEqual(
                    artifact["fields"][expected_fields[name]]["validation"]["status"],
                    "hard_block",
                )

    def test_ocr_fallback_and_confidence_thresholds(self) -> None:
        passing_provider = _StaticPageProvider(
            embedded=[
                ExtractedPage(1, "", "embedded_text", 1.0, False),
            ],
            ocr=[
                ExtractedPage(1, _sample_text(), "ocr", 0.99, False),
            ],
        )
        artifact = _extract_synthetic("", provider=passing_provider)
        self.assertEqual(artifact["overall_extraction_decision"], "pass")
        self.assertTrue(
            all(
                field.get("extraction_method") == "ocr"
                or all(value["extraction_method"] == "ocr" for value in field.get("values", []))
                for field in artifact["fields"].values()
            )
        )

        token_text = _sample_text()
        token_confidence_provider = _StaticPageProvider(
            embedded=[
                ExtractedPage(1, "", "embedded_text", 1.0, False),
            ],
            ocr=[
                ExtractedPage(
                    1,
                    token_text,
                    "ocr",
                    0.80,
                    False,
                    tuple((token, 0.99) for token in token_text.split()),
                ),
            ],
        )
        token_confidence_artifact = _extract_synthetic(
            "",
            provider=token_confidence_provider,
        )
        self.assertEqual(
            token_confidence_artifact["overall_extraction_decision"],
            "pass",
        )

        low_confidence_provider = _StaticPageProvider(
            embedded=[
                ExtractedPage(1, "", "embedded_text", 1.0, False),
            ],
            ocr=[
                ExtractedPage(1, _sample_text(), "ocr", 0.94, False),
            ],
        )
        blocked = _extract_synthetic("", provider=low_confidence_provider)
        self.assertEqual(blocked["overall_extraction_decision"], "hard_block")
        self.assertIn(
            "ocr_required_field_below_threshold",
            [item["code"] for item in blocked["hard_block_discrepancies"]],
        )

    def test_exact_decimals_dates_pi_patterns_and_related_lc_normalization(self) -> None:
        mtb_artifact = _extract_synthetic(
            _sample_text(
                btb_number="0002228260403929",
                date_text="07/06/2026",
                amount_text="USD54,209.950",
                pi_text="btl/26/0042",
            ),
            filename="0002228260403929.pdf",
        )
        self.assertEqual(mtb_artifact["fields"]["btb_lc_value"]["canonical"], "54209.950")
        self.assertEqual(mtb_artifact["fields"]["btb_lc_date"]["canonical"], "2026-06-07")
        self.assertEqual(
            mtb_artifact["fields"]["seller_pi_numbers"]["canonical"],
            ["BTL/26/0042"],
        )

        kyl_artifact = _extract_synthetic(_sample_text(pi_text="kyl/26/0042"))
        self.assertEqual(
            kyl_artifact["fields"]["seller_pi_numbers"]["canonical"],
            ["KYL/26/0042"],
        )
        self.assertEqual(
            _canonicalize_related_export_lc("lc  -0038", None),
            "LC-0038",
        )
        self.assertEqual(
            _canonicalize_related_export_lc("DPCBDA033739", "LC"),
            "LC-DPCBDA033739",
        )
        self.assertEqual(
            _canonicalize_related_export_lc("DPCBDA 033739", "LC"),
            "LC-DPCBDA-033739",
        )
        self.assertEqual(
            _canonicalize_related_export_lc(
                "PDL/INDOCHINE/2026/0002",
                "LC",
            ),
            "LC-PDL/INDOCHINE/2026/0002",
        )
        self.assertIsNone(
            _canonicalize_related_export_lc(
                "PDL//INDOCHINE/2026/0002",
                "LC",
            )
        )

        indian_grouped = _extract_synthetic(
            _sample_text(
                btb_number="0002228260404815",
                amount_text="USD1,04,173.400",
            ),
            filename="0002228260404815.pdf",
        )
        self.assertEqual(
            indian_grouped["fields"]["btb_lc_value"]["canonical"],
            "104173.400",
        )

        swift_zero_fraction = _extract_synthetic(
            _sample_text(
                btb_number="411012525148-L",
                amount_text="USD41379,",
                pi_text="KYL/26/0016",
                related_text=(
                    "EXPORT CONTRACT NUMBER/EXPORT L/C NUMBER: "
                    "DPCBD1166856 DATED: 30.12.2025"
                ),
            ).replace(
                "City Bank PLC. Trade Services Division",
                "Standard Chartered Bank",
            ),
            filename="411012525148-L.pdf",
        )
        self.assertEqual(swift_zero_fraction["overall_extraction_decision"], "pass")
        self.assertEqual(
            swift_zero_fraction["fields"]["btb_lc_value"]["raw"],
            "41379,",
        )
        self.assertEqual(
            swift_zero_fraction["fields"]["btb_lc_value"]["canonical"],
            "41379",
        )

        for malformed_amount in ("USD,", "USD41379,,", "USD41.37,"):
            with self.subTest(malformed_swift_amount=malformed_amount):
                malformed = _extract_synthetic(
                    _sample_text(
                        btb_number="411012525148-L",
                        amount_text=malformed_amount,
                        pi_text="KYL/26/0016",
                        related_text=(
                            "EXPORT CONTRACT NUMBER/EXPORT L/C NUMBER: "
                            "DPCBD1166856 DATED: 30.12.2025"
                        ),
                    ).replace(
                        "City Bank PLC. Trade Services Division",
                        "Standard Chartered Bank",
                    ),
                    filename="411012525148-L.pdf",
                )
                self.assertEqual(
                    malformed["fields"]["btb_lc_value"]["validation"]["status"],
                    "hard_block",
                )

    def test_brac_combined_clause_uses_first_reference(self) -> None:
        artifact = _extract_synthetic(
            _sample_text(
                btb_number="3085260400651",
                pi_text="KYL/26/0403",
                related_text=(
                    "ALL SHIPPING DOCUMENTS MUST BEAR THE L/C NUMBER WITH DATE "
                    "AND EXPORT SALES CONTRACT NO:21592604000048 "
                    "DAT:12-JAN-2026 AND SALES CONTRACT NO."
                    "VFL/TEDDY/2026/002 DATE 01-JAN-2026"
                ),
            ),
            filename="3085260400651.pdf",
        )

        field = artifact["fields"]["related_export_lc_number"]
        self.assertEqual(artifact["overall_extraction_decision"], "pass")
        self.assertEqual(field["canonical"], "LC-21592604000048")
        self.assertEqual(
            [(match["raw"], match["page_number"]) for match in field["matches"]],
            [("21592604000048", 1)],
        )

    def test_brac_authoritative_clause_uses_first_occurrence(self) -> None:
        artifact = _extract_synthetic(
            _sample_text(
                btb_number="3085260401116",
                pi_text="BTL/26/0826",
                related_text=(
                    "ALL SHIPPING DOCUMENTS MUST BEAR THE EXPORT SALES "
                    "CONTRACT NO. PDL/KENPARK/26/0001 DATED 29-JAN-2026. "
                    "ALL SHIPPING DOCUMENTS MUST BEAR THE EXPORT NO. "
                    "9999999999999 DATE 30-JAN-2026."
                ),
            ),
            filename="3085260401116.pdf",
        )

        field = artifact["fields"]["related_export_lc_number"]
        self.assertEqual(artifact["overall_extraction_decision"], "pass")
        self.assertEqual(field["canonical"], "LC-PDL/KENPARK/26/0001")
        self.assertEqual(
            [(match["raw"], match["page_number"]) for match in field["matches"]],
            [("PDL/KENPARK/26/0001", 1)],
        )

    def test_multiple_valid_pi_numbers_are_preserved_in_document_order(self) -> None:
        artifact = _extract_synthetic(
            _sample_text(
                pi_text=(
                    "BTL/26/3019 DATED 27.04.2026 "
                    "BTL/26/3018 DATED 27.04.2026 "
                    "BTL/26/3019"
                )
            )
        )

        self.assertEqual(artifact["overall_extraction_decision"], "pass")
        self.assertEqual(
            artifact["fields"]["seller_pi_numbers"]["canonical"],
            ["BTL/26/3019", "BTL/26/3018"],
        )
        self.assertEqual(
            len(artifact["fields"]["seller_pi_numbers"]["matches"]),
            2,
        )

    def test_valid_pi_does_not_hide_malformed_pi_like_value(self) -> None:
        artifact = _extract_synthetic(
            _sample_text(pi_text="BTL/26/3019 and KYL-26-1927")
        )

        self.assertEqual(artifact["overall_extraction_decision"], "hard_block")
        self.assertEqual(
            artifact["fields"]["seller_pi_numbers"]["canonical"],
            ["BTL/26/3019"],
        )
        self.assertEqual(
            artifact["fields"]["seller_pi_numbers"]["validation"]["code"],
            "import_pi_number_invalid",
        )

    def test_pi_candidates_from_appended_non_lc_page_are_ignored(self) -> None:
        provider = _StaticPageProvider(
            embedded=[
                ExtractedPage(
                    1,
                    _sample_text(
                        btb_number="3085260404999",
                        pi_text="BTL/26/4009",
                        related_text=(
                            "ALL SHIPPING DOCUMENTS MUST BEAR THE L/C NUMBER "
                            "WITH DATE AND EXPORT SALES CONTRACT NO. "
                            "DPCBD1183097 DATE 07-06-2026"
                        ),
                    ),
                    "embedded_text",
                    1.0,
                    True,
                ),
                ExtractedPage(
                    2,
                    "47A: Additional Conditions",
                    "embedded_text",
                    1.0,
                    True,
                ),
                ExtractedPage(
                    3,
                    "PROFORMA INVOICE NO. BTL/ 26/4009",
                    "ocr",
                    0.99,
                    False,
                ),
            ],
            ocr=[],
        )

        artifact = _extract_synthetic(
            "",
            filename="3085260404999.pdf",
            provider=provider,
        )

        self.assertEqual(artifact["overall_extraction_decision"], "pass")
        self.assertEqual(
            artifact["fields"]["seller_pi_numbers"]["canonical"],
            ["BTL/26/4009"],
        )
        self.assertEqual(
            {
                match["page_number"]
                for match in artifact["fields"]["seller_pi_numbers"]["matches"]
            },
            {1},
        )

    def test_filename_mismatch_is_warning_only_when_all_fields_pass(self) -> None:
        artifact = _extract_synthetic(_sample_text(), filename="scan.pdf")
        self.assertEqual(artifact["overall_extraction_decision"], "warning")
        self.assertEqual([item["code"] for item in artifact["warnings"]], ["import_filename_number_mismatch"])

        blocked = _extract_synthetic(_sample_text(pi_text=""), filename="scan.pdf")
        self.assertEqual(blocked["overall_extraction_decision"], "hard_block")
        self.assertEqual(blocked["warnings"], [])

    def test_repeated_execution_emits_equivalent_canonical_json(self) -> None:
        provider = _StaticPageProvider(
            embedded=[
                ExtractedPage(1, _sample_text(), "embedded_text", 1.0, True),
            ],
            ocr=[],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "0742260401049.pdf"
            source_path.write_bytes(b"%PDF-1.4\nsynthetic\n")
            first_output = root / "first"
            second_output = root / "second"

            extract_import_btb_lc_path(
                input_path=source_path,
                output_directory=first_output,
                page_provider=provider,
            )
            extract_import_btb_lc_path(
                input_path=source_path,
                output_directory=second_output,
                page_provider=provider,
            )

            first = (first_output / f"{source_path.name}.import-btb-lc.json").read_bytes()
            second = (second_output / f"{source_path.name}.import-btb-lc.json").read_bytes()

        self.assertEqual(first, second)

    def test_cli_writes_import_scoped_1_1_0_without_changing_shared_version(self) -> None:
        self.assertEqual(REPORT_SCHEMA_VERSION, "1.0.0")
        self.assertEqual(IMPORT_BTB_LC_EXTRACTION_SCHEMA_VERSION, "1.1.0")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "0742260401049.pdf"
            source_path.write_bytes(b"%PDF-1.4\nsynthetic\n")
            output_path = root / "output"
            provider = _StaticPageProvider(
                embedded=[
                    ExtractedPage(1, _sample_text(), "embedded_text", 1.0, True),
                ],
                ocr=[],
            )
            from unittest.mock import patch

            stdout = io.StringIO()
            with patch(
                "project.workflows.import_btb_lc.extraction.PDFImportBTBLCPageProvider",
                return_value=provider,
            ):
                with redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "extract-import-btb-lc",
                            "--input",
                            str(source_path),
                            "--output",
                            str(output_path),
                        ]
                    )
            summary = json.loads(stdout.getvalue())
            artifact = json.loads(
                (output_path / f"{source_path.name}.import-btb-lc.json").read_text(encoding="utf-8")
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["report_schema_version"], "1.1.0")
        self.assertEqual(artifact["report_schema_version"], "1.1.0")


class _StaticPageProvider:
    def __init__(self, *, embedded: list[ExtractedPage], ocr: list[ExtractedPage]) -> None:
        self._embedded = embedded
        self._ocr = ocr

    def embedded_pages(self, *, pdf_path: Path, page_limit: int) -> list[ExtractedPage]:
        del pdf_path
        return [page for page in self._embedded if page.page_number <= page_limit]

    def ocr_pages(self, *, pdf_path: Path, page_numbers: list[int]) -> list[ExtractedPage]:
        del pdf_path
        return [page for page in self._ocr if page.page_number in page_numbers]


def _extract_synthetic(
    text: str,
    *,
    filename: str = "0742260401049.pdf",
    provider: _StaticPageProvider | None = None,
) -> dict:
    with tempfile.TemporaryDirectory() as temp_dir:
        pdf_path = Path(temp_dir) / filename
        pdf_path.write_bytes(b"%PDF-1.4\nsynthetic\n")
        active_provider = provider or _StaticPageProvider(
            embedded=[
                ExtractedPage(1, text, "embedded_text", 1.0, True),
            ],
            ocr=[],
        )
        return extract_import_btb_lc_pdf(
            pdf_path=pdf_path,
            page_provider=active_provider,
        )


def _assert_pdf_fixture(
    test_case: unittest.TestCase,
    source_path: Path,
    expected: dict,
) -> None:
    artifact = extract_import_btb_lc_pdf(pdf_path=source_path)

    test_case.assertEqual(artifact["schema_version"], "1.1.0")
    test_case.assertEqual(artifact["report_schema_version"], "1.1.0")
    test_case.assertEqual(artifact["source"]["page_limit"], 3)
    test_case.assertEqual(
        artifact["source"]["file_sha256"],
        expected["file_sha256"],
    )
    test_case.assertEqual(
        artifact["bank_detection"]["bank_id"],
        expected["bank_id"],
    )
    test_case.assertEqual(
        artifact["overall_extraction_decision"],
        expected["decision"],
    )
    test_case.assertTrue(artifact["filename_comparison"]["matches"])
    for field_name in (
        "btb_lc_number",
        "btb_lc_date",
        "btb_lc_value",
        "currency",
        "seller_pi_numbers",
        "related_export_lc_number",
    ):
        test_case.assertEqual(
            artifact["fields"][field_name]["canonical"],
            expected[field_name],
        )
    test_case.assertTrue(
        all(
            field.get("page_number") is not None
            or field.get("values")
            or field["validation"]["status"] == "hard_block"
            for field in artifact["fields"].values()
        )
    )


def _sample_text(
    *,
    btb_number: str = "0742260401049",
    date_text: str = "260505",
    amount_text: str = "USD59242,25",
    pi_text: str = "BTL/26/3183",
    related_text: str = "EXPORT L/C NO. 1883260400042 DATE: 26-04-2026",
) -> str:
    return "\n".join(
        [
            "City Bank PLC. Trade Services Division",
            "20:",
            "Documentary Credit Number",
            btb_number,
            "31C: Date of Issue",
            date_text,
            "32B: Currency Code, Amount",
            amount_text,
            "41D: Available With ... By ...",
            "45A: Description of Goods and/or Services",
            f"AS PER PROFORMA INVOICE NO. {pi_text} DATED 01-01-2026",
            "47A: Additional Conditions",
            related_text,
        ]
    )


if __name__ == "__main__":
    unittest.main()
