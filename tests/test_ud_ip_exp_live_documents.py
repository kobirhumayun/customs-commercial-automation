from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project.documents import PyMuPDFSavedDocumentAnalysisProvider, SavedDocumentAnalysis
from project.models import (
    FinalDecision,
    MailMovePhaseStatus,
    PrintPhaseStatus,
    RunReport,
    WorkflowId,
    WritePhaseStatus,
)
from project.rules import load_rule_pack
from project.storage import SimulatedAttachmentContentProvider
from project.workbook import WorkbookHeader, WorkbookRow, WorkbookSnapshot
from project.workflows.registry import get_workflow_descriptor
from project.workflows.snapshot import SourceAttachmentRecord, SourceEmailRecord, build_email_snapshot
from project.workflows.ud_ip_exp import prepare_live_ud_ip_exp_documents
from project.workflows.validation import validate_run_snapshot


class UDIPEXPLiveDocumentTests(unittest.TestCase):
    def test_prepare_live_ud_ip_exp_documents_saves_into_family_directory(self) -> None:
        mail = _mail(
            "entry-live-001",
            "UD-LC-0043-ANANTA",
            attachments=[{"attachment_name": "UD-LC-0043-ANANTA.pdf"}],
        )
        workbook_snapshot = _full_snapshot(
            rows=[
                WorkbookRow(
                    row_index=11,
                    values={
                        1: "LC-0043",
                        2: "ANANTA GARMENTS LTD",
                        3: "2026-01-10",
                        4: "1000 YDS",
                        5: "",
                        6: "",
                        7: "",
                    },
                )
            ]
        )

        class Provider:
            def analyze(self, *, saved_document):
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="UD-LC-0043-ANANTA",
                    extracted_document_date="2026-04-01",
                    extracted_lc_sc_number="LC-0043",
                    extracted_quantity="1000",
                    extracted_quantity_unit="YDS",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = prepare_live_ud_ip_exp_documents(
                run_id="run-live-001",
                mail=mail,
                workbook_snapshot=workbook_snapshot,
                document_root=root,
                provider=SimulatedAttachmentContentProvider(
                    content_by_key={(mail.entry_id, 0): b"%PDF-1.4\nud live\n"}
                ),
                analysis_provider=Provider(),
            )

        self.assertEqual(result.document_save_result.issues, [])
        self.assertEqual(len(result.document_save_result.saved_documents), 1)
        self.assertTrue(
            result.document_save_result.saved_documents[0].destination_path.replace("\\", "/").endswith(
                "2026/ANANTA GARMENTS LTD/LC-0043/All Attachments/UD-LC-0043-ANANTA.pdf"
            )
        )
        self.assertEqual(
            result.classified_documents.documents[0].source_saved_document_id,
            result.document_save_result.saved_documents[0].saved_document_id,
        )

    def test_validate_run_snapshot_uses_live_ud_saved_document_analysis(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        mail = _mail(
            "entry-live-001",
            "UD-LC-0043-ANANTA",
            attachments=[{"attachment_name": "UD-LC-0043-ANANTA.pdf"}],
        )

        class Provider:
            def analyze(self, *, saved_document):
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="UD-LC-0043-ANANTA",
                    extracted_document_date="2026-04-01",
                    extracted_lc_sc_number="LC-0043",
                    extracted_quantity="1000",
                    extracted_quantity_unit="YDS",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            validation_result = validate_run_snapshot(
                descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
                run_report=_run_report(rule_pack, [mail]),
                rule_pack=rule_pack,
                workbook_snapshot=_full_snapshot(
                    rows=[
                        WorkbookRow(
                            row_index=11,
                            values={
                                1: "LC-0043",
                                2: "ANANTA GARMENTS LTD",
                                3: "2026-01-10",
                                4: "1000 YDS",
                                5: "",
                                6: "",
                                7: "",
                            },
                        )
                    ]
                ),
                attachment_content_provider=SimulatedAttachmentContentProvider(
                    content_by_key={(mail.entry_id, 0): b"%PDF-1.4\nud live\n"}
                ),
                document_root=Path(temp_dir),
                document_analysis_provider=Provider(),
            )

        self.assertEqual(validation_result.run_report.summary, {"pass": 1, "warning": 0, "hard_block": 0})
        self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.PASS)
        self.assertTrue(validation_result.mail_outcomes[0].eligible_for_write)
        self.assertFalse(validation_result.mail_outcomes[0].eligible_for_print)
        self.assertFalse(validation_result.mail_outcomes[0].eligible_for_mail_move)
        self.assertEqual(len(validation_result.staged_write_plan), 1)
        self.assertEqual(validation_result.staged_write_plan[0].row_index, 11)
        self.assertEqual(
            validation_result.mail_outcomes[0].saved_documents[0]["document_type"],
            "ud_document",
        )

    def test_prepare_live_ud_ip_exp_documents_infers_lc_sc_from_document_number_when_field_missing(self) -> None:
        mail = _mail(
            "entry-live-002",
            "UD-LC-0043-ANANTA",
            attachments=[{"attachment_name": "UD-LC-0043-ANANTA.pdf"}],
        )
        workbook_snapshot = _full_snapshot(
            rows=[
                WorkbookRow(
                    row_index=11,
                    values={
                        1: "LC-0043",
                        2: "ANANTA GARMENTS LTD",
                        3: "2026-01-10",
                        4: "1000 YDS",
                        5: "",
                        6: "",
                        7: "",
                    },
                )
            ]
        )

        class Provider:
            def analyze(self, *, saved_document):
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="UD-LC-0043-ANANTA",
                    extracted_document_date="2026-04-01",
                    extracted_quantity="1000",
                    extracted_quantity_unit="YDS",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = prepare_live_ud_ip_exp_documents(
                run_id="run-live-002",
                mail=mail,
                workbook_snapshot=workbook_snapshot,
                document_root=root,
                provider=SimulatedAttachmentContentProvider(
                    content_by_key={(mail.entry_id, 0): b"%PDF-1.4\nud infer\n"}
                ),
                analysis_provider=Provider(),
            )

        self.assertEqual(result.document_save_result.issues, [])
        self.assertEqual(len(result.classified_documents.documents), 1)
        self.assertEqual(result.classified_documents.documents[0].lc_sc_number.value, "LC-0043")

    def test_prepare_live_ud_ip_exp_documents_hard_blocks_mixed_live_document_families(self) -> None:
        mail = _mail(
            "entry-live-003",
            "UD-LC-0043-ANANTA",
            attachments=[
                {"attachment_name": "UD-LC-0043-ONE.pdf"},
                {"attachment_name": "UD-LC-9999-TWO.pdf"},
            ],
        )
        workbook_snapshot = _full_snapshot(
            rows=[
                WorkbookRow(
                    row_index=11,
                    values={
                        1: "LC-0043",
                        2: "ANANTA GARMENTS LTD",
                        3: "2026-01-10",
                        4: "1000 YDS",
                        5: "",
                        6: "",
                        7: "",
                    },
                )
            ]
        )

        class Provider:
            def analyze(self, *, saved_document):
                if saved_document.normalized_filename == "UD-LC-0043-ONE.pdf":
                    return SavedDocumentAnalysis(
                        analysis_basis="fixture",
                        extracted_document_number="UD-LC-0043-ONE",
                        extracted_document_date="2026-04-01",
                        extracted_lc_sc_number="LC-0043",
                        extracted_quantity="1000",
                        extracted_quantity_unit="YDS",
                    )
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="UD-LC-9999-TWO",
                    extracted_document_date="2026-04-01",
                    extracted_lc_sc_number="LC-9999",
                    extracted_quantity="1000",
                    extracted_quantity_unit="YDS",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = prepare_live_ud_ip_exp_documents(
                run_id="run-live-003",
                mail=mail,
                workbook_snapshot=workbook_snapshot,
                document_root=root,
                provider=SimulatedAttachmentContentProvider(
                    content_by_key={
                        (mail.entry_id, 0): b"%PDF-1.4\nud one\n",
                        (mail.entry_id, 1): b"%PDF-1.4\nud two\n",
                    }
                ),
                analysis_provider=Provider(),
            )

        self.assertEqual(len(result.document_save_result.issues), 1)
        self.assertEqual(result.document_save_result.issues[0].code, "document_storage_path_unresolved")
        self.assertEqual(
            result.document_save_result.issues[0].details["lc_sc_numbers"],
            ["LC-0043", "LC-9999"],
        )
        self.assertEqual(
            [
                evidence["document_number"]
                for evidence in result.document_save_result.issues[0].details["document_evidence"]
            ],
            ["UD-LC-0043-ONE", "UD-LC-9999-TWO"],
        )
        self.assertEqual(
            [
                evidence["attachment_name"]
                for evidence in result.document_save_result.issues[0].details["document_evidence"]
            ],
            ["UD-LC-0043-ONE.pdf", "UD-LC-9999-TWO.pdf"],
        )

    def test_prepare_live_ud_ip_exp_documents_hard_blocks_unresolved_family_with_attachment_evidence(self) -> None:
        mail = _mail(
            "entry-live-004",
            "UD attachment unresolved",
            attachments=[{"attachment_name": "ud-unresolved.pdf"}],
        )

        class Provider:
            def analyze(self, *, saved_document):
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="UD-REFERENCE-ONLY",
                    extracted_document_date="2026-04-01",
                    extracted_quantity="1000",
                    extracted_quantity_unit="YDS",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = prepare_live_ud_ip_exp_documents(
                run_id="run-live-004",
                mail=mail,
                workbook_snapshot=_full_snapshot(rows=[]),
                document_root=root,
                provider=SimulatedAttachmentContentProvider(
                    content_by_key={(mail.entry_id, 0): b"%PDF-1.4\nud unresolved\n"}
                ),
                analysis_provider=Provider(),
            )

        self.assertEqual(len(result.document_save_result.issues), 1)
        self.assertEqual(result.document_save_result.issues[0].code, "document_storage_path_unresolved")
        self.assertEqual(result.document_save_result.issues[0].details["lc_sc_numbers"], [])
        self.assertEqual(len(result.document_save_result.issues[0].details["document_evidence"]), 1)
        self.assertEqual(
            {key: value for key, value in result.document_save_result.issues[0].details["document_evidence"][0].items() if key != "saved_document_id"},
            {
                "attachment_name": "ud-unresolved.pdf",
                "normalized_filename": "ud-unresolved.pdf",
                "document_kind": "UD",
                "document_number": "UD-REFERENCE-ONLY",
                "lc_sc_number": "",
                "document_date": "2026-04-01",
                "quantity": "1000 YDS",
            },
        )
        self.assertTrue(result.document_save_result.issues[0].details["document_evidence"][0]["saved_document_id"])

    def test_validate_run_snapshot_serializes_live_document_resolution_evidence(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        mail = _mail(
            "entry-live-005",
            "UD mixed family live validation",
            attachments=[
                {"attachment_name": "UD-LC-0043-ONE.pdf"},
                {"attachment_name": "UD-LC-9999-TWO.pdf"},
            ],
        )

        class Provider:
            def analyze(self, *, saved_document):
                if saved_document.normalized_filename == "UD-LC-0043-ONE.pdf":
                    return SavedDocumentAnalysis(
                        analysis_basis="fixture",
                        extracted_document_number="UD-LC-0043-ONE",
                        extracted_document_date="2026-04-01",
                        extracted_lc_sc_number="LC-0043",
                        extracted_quantity="1000",
                        extracted_quantity_unit="YDS",
                    )
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="UD-LC-9999-TWO",
                    extracted_document_date="2026-04-01",
                    extracted_lc_sc_number="LC-9999",
                    extracted_quantity="1000",
                    extracted_quantity_unit="YDS",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            validation_result = validate_run_snapshot(
                descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
                run_report=_run_report(rule_pack, [mail]),
                rule_pack=rule_pack,
                workbook_snapshot=_full_snapshot(
                    rows=[
                        WorkbookRow(
                            row_index=11,
                            values={
                                1: "LC-0043",
                                2: "ANANTA GARMENTS LTD",
                                3: "2026-01-10",
                                4: "1000 YDS",
                                5: "",
                                6: "",
                                7: "",
                            },
                        )
                    ]
                ),
                attachment_content_provider=SimulatedAttachmentContentProvider(
                    content_by_key={
                        (mail.entry_id, 0): b"%PDF-1.4\nud one\n",
                        (mail.entry_id, 1): b"%PDF-1.4\nud two\n",
                    }
                ),
                document_root=Path(temp_dir),
                document_analysis_provider=Provider(),
            )

        self.assertEqual(validation_result.run_report.summary, {"pass": 0, "warning": 0, "hard_block": 1})
        self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.HARD_BLOCK)
        discrepancy = next(
            item
            for item in validation_result.mail_outcomes[0].discrepancies
            if item["code"] == "document_storage_path_unresolved"
        )
        self.assertEqual(discrepancy["code"], "document_storage_path_unresolved")
        self.assertEqual(discrepancy["details"]["lc_sc_numbers"], ["LC-0043", "LC-9999"])
        self.assertEqual(
            [evidence["document_number"] for evidence in discrepancy["details"]["document_evidence"]],
            ["UD-LC-0043-ONE", "UD-LC-9999-TWO"],
        )

    def test_prepare_live_ud_ip_exp_documents_hard_blocks_conflicting_ud_quantities_within_same_family(self) -> None:
        mail = _mail(
            "entry-live-006",
            "UD same family conflicting quantity",
            attachments=[
                {"attachment_name": "UD-LC-0043-ONE.pdf"},
                {"attachment_name": "UD-LC-0043-TWO.pdf"},
            ],
        )
        workbook_snapshot = _full_snapshot(
            rows=[
                WorkbookRow(
                    row_index=11,
                    values={
                        1: "LC-0043",
                        2: "ANANTA GARMENTS LTD",
                        3: "2026-01-10",
                        4: "1000 YDS",
                        5: "",
                        6: "",
                        7: "",
                    },
                )
            ]
        )

        class Provider:
            def analyze(self, *, saved_document):
                if saved_document.normalized_filename == "UD-LC-0043-ONE.pdf":
                    return SavedDocumentAnalysis(
                        analysis_basis="fixture",
                        extracted_document_number="UD-LC-0043-ONE",
                        extracted_document_date="2026-04-01",
                        extracted_lc_sc_number="LC-0043",
                        extracted_quantity="1000",
                        extracted_quantity_unit="YDS",
                    )
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="UD-LC-0043-TWO",
                    extracted_document_date="2026-04-01",
                    extracted_lc_sc_number="LC-0043",
                    extracted_quantity="1200",
                    extracted_quantity_unit="YDS",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = prepare_live_ud_ip_exp_documents(
                run_id="run-live-006",
                mail=mail,
                workbook_snapshot=workbook_snapshot,
                document_root=root,
                provider=SimulatedAttachmentContentProvider(
                    content_by_key={
                        (mail.entry_id, 0): b"%PDF-1.4\nud one\n",
                        (mail.entry_id, 1): b"%PDF-1.4\nud two\n",
                    }
                ),
                analysis_provider=Provider(),
            )

        self.assertEqual(len(result.document_save_result.issues), 1)
        self.assertEqual(result.document_save_result.issues[0].code, "ud_live_document_conflict")
        self.assertEqual(result.document_save_result.issues[0].details["conflicting_fields"], ["quantity"])
        self.assertEqual(result.document_save_result.issues[0].details["quantities"], ["1000 YDS", "1200 YDS"])
        self.assertEqual(
            [evidence["document_number"] for evidence in result.document_save_result.issues[0].details["document_evidence"]],
            ["UD-LC-0043-ONE", "UD-LC-0043-TWO"],
        )

    def test_validate_run_snapshot_serializes_live_ud_date_conflict(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        mail = _mail(
            "entry-live-007",
            "UD same family conflicting date",
            attachments=[
                {"attachment_name": "UD-LC-0043-ONE.pdf"},
                {"attachment_name": "UD-LC-0043-TWO.pdf"},
            ],
        )

        class Provider:
            def analyze(self, *, saved_document):
                if saved_document.normalized_filename == "UD-LC-0043-ONE.pdf":
                    return SavedDocumentAnalysis(
                        analysis_basis="fixture",
                        extracted_document_number="UD-LC-0043-ONE",
                        extracted_document_date="2026-04-01",
                        extracted_lc_sc_number="LC-0043",
                        extracted_quantity="1000",
                        extracted_quantity_unit="YDS",
                    )
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="UD-LC-0043-TWO",
                    extracted_document_date="2026-04-02",
                    extracted_lc_sc_number="LC-0043",
                    extracted_quantity="1000",
                    extracted_quantity_unit="YDS",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            validation_result = validate_run_snapshot(
                descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
                run_report=_run_report(rule_pack, [mail]),
                rule_pack=rule_pack,
                workbook_snapshot=_full_snapshot(
                    rows=[
                        WorkbookRow(
                            row_index=11,
                            values={
                                1: "LC-0043",
                                2: "ANANTA GARMENTS LTD",
                                3: "2026-01-10",
                                4: "1000 YDS",
                                5: "",
                                6: "",
                                7: "",
                            },
                        )
                    ]
                ),
                attachment_content_provider=SimulatedAttachmentContentProvider(
                    content_by_key={
                        (mail.entry_id, 0): b"%PDF-1.4\nud one\n",
                        (mail.entry_id, 1): b"%PDF-1.4\nud two\n",
                    }
                ),
                document_root=Path(temp_dir),
                document_analysis_provider=Provider(),
            )

        self.assertEqual(validation_result.run_report.summary, {"pass": 0, "warning": 0, "hard_block": 1})
        self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.HARD_BLOCK)
        discrepancy = next(
            item
            for item in validation_result.mail_outcomes[0].discrepancies
            if item["code"] == "ud_live_document_conflict"
        )
        self.assertEqual(discrepancy["details"]["conflicting_fields"], ["document_date"])
        self.assertEqual(discrepancy["details"]["document_dates"], ["2026-04-01", "2026-04-02"])
        self.assertEqual(
            [evidence["document_number"] for evidence in discrepancy["details"]["document_evidence"]],
            ["UD-LC-0043-ONE", "UD-LC-0043-TWO"],
        )

    def test_validate_run_snapshot_hard_blocks_mixed_quality_ud_documents_with_stable_evidence(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        mail = _mail(
            "entry-live-008",
            "UD mixed quality live documents",
            attachments=[
                {"attachment_name": "UD-LC-0043-PARTIAL.pdf"},
                {"attachment_name": "UD-LC-0043-COMPLETE.pdf"},
            ],
        )

        class Provider:
            def analyze(self, *, saved_document):
                if saved_document.normalized_filename == "UD-LC-0043-PARTIAL.pdf":
                    return SavedDocumentAnalysis(
                        analysis_basis="fixture",
                        extracted_document_number="UD-LC-0043-PARTIAL",
                        extracted_document_date="2026-04-01",
                        extracted_lc_sc_number="LC-0043",
                    )
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="UD-LC-0043-COMPLETE",
                    extracted_document_date="2026-04-01",
                    extracted_lc_sc_number="LC-0043",
                    extracted_quantity="1000",
                    extracted_quantity_unit="YDS",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            validation_result = validate_run_snapshot(
                descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
                run_report=_run_report(rule_pack, [mail]),
                rule_pack=rule_pack,
                workbook_snapshot=_full_snapshot(
                    rows=[
                        WorkbookRow(
                            row_index=11,
                            values={
                                1: "LC-0043",
                                2: "ANANTA GARMENTS LTD",
                                3: "2026-01-10",
                                4: "1000 YDS",
                                5: "",
                                6: "",
                                7: "",
                            },
                        )
                    ]
                ),
                attachment_content_provider=SimulatedAttachmentContentProvider(
                    content_by_key={
                        (mail.entry_id, 0): b"%PDF-1.4\nud partial\n",
                        (mail.entry_id, 1): b"%PDF-1.4\nud complete\n",
                    }
                ),
                document_root=Path(temp_dir),
                document_analysis_provider=Provider(),
            )

        self.assertEqual(validation_result.run_report.summary, {"pass": 0, "warning": 0, "hard_block": 1})
        self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.HARD_BLOCK)
        discrepancy = next(
            item
            for item in validation_result.mail_outcomes[0].discrepancies
            if item["code"] == "ud_required_field_missing"
        )
        self.assertEqual(
            discrepancy["details"]["missing_by_document"],
            [
                {
                    "document_index": 0,
                    "document_number": "UD-LC-0043-PARTIAL",
                    "missing_fields": ["quantity"],
                }
            ],
        )
        self.assertEqual(validation_result.mail_outcomes[0].ud_selection["required_quantity"], "1000")
        self.assertEqual(
            validation_result.mail_outcomes[0].saved_documents[0]["extracted_document_number"],
            "UD-LC-0043-PARTIAL",
        )
        self.assertEqual(
            validation_result.mail_outcomes[0].saved_documents[1]["extracted_document_number"],
            "UD-LC-0043-COMPLETE",
        )
        self.assertEqual(validation_result.staged_write_plan, [])

    def test_validate_run_snapshot_does_not_use_lc_issue_date_as_ud_date(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        mail = _mail(
            "entry-live-009",
            "UD with LC issue date only",
            attachments=[{"attachment_name": "UD-LC-0043-ANANTA.pdf"}],
        )

        class FakePage:
            def get_text(self, mode: str) -> str:
                self.last_mode = mode
                return (
                    "UD No: UD-LC-0043-ANANTA\n"
                    "L/C Issue Date: 2026-01-10\n"
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
                return FakeDocument()

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("project.documents.providers._load_pymupdf_module", return_value=FakeFitz()):
                validation_result = validate_run_snapshot(
                    descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
                    run_report=_run_report(rule_pack, [mail]),
                    rule_pack=rule_pack,
                    workbook_snapshot=_full_snapshot(
                        rows=[
                            WorkbookRow(
                                row_index=11,
                                values={
                                    1: "LC-0043",
                                    2: "ANANTA GARMENTS LTD",
                                    3: "2026-01-10",
                                    4: "1000 YDS",
                                    5: "",
                                    6: "",
                                    7: "",
                                },
                            )
                        ]
                    ),
                    attachment_content_provider=SimulatedAttachmentContentProvider(
                        content_by_key={(mail.entry_id, 0): b"%PDF-1.4\nud with lc issue date only\n"}
                    ),
                    document_root=Path(temp_dir),
                    document_analysis_provider=PyMuPDFSavedDocumentAnalysisProvider(),
                )

        self.assertEqual(validation_result.run_report.summary, {"pass": 0, "warning": 0, "hard_block": 1})
        self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.HARD_BLOCK)
        discrepancy = next(
            item
            for item in validation_result.mail_outcomes[0].discrepancies
            if item["code"] == "ud_required_field_missing"
        )
        self.assertEqual(
            discrepancy["details"]["missing_by_document"],
            [
                {
                    "document_index": 0,
                    "document_number": "UD-LC-0043-ANANTA",
                    "missing_fields": ["document_date"],
                }
            ],
        )
        self.assertIsNone(validation_result.mail_outcomes[0].saved_documents[0]["extracted_document_date"])
        self.assertEqual(validation_result.mail_outcomes[0].ud_selection["required_quantity"], "1000")
        self.assertEqual(validation_result.staged_write_plan, [])


def _mail(entry_id: str, subject: str, *, attachments: list[dict] | None = None):
    return build_email_snapshot(
        [
            SourceEmailRecord(
                entry_id=entry_id,
                received_time="2026-04-01T03:00:00Z",
                subject_raw=subject,
                sender_address="sender@example.com",
                attachments=[
                    SourceAttachmentRecord(attachment_name=attachment["attachment_name"])
                    for attachment in (attachments or [])
                ],
            )
        ],
        state_timezone="Asia/Dhaka",
    )[0]


def _run_report(rule_pack, mails):
    return RunReport(
        run_id="run-live-001",
        workflow_id=WorkflowId.UD_IP_EXP,
        tool_version="0.1.0",
        rule_pack_id=rule_pack.rule_pack_id,
        rule_pack_version=rule_pack.rule_pack_version,
        started_at_utc="2026-04-01T00:00:00Z",
        completed_at_utc=None,
        state_timezone="Asia/Dhaka",
        mail_iteration_order=[mail.mail_id for mail in mails],
        print_group_order=[],
        write_phase_status=WritePhaseStatus.NOT_STARTED,
        print_phase_status=PrintPhaseStatus.NOT_STARTED,
        mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
        hash_algorithm="sha256",
        run_start_backup_hash="a" * 64,
        current_workbook_hash="b" * 64,
        staged_write_plan_hash="",
        summary={"pass": 0, "warning": 0, "hard_block": 0},
        mail_snapshot=list(mails),
    )


def _full_snapshot(*, rows: list[WorkbookRow]) -> WorkbookSnapshot:
    return WorkbookSnapshot(
        sheet_name="Sheet1",
        headers=[
            WorkbookHeader(column_index=1, text="L/C & S/C No."),
            WorkbookHeader(column_index=2, text="Name of Buyers"),
            WorkbookHeader(column_index=3, text="LC Issue Date"),
            WorkbookHeader(column_index=4, text="Quantity of Fabrics (Yds/Mtr)"),
            WorkbookHeader(column_index=5, text="UD No. & IP No."),
            WorkbookHeader(column_index=6, text="L/C Amnd No."),
            WorkbookHeader(column_index=7, text="L/C Amnd Date"),
        ],
        rows=rows,
    )


if __name__ == "__main__":
    unittest.main()
