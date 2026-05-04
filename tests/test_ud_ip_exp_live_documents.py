from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from project.documents import PyMuPDFSavedDocumentAnalysisProvider, SavedDocumentAnalysis
from project.erp import ERPFamily, ERPRegisterRow
from project.models import (
    FinalDecision,
    MailMovePhaseStatus,
    PrintPhaseStatus,
    RunReport,
    WorkflowId,
    WritePhaseStatus,
    MailProcessingStatus,
)
from project.outlook import SimulatedMailMoveProvider
from project.rules import load_rule_pack
from project.storage import create_run_artifact_layout
from project.storage import SimulatedAttachmentContentProvider
from project.workbook import WorkbookHeader, WorkbookRow, WorkbookSnapshot
from project.workflows.mail_moves import execute_mail_moves
from project.workflows.print_planning import plan_print_batches
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
                    extracted_document_number="BGMEA/DHK/UD/2026/5483/003",
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

    def test_prepare_live_ud_ip_exp_documents_hard_blocks_filename_suffix_mismatch_with_erp_family(self) -> None:
        mail = _mail(
            "entry-live-filename-mismatch",
            "Subject ignored for UD/IP/EXP",
            attachments=[{"attachment_name": "UD-LC-0043-RENAMED-WRONG.pdf"}],
        )

        class Provider:
            def analyze(self, *, saved_document):
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="BGMEA/DHK/UD/2026/5483/999",
                    extracted_document_date="2026-04-01",
                    extracted_lc_sc_number="LC-9999",
                    extracted_quantity="1000",
                    extracted_quantity_unit="YDS",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            result = prepare_live_ud_ip_exp_documents(
                run_id="run-live-filename-mismatch",
                mail=mail,
                workbook_snapshot=None,
                document_root=Path(temp_dir),
                provider=SimulatedAttachmentContentProvider(
                    content_by_key={(mail.entry_id, 0): b"%PDF-1.4\nud renamed wrong\n"}
                ),
                analysis_provider=Provider(),
                verified_family=ERPFamily(
                    lc_sc_number="LC-9999",
                    buyer_name="ANANTA GARMENTS LTD",
                    lc_sc_date="2026-01-10",
                    folder_buyer_name="ANANTA GARMENTS LTD",
                ),
            )

        self.assertEqual(len(result.document_save_result.issues), 1)
        issue = result.document_save_result.issues[0]
        self.assertEqual(issue.code, "ud_filename_lc_suffix_mismatch")
        self.assertEqual(issue.details["expected_lc_sc_number"], "LC-9999")
        self.assertEqual(
            issue.details["mismatched_filename_suffixes"],
            [
                {
                    "attachment_name": "UD-LC-0043-RENAMED-WRONG.pdf",
                    "normalized_filename": "UD-LC-0043-RENAMED-WRONG.pdf",
                    "filename_suffix": "0043",
                    "expected_lc_sc_number": "LC-9999",
                }
            ],
        )
        self.assertEqual(issue.details["document_evidence"][0]["lc_sc_number"], "LC-9999")

    def test_prepare_live_ud_ip_exp_documents_does_not_require_filename_suffix_when_absent(self) -> None:
        mail = _mail(
            "entry-live-no-filename-suffix",
            "Subject ignored for UD/IP/EXP",
            attachments=[{"attachment_name": "UD-document.pdf"}],
        )

        class Provider:
            def analyze(self, *, saved_document):
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="BGMEA/DHK/UD/2026/5483/999",
                    extracted_document_date="2026-04-01",
                    extracted_lc_sc_number="LC-9999",
                    extracted_quantity="1000",
                    extracted_quantity_unit="YDS",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            result = prepare_live_ud_ip_exp_documents(
                run_id="run-live-no-filename-suffix",
                mail=mail,
                workbook_snapshot=None,
                document_root=Path(temp_dir),
                provider=SimulatedAttachmentContentProvider(
                    content_by_key={(mail.entry_id, 0): b"%PDF-1.4\nud no filename suffix\n"}
                ),
                analysis_provider=Provider(),
                verified_family=ERPFamily(
                    lc_sc_number="LC-9999",
                    buyer_name="ANANTA GARMENTS LTD",
                    lc_sc_date="2026-01-10",
                    folder_buyer_name="ANANTA GARMENTS LTD",
                ),
            )

        self.assertEqual(result.document_save_result.issues, [])
        self.assertEqual(len(result.document_save_result.saved_documents), 1)
        self.assertTrue(
            result.document_save_result.saved_documents[0].destination_path.replace("\\", "/").endswith(
                "2026/ANANTA GARMENTS LTD/LC-9999/All Attachments/UD-document.pdf"
            )
        )

    def test_prepare_live_ud_ip_exp_documents_saves_all_pdfs_but_only_extracts_processable_ud_ip_exp_documents(self) -> None:
        mail = _mail(
            "entry-live-strict-reader",
            "UD strict reader",
            attachments=[
                {"attachment_name": "PDL-26-1755.pdf"},
                {"attachment_name": "UD-LC-0113-ANANTA CASUAL WEAR LTD.pdf"},
                {"attachment_name": "LC-0113-ANANTA CASUAL WEAR LTD.pdf"},
            ],
        )
        test_case = self

        class Provider:
            def analyze(self, *, saved_document):
                test_case.assertEqual(saved_document.normalized_filename, "UD-LC-0113-ANANTA CASUAL WEAR LTD.pdf")
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="BGMEA/DHK/UD/2026/5483/113",
                    extracted_document_date="2026-04-19",
                    extracted_lc_sc_number="LC-0113",
                    extracted_quantity="26548",
                    extracted_quantity_unit="MTR",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            result = prepare_live_ud_ip_exp_documents(
                run_id="run-live-strict-reader",
                mail=mail,
                workbook_snapshot=None,
                document_root=Path(temp_dir),
                provider=SimulatedAttachmentContentProvider(
                    content_by_key={
                        (mail.entry_id, 0): b"%PDF-1.4\npi\n",
                        (mail.entry_id, 1): b"%PDF-1.4\nud\n",
                        (mail.entry_id, 2): b"%PDF-1.4\nlc\n",
                    }
                ),
                analysis_provider=Provider(),
                verified_family=ERPFamily(
                    lc_sc_number="LC-0113",
                    buyer_name="ANANTA CASUAL WEAR LTD",
                    lc_sc_date="2026-04-15",
                    folder_buyer_name="ANANTA CASUAL WEAR LTD",
                ),
            )

        self.assertEqual(result.document_save_result.issues, [])
        self.assertEqual(
            [document.normalized_filename for document in result.document_save_result.saved_documents],
            [
                "PDL-26-1755.pdf",
                "UD-LC-0113-ANANTA CASUAL WEAR LTD.pdf",
                "LC-0113-ANANTA CASUAL WEAR LTD.pdf",
            ],
        )
        self.assertEqual(len(result.classified_documents.documents), 1)
        self.assertEqual(result.classified_documents.documents[0].document_number.value, "BGMEA/DHK/UD/2026/5483/113")
        supporting_documents = [
            document
            for document in result.classified_documents.saved_documents
            if document.document_type == "supporting_pdf"
        ]
        self.assertEqual(len(supporting_documents), 2)
        self.assertTrue(all(document.print_eligible for document in supporting_documents))

    def test_prepare_live_ud_ip_exp_documents_ignores_exp_files_with_trailing_descriptors(self) -> None:
        mail = _mail(
            "entry-live-exp-reader",
            "EXP strict reader",
            attachments=[
                {"attachment_name": "123-EXP.pdf"},
                {"attachment_name": "123-EXP-INVOICE.pdf"},
            ],
        )
        test_case = self

        class Provider:
            def analyze(self, *, saved_document):
                test_case.assertEqual(saved_document.normalized_filename, "123-EXP.pdf")
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="EXP-123",
                    extracted_document_date="2026-04-19",
                    extracted_lc_sc_number="LC-0113",
                    extracted_quantity="26548",
                    extracted_quantity_unit="MTR",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            result = prepare_live_ud_ip_exp_documents(
                run_id="run-live-exp-reader",
                mail=mail,
                workbook_snapshot=None,
                document_root=Path(temp_dir),
                provider=SimulatedAttachmentContentProvider(
                    content_by_key={
                        (mail.entry_id, 0): b"%PDF-1.4\nmachine generated exp\n",
                        (mail.entry_id, 1): b"%PDF-1.4\nscanned invoice\n",
                    }
                ),
                analysis_provider=Provider(),
                verified_family=ERPFamily(
                    lc_sc_number="LC-0113",
                    buyer_name="ANANTA CASUAL WEAR LTD",
                    lc_sc_date="2026-04-15",
                    folder_buyer_name="ANANTA CASUAL WEAR LTD",
                ),
            )

        self.assertEqual(result.document_save_result.issues, [])
        self.assertEqual(
            [document.normalized_filename for document in result.document_save_result.saved_documents],
            ["123-EXP.pdf", "123-EXP-INVOICE.pdf"],
        )
        self.assertEqual(len(result.classified_documents.documents), 1)
        self.assertEqual(result.classified_documents.documents[0].document_number.value, "EXP-123")
        self.assertEqual(result.classified_documents.saved_documents[1].document_type, "supporting_pdf")
        self.assertTrue(result.classified_documents.saved_documents[1].print_eligible)

    def test_prepare_live_ud_ip_exp_documents_analyzes_processable_pdfs_once_for_many_attachment_mail(self) -> None:
        attachments = [{"attachment_name": f"supporting-{index:02d}.pdf"} for index in range(12)]
        attachments.insert(7, {"attachment_name": "UD-LC-0113-ANANTA CASUAL WEAR LTD.pdf"})
        mail = _mail(
            "entry-live-many-pdfs",
            "UD many pdfs",
            attachments=attachments,
        )

        class Provider:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def analyze(self, *, saved_document):
                self.calls.append(saved_document.normalized_filename)
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="BGMEA/DHK/UD/2026/5483/113",
                    extracted_document_date="2026-04-19",
                    extracted_lc_sc_number="LC-0113",
                    extracted_quantity="26548",
                    extracted_quantity_unit="MTR",
                )

        provider = Provider()
        content_by_key = {
            (mail.entry_id, index): f"%PDF-1.4\nattachment {index}\n".encode("utf-8")
            for index in range(len(attachments))
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            result = prepare_live_ud_ip_exp_documents(
                run_id="run-live-many-pdfs",
                mail=mail,
                workbook_snapshot=None,
                document_root=Path(temp_dir),
                provider=SimulatedAttachmentContentProvider(content_by_key=content_by_key),
                analysis_provider=provider,
                verified_family=ERPFamily(
                    lc_sc_number="LC-0113",
                    buyer_name="ANANTA CASUAL WEAR LTD",
                    lc_sc_date="2026-04-15",
                    folder_buyer_name="ANANTA CASUAL WEAR LTD",
                ),
            )

        self.assertEqual(result.document_save_result.issues, [])
        self.assertEqual(len(result.document_save_result.saved_documents), len(attachments))
        self.assertEqual(provider.calls, ["UD-LC-0113-ANANTA CASUAL WEAR LTD.pdf"])
        self.assertEqual(len(result.classified_documents.documents), 1)

    def test_prepare_live_ud_ip_exp_documents_reuses_saved_new_hash_after_move(self) -> None:
        mail = _mail(
            "entry-live-hash-reuse",
            "UD hash reuse",
            attachments=[{"attachment_name": "UD-LC-0043-ANANTA.pdf"}],
        )

        class Provider:
            def analyze(self, *, saved_document):
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="BGMEA/DHK/UD/2026/5483/003",
                    extracted_document_date="2026-04-01",
                    extracted_lc_sc_number="LC-0043",
                    extracted_quantity="1000",
                    extracted_quantity_unit="YDS",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch(
                "project.workflows.ud_ip_exp.live_documents.sha256_file",
                return_value="f" * 64,
            ) as sha256_mock:
                result = prepare_live_ud_ip_exp_documents(
                    run_id="run-live-hash-reuse",
                    mail=mail,
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
                    document_root=root,
                    provider=SimulatedAttachmentContentProvider(
                        content_by_key={(mail.entry_id, 0): b"%PDF-1.4\nud live\n"}
                    ),
                    analysis_provider=Provider(),
                )

        self.assertEqual(result.document_save_result.issues, [])
        self.assertEqual(sha256_mock.call_count, 1)
        self.assertEqual(result.document_save_result.saved_documents[0].file_sha256, "f" * 64)

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
                    extracted_document_number="BGMEA/DHK/UD/2026/5483/003",
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
                erp_row_provider=_erp_provider(buyer_name="ERP BUYER LTD", lc_sc_date="2026-01-10"),
                workbook_snapshot=_full_snapshot(
                    rows=[
                        WorkbookRow(
                            row_index=11,
                            values={
                                1: "LC-0043",
                                2: "WORKBOOK BUYER LTD",
                                3: "2025-05-05",
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
        self.assertTrue(validation_result.mail_outcomes[0].eligible_for_print)
        self.assertTrue(validation_result.mail_outcomes[0].eligible_for_mail_move)
        self.assertEqual(len(validation_result.staged_write_plan), 1)
        self.assertEqual(validation_result.staged_write_plan[0].row_index, 11)
        self.assertEqual(
            validation_result.mail_outcomes[0].saved_documents[0]["document_type"],
            "ud_document",
        )
        self.assertIn(
            "2026/ERP BUYER LTD/LC-0043/All Attachments/UD-LC-0043-ANANTA.pdf",
            validation_result.mail_outcomes[0].saved_documents[0]["destination_path"].replace("\\", "/"),
        )

    def test_live_ud_documents_plan_print_and_mail_move_after_shared_transport_gates(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        mail = _mail(
            "entry-live-transport-001",
            "Subject intentionally ignored for family",
            attachments=[{"attachment_name": "UD-LC-0043-ANANTA.pdf"}],
        )

        class Provider:
            def analyze(self, *, saved_document):
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="BGMEA/DHK/UD/2026/5483/003",
                    extracted_document_date="2026-04-01",
                    extracted_lc_sc_number="LC-0043",
                    extracted_quantity="1000",
                    extracted_quantity_unit="YDS",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            validation_result = validate_run_snapshot(
                descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
                run_report=_run_report(rule_pack, [mail]),
                rule_pack=rule_pack,
                erp_row_provider=_erp_provider(buyer_name="ERP TRANSPORT BUYER LTD"),
                workbook_snapshot=_full_snapshot(
                    rows=[
                        WorkbookRow(
                            row_index=11,
                            values={
                                1: "LC-0043",
                                2: "WORKBOOK BUYER SHOULD NOT DRIVE STORAGE",
                                3: "2025-05-05",
                                4: "1000 YDS",
                                5: "",
                                6: "",
                                7: "",
                            },
                        )
                    ]
                ),
                attachment_content_provider=SimulatedAttachmentContentProvider(
                    content_by_key={(mail.entry_id, 0): b"%PDF-1.4\nud transport\n"}
                ),
                document_root=root / "documents",
                document_analysis_provider=Provider(),
            )
            committed_report = replace(
                validation_result.run_report,
                write_phase_status=WritePhaseStatus.COMMITTED,
                resolved_source_folder_entry_id="src-folder",
                resolved_destination_folder_entry_id="dst-folder",
                folder_resolution_mode="entry_id",
            )
            planning_result = plan_print_batches(
                run_report=committed_report,
                mail_outcomes=validation_result.mail_outcomes,
                staged_write_plan=validation_result.staged_write_plan,
            )
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id=WorkflowId.UD_IP_EXP.value,
                run_id=committed_report.run_id,
            )
            blocked_report, blocked_outcomes, _blocked_moves, blocked_discrepancies = execute_mail_moves(
                run_report=planning_result.run_report,
                mail_outcomes=planning_result.mail_outcomes,
                artifact_paths=artifact_paths,
                provider=SimulatedMailMoveProvider(),
            )
            completed_print_report = replace(
                planning_result.run_report,
                print_phase_status=PrintPhaseStatus.COMPLETED,
            )
            moved_report, moved_outcomes, move_operations, move_discrepancies = execute_mail_moves(
                run_report=completed_print_report,
                mail_outcomes=planning_result.mail_outcomes,
                artifact_paths=artifact_paths,
                provider=SimulatedMailMoveProvider(),
            )

        saved_document = validation_result.mail_outcomes[0].saved_documents[0]
        self.assertEqual(validation_result.run_report.summary, {"pass": 1, "warning": 0, "hard_block": 0})
        self.assertTrue(validation_result.mail_outcomes[0].eligible_for_print)
        self.assertTrue(validation_result.mail_outcomes[0].eligible_for_mail_move)
        self.assertTrue(saved_document["print_eligible"])
        self.assertIn(
            "2026/ERP TRANSPORT BUYER LTD/LC-0043/All Attachments/UD-LC-0043-ANANTA.pdf",
            saved_document["destination_path"].replace("\\", "/"),
        )
        self.assertEqual(planning_result.run_report.print_phase_status, PrintPhaseStatus.PLANNED)
        self.assertEqual(len(planning_result.print_batches), 1)
        self.assertEqual(planning_result.print_batches[0].document_paths, [saved_document["destination_path"]])
        self.assertEqual(blocked_report.mail_move_phase_status, MailMovePhaseStatus.HARD_BLOCKED)
        self.assertEqual(blocked_discrepancies[0].code, "mail_move_gate_unsatisfied")
        self.assertFalse(blocked_outcomes[0].eligible_for_mail_move)
        self.assertEqual(moved_report.mail_move_phase_status, MailMovePhaseStatus.COMPLETED)
        self.assertEqual(moved_outcomes[0].processing_status, MailProcessingStatus.MOVED)
        self.assertEqual(move_discrepancies, [])
        self.assertEqual(len(move_operations), 1)

    def test_validate_run_snapshot_uses_email_file_number_erp_context_for_structured_ud_pdfs(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        base_mail = _mail_with_body_file(
            "entry-structured-base-0434",
            file_number="P/26/7001",
            attachment_name="UD-LC-0434-NALIN TEX LTD.pdf",
        )
        amendment_mail = _mail_with_body_file(
            "entry-structured-amd-0935",
            file_number="P/26/8002",
            attachment_name="UD-LC-0935-A.K.M KNIT WEAR LTD_AMD_01.pdf",
        )

        class FallbackProvider:
            def analyze(self, *, saved_document):
                raise AssertionError(
                    f"Structured extraction should handle {saved_document.normalized_filename} without fallback."
                )

        def raw_report_loader(*, saved_document, mode="layered", **_kwargs):
            self.assertEqual(mode, "layered")
            if saved_document.normalized_filename == "UD-LC-0434-NALIN TEX LTD.pdf":
                return _base_structured_report()
            if saved_document.normalized_filename == "UD-LC-0935-A.K.M KNIT WEAR LTD_AMD_01.pdf":
                return _amendment_structured_report()
            raise AssertionError(f"Unexpected saved document: {saved_document.normalized_filename}")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "project.workflows.ud_ip_exp.structured_extraction.extract_saved_document_raw_report",
                side_effect=raw_report_loader,
            ):
                validation_result = validate_run_snapshot(
                    descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
                    run_report=_run_report(rule_pack, [base_mail, amendment_mail]),
                    rule_pack=rule_pack,
                    erp_row_provider=_structured_erp_provider(),
                    workbook_snapshot=_structured_workbook_snapshot(),
                    attachment_content_provider=SimulatedAttachmentContentProvider(
                        content_by_key={
                            (base_mail.entry_id, 0): b"%PDF-1.4\nstructured base ud\n",
                            (amendment_mail.entry_id, 0): b"%PDF-1.4\nstructured amendment ud\n",
                        }
                    ),
                    document_root=Path(temp_dir),
                    document_analysis_provider=FallbackProvider(),
                )

        self.assertEqual(validation_result.run_report.summary, {"pass": 2, "warning": 0, "hard_block": 0})
        self.assertEqual(len(validation_result.staged_write_plan), 6)
        self.assertEqual(validation_result.mail_outcomes[0].file_numbers_extracted, ["P/26/7001"])
        self.assertEqual(validation_result.mail_outcomes[1].file_numbers_extracted, ["P/26/8002"])
        self.assertEqual(
            [
                (operation.row_index, operation.column_key, operation.expected_post_write_value)
                for operation in validation_result.staged_write_plan
            ],
            [
                (11, "ud_ip_shared", "BGMEA/DHK/UD/2026/5483/003"),
                (11, "ud_ip_date", "31/03/2026"),
                (11, "ud_recv_date", "01/04/2026"),
                (21, "ud_ip_shared", "BGMEA/DHK/AM/2026/3420/004-010"),
                (21, "ud_ip_date", "12/04/2026"),
                (21, "ud_recv_date", "01/04/2026"),
            ],
        )
        self.assertEqual(
            validation_result.mail_outcomes[0].saved_documents[0]["extracted_lc_sc_number"],
            "1345260400434",
        )
        self.assertEqual(
            validation_result.mail_outcomes[1].saved_documents[0]["extracted_lc_sc_number"],
            "201260400935",
        )
        self.assertTrue(validation_result.mail_outcomes[0].eligible_for_print)
        self.assertTrue(validation_result.mail_outcomes[1].eligible_for_mail_move)

    def test_prepare_live_ud_ip_exp_documents_hard_blocks_when_lc_sc_field_missing(self) -> None:
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
                    extracted_document_number="BGMEA/DHK/UD/2026/5483/003",
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

        self.assertEqual(len(result.document_save_result.issues), 1)
        self.assertEqual(result.document_save_result.issues[0].code, "document_storage_path_unresolved")
        self.assertEqual(result.document_save_result.issues[0].details["lc_sc_numbers"], [])
        self.assertEqual(
            result.document_save_result.issues[0].details["document_evidence"][0]["document_number"],
            "BGMEA/DHK/UD/2026/5483/003",
        )

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
                        extracted_document_number="BGMEA/DHK/UD/2026/5483/001",
                        extracted_document_date="2026-04-01",
                        extracted_lc_sc_number="LC-0043",
                        extracted_quantity="1000",
                        extracted_quantity_unit="YDS",
                    )
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="BGMEA/DHK/UD/2026/5483/999",
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
            ["BGMEA/DHK/UD/2026/5483/001", "BGMEA/DHK/UD/2026/5483/999"],
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
                    extracted_document_number="BGMEA/DHK/UD/2026/5483/003",
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
                "document_number": "BGMEA/DHK/UD/2026/5483/003",
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
                        extracted_document_number="BGMEA/DHK/UD/2026/5483/001",
                        extracted_document_date="2026-04-01",
                        extracted_lc_sc_number="LC-0043",
                        extracted_quantity="1000",
                        extracted_quantity_unit="YDS",
                    )
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="BGMEA/DHK/UD/2026/5483/999",
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
                erp_row_provider=_erp_provider(),
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
        self.assertEqual(discrepancy["details"]["expected_lc_sc_number"], "LC-0043")
        self.assertEqual(
            [evidence["document_number"] for evidence in discrepancy["details"]["conflicting_document_evidence"]],
            ["BGMEA/DHK/UD/2026/5483/999"],
        )

    def test_prepare_live_ud_ip_exp_documents_allows_multiple_same_family_ud_quantities(self) -> None:
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
                        extracted_document_number="BGMEA/DHK/UD/2026/5483/001",
                        extracted_document_date="2026-04-01",
                        extracted_lc_sc_number="LC-0043",
                        extracted_quantity="1000",
                        extracted_quantity_unit="YDS",
                    )
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="BGMEA/DHK/UD/2026/5483/002",
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

        self.assertEqual(result.document_save_result.issues, [])
        self.assertEqual(
            [document.document_number.value for document in result.classified_documents.documents],
            ["BGMEA/DHK/UD/2026/5483/001", "BGMEA/DHK/UD/2026/5483/002"],
        )
        self.assertEqual(
            [str(document.quantity.amount) for document in result.classified_documents.documents],
            ["1000", "1200"],
        )

    def test_validate_run_snapshot_processes_multiple_same_mail_ud_documents_in_date_order(self) -> None:
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
                        extracted_document_number="BGMEA/DHK/UD/2026/5483/001",
                        extracted_document_date="2026-04-01",
                        extracted_lc_sc_number="LC-0043",
                        extracted_quantity="1000",
                        extracted_quantity_unit="YDS",
                    )
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="BGMEA/DHK/UD/2026/5483/002",
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
                erp_row_provider=_erp_provider(),
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
                        ),
                        WorkbookRow(
                            row_index=12,
                            values={
                                1: "LC-0043",
                                2: "ANANTA GARMENTS LTD",
                                3: "2026-01-10",
                                4: "1000 YDS",
                                5: "",
                                6: "",
                                7: "",
                            },
                        ),
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

        self.assertEqual(validation_result.run_report.summary, {"pass": 1, "warning": 0, "hard_block": 0})
        self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.PASS)
        self.assertEqual(validation_result.mail_outcomes[0].discrepancies, [])
        self.assertEqual(
            [
                (operation.row_index, operation.expected_post_write_value)
                for operation in validation_result.staged_write_plan
            ],
            [
                (11, "BGMEA/DHK/UD/2026/5483/001"),
                (12, "BGMEA/DHK/UD/2026/5483/002"),
            ],
        )
        self.assertEqual(validation_result.mail_outcomes[0].ud_selection["document_count"], 2)
        self.assertEqual(validation_result.mail_outcomes[0].ud_selection["final_decision"], "selected")

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
                        extracted_document_number="BGMEA/DHK/UD/2026/5483/001",
                        extracted_document_date="2026-04-01",
                        extracted_lc_sc_number="LC-0043",
                    )
                return SavedDocumentAnalysis(
                    analysis_basis="fixture",
                    extracted_document_number="BGMEA/DHK/UD/2026/5483/002",
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
                erp_row_provider=_erp_provider(),
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
                    "document_number": "BGMEA/DHK/UD/2026/5483/001",
                    "missing_fields": ["quantity"],
                }
            ],
        )
        self.assertEqual(validation_result.mail_outcomes[0].ud_selection["document_count"], 2)
        self.assertEqual(validation_result.mail_outcomes[0].ud_selection["final_decision"], "hard_block")
        self.assertEqual(
            validation_result.mail_outcomes[0].ud_selection["documents"][1]["selection"]["required_quantity"],
            "1000",
        )
        self.assertEqual(
            validation_result.mail_outcomes[0].saved_documents[0]["extracted_document_number"],
            "BGMEA/DHK/UD/2026/5483/001",
        )
        self.assertEqual(
            validation_result.mail_outcomes[0].saved_documents[1]["extracted_document_number"],
            "BGMEA/DHK/UD/2026/5483/002",
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
                    erp_row_provider=_erp_provider(),
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
            if item["code"] == "ud_document_number_pattern_mismatch"
        )
        self.assertEqual(discrepancy["details"]["extracted_document_number"], "UD-LC-0043-ANANTA")
        self.assertIsNone(validation_result.mail_outcomes[0].saved_documents[0]["extracted_document_date"])
        self.assertIsNone(validation_result.mail_outcomes[0].ud_selection)
        self.assertEqual(validation_result.staged_write_plan, [])


def _mail(entry_id: str, subject: str, *, attachments: list[dict] | None = None):
    return build_email_snapshot(
        [
            SourceEmailRecord(
                entry_id=entry_id,
                received_time="2026-04-01T03:00:00Z",
                subject_raw=subject,
                sender_address="sender@example.com",
                body_text="Please process commercial file P/26/0042.",
                attachments=[
                    SourceAttachmentRecord(attachment_name=attachment["attachment_name"])
                    for attachment in (attachments or [])
                ],
            )
        ],
        state_timezone="Asia/Dhaka",
    )[0]


def _mail_with_body_file(entry_id: str, *, file_number: str, attachment_name: str):
    return build_email_snapshot(
        [
            SourceEmailRecord(
                entry_id=entry_id,
                received_time="2026-04-01T03:00:00Z",
                subject_raw="Subject intentionally ignored for UD/IP/EXP",
                sender_address="sender@example.com",
                body_text=f"Please process commercial file {file_number}.",
                attachments=[SourceAttachmentRecord(attachment_name=attachment_name)],
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


def _structured_workbook_snapshot() -> WorkbookSnapshot:
    return WorkbookSnapshot(
        sheet_name="Sheet1",
        headers=[
            WorkbookHeader(column_index=1, text="L/C & S/C No."),
            WorkbookHeader(column_index=2, text="Quantity of Fabrics (Yds/Mtr)"),
            WorkbookHeader(column_index=3, text="UD No. & IP No."),
            WorkbookHeader(column_index=4, text="L/C Amnd No."),
            WorkbookHeader(column_index=5, text="L/C Amnd Date"),
            WorkbookHeader(column_index=6, text="Amount"),
            WorkbookHeader(column_index=7, text="UD & IP Date"),
            WorkbookHeader(column_index=8, text="UD Recv. Date"),
        ],
        rows=[
            WorkbookRow(
                row_index=11,
                values={
                    1: "1345260400434",
                    2: "6633 YDS",
                    3: "",
                    4: "",
                    5: "",
                    6: "17375.80",
                    7: "",
                    8: "",
                },
            ),
            WorkbookRow(
                row_index=21,
                values={
                    1: "201260400935",
                    2: "21390 YDS",
                    3: "",
                    4: "",
                    5: "",
                    6: "69734.70",
                    7: "",
                    8: "",
                },
            ),
        ],
    )


class _ERPProvider:
    def __init__(self, *, buyer_name: str = "ANANTA GARMENTS LTD", lc_sc_date: str = "2026-01-10") -> None:
        self.buyer_name = buyer_name
        self.lc_sc_date = lc_sc_date

    def lookup_rows(self, *, file_numbers):
        row = ERPRegisterRow(
            file_number="P/26/0042",
            lc_sc_number="LC-0043",
            buyer_name=self.buyer_name,
            lc_sc_date=self.lc_sc_date,
            source_row_index=1,
            lc_qty="1000",
            lc_unit="YDS",
        )
        return {file_number: [row] if file_number == "P/26/0042" else [] for file_number in file_numbers}


def _erp_provider(*, buyer_name: str = "ANANTA GARMENTS LTD", lc_sc_date: str = "2026-01-10"):
    return _ERPProvider(buyer_name=buyer_name, lc_sc_date=lc_sc_date)


class _StructuredERPProvider:
    def lookup_rows(self, *, file_numbers):
        rows = {
            "P/26/7001": ERPRegisterRow(
                file_number="P/26/7001",
                lc_sc_number="1345260400434",
                buyer_name="NALIN TEX LTD",
                lc_sc_date="2026-03-16",
                source_row_index=43,
                folder_buyer_name="NALIN TEX LTD",
            ),
            "P/26/8002": ERPRegisterRow(
                file_number="P/26/8002",
                lc_sc_number="201260400935",
                buyer_name="A.K.M. KNIT WEAR LTD",
                lc_sc_date="2026-03-09",
                source_row_index=93,
                folder_buyer_name="A.K.M. KNIT WEAR LTD",
            ),
        }
        return {
            file_number: [rows[file_number]] if file_number in rows else []
            for file_number in file_numbers
        }


def _structured_erp_provider():
    return _StructuredERPProvider()


def _base_structured_report() -> dict:
    return {
        "combined_text": "UD Authenticating Authority",
        "pages": [
            {
                "page_number": 1,
                "searchable_text": "UD Authenticating Authority",
                "tables": [
                    {"table_index": 1, "rows": [["01.", "Name"]]},
                    {
                        "table_index": 2,
                        "rows": [
                            ["03. Application No", "2603310081", "Date", "2026-03-31"],
                            [
                                "04. UD No (For office use only)",
                                "BGMEA/DHK/UD/2026/5483/003",
                                "Date",
                                "2026-03-31",
                            ],
                        ],
                    },
                    {
                        "table_index": 3,
                        "rows": [
                            ["SL No", "32. Import L/C No.", "33. Date", "34. Value", "Used Value", "35. Currency"],
                            ["1", "1345260400434", "2026-03-16", "17375.8", "17375.8", "USD"],
                        ],
                    },
                    {
                        "table_index": 4,
                        "rows": [
                            ["Fabric Description", "Qty", "Unit", "Net Weight", "Unit", "Country", "Supplierinfo"],
                            ["DENIM", "1300", "YRD", "0", "KGM", "Bangladesh", "PIONEER DENIM LIMITED"],
                            ["DENIM", "5333", "YRD", "0", "KGM", "Bangladesh", "DO"],
                            ["Total", "6633", "YRD", "", "", "", ""],
                        ],
                    },
                ],
            }
        ],
    }


def _amendment_structured_report() -> dict:
    return {
        "combined_text": "Amendment Authenticating Authority",
        "pages": [
            {
                "page_number": 1,
                "searchable_text": "Amendment Authenticating Authority",
                "tables": [
                    {"table_index": 1, "rows": [["01.", "Name"]]},
                    {
                        "table_index": 2,
                        "rows": [
                            ["UD No.: BGMEA/DHK/UD/2026/3420/004", "Date", "2026-01-18"],
                            [
                                "Amendment no. (For office use only)",
                                "BGMEA/DHK/AM/2026/3420/004-010",
                                "Date",
                                "2026-04-12",
                            ],
                        ],
                    },
                    {
                        "table_index": 3,
                        "rows": [
                            [
                                "SL No",
                                "Back-to-Back LC/Sight/Usance",
                                "Date",
                                "Value",
                                "Increased/Decreased",
                                "Total Value",
                            ],
                            ["7", "201260400935", "2026-03-09", "USD 89,675.00", "USD 69,734.70", "USD 159,409.70"],
                        ],
                    },
                    {
                        "table_index": 4,
                        "rows": [
                            ["Fabric/Yarn Description", "Qty", "Unit", "Net Weight", "Unit", "Country Name", "Supplier Info"],
                            ["DENIM", "410", "YRD", "0", "KGM", "Bangladesh", "PIONEER DENIM LIMITED"],
                            ["DENIM", "20980", "YRD", "0", "KGM", "Bangladesh", "DO"],
                        ],
                    },
                ],
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
