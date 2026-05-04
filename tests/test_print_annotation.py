from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project.models import (
    MailMovePhaseStatus,
    MailOutcomeRecord,
    MailProcessingStatus,
    PrintBatch,
    PrintPhaseStatus,
    RunReport,
    WorkflowId,
    WritePhaseStatus,
)
from project.storage import create_run_artifact_layout
from project.workbook import WorkbookHeader, WorkbookRow, WorkbookSnapshot
from project.workflows.print_annotation import (
    PrintAnnotationChecklistError,
    build_print_annotation_checklist,
    persist_print_annotation_checklist,
    validate_print_annotation_checklist,
)


class PrintAnnotationChecklistTests(unittest.TestCase):
    def test_build_print_annotation_checklist_uses_planned_print_order_and_resolves_sl_no_values(self) -> None:
        run_report = _build_run_report()
        mail_outcomes = [
            MailOutcomeRecord(
                run_id="run-1",
                mail_id="mail-1",
                workflow_id=WorkflowId.UD_IP_EXP,
                snapshot_index=0,
                processing_status=MailProcessingStatus.WRITTEN,
                final_decision=None,
                decision_reasons=[],
                eligible_for_write=False,
                eligible_for_print=True,
                eligible_for_mail_move=True,
                source_entry_id="entry-1",
                subject_raw="UD subject",
                sender_address="ud@example.com",
                saved_documents=[
                    {
                        "saved_document_id": "doc-1",
                        "normalized_filename": "UD-ONE.pdf",
                        "destination_path": "C:/docs/UD-ONE.pdf",
                        "extracted_document_number": "BGMEA/DHK/UD/2026/1001",
                    },
                    {
                        "saved_document_id": "doc-2",
                        "normalized_filename": "UD-TWO.pdf",
                        "destination_path": "C:/docs/UD-TWO.pdf",
                        "extracted_document_number": "BGMEA/DHK/AM/2026/1002",
                    },
                ],
                ud_selection={
                    "document_count": 2,
                    "final_decision": "selected",
                    "documents": [
                        {
                            "document_index": 0,
                            "document_number": "BGMEA/DHK/UD/2026/1001",
                            "source_saved_document_id": "doc-1",
                            "selection": {
                                "candidates": [
                                    {"selected": True, "row_indexes": [11, 12]},
                                ]
                            },
                        },
                        {
                            "document_index": 1,
                            "document_number": "BGMEA/DHK/AM/2026/1002",
                            "source_saved_document_id": "doc-2",
                            "selection": {
                                "candidates": [
                                    {"selected": True, "row_indexes": [13]},
                                ]
                            },
                        },
                    ],
                },
            )
        ]
        print_batches = [
            PrintBatch(
                print_group_id="group-1",
                run_id="run-1",
                mail_id="mail-1",
                print_group_index=0,
                document_paths=["C:/docs/UD-ONE.pdf", "C:/docs/UD-TWO.pdf"],
                document_path_hashes=["hash-1", "hash-2"],
                completion_marker_id="completion-1",
                manual_verification_summary={},
            )
        ]
        workbook_snapshot = WorkbookSnapshot(
            sheet_name="Sheet1",
            headers=[
                WorkbookHeader(column_index=1, text="SL.No."),
                WorkbookHeader(column_index=2, text="L/C & S/C No."),
                WorkbookHeader(column_index=3, text="Bangladesh Bank Ref."),
            ],
            rows=[
                WorkbookRow(row_index=11, values={1: "17", 2: "LC-0043", 3: "BB-001"}),
                WorkbookRow(row_index=12, values={1: "18", 2: "LC-0043", 3: "BB-001"}),
                WorkbookRow(row_index=13, values={1: "21A", 2: "LC-0043", 3: "BB-002"}),
            ],
        )

        result = build_print_annotation_checklist(
            run_report=run_report,
            mail_outcomes=mail_outcomes,
            print_batches=print_batches,
            workbook_snapshot=workbook_snapshot,
        )

        self.assertEqual(result.payload["checklist_row_count"], 2)
        self.assertEqual(result.payload["rows"][0]["print_sequence"], 1)
        self.assertEqual(result.payload["rows"][0]["ud_or_amendment_no"], "BGMEA/DHK/UD/2026/1001")
        self.assertEqual(result.payload["rows"][0]["lc_sc"], "LC-0043")
        self.assertEqual(result.payload["rows"][0]["bangladesh_bank_ref"], "BB-001")
        self.assertEqual(result.payload["rows"][0]["sl_no_values"], ["17", "18"])
        self.assertEqual(result.payload["rows"][1]["print_sequence"], 2)
        self.assertEqual(result.payload["rows"][1]["bangladesh_bank_ref"], "BB-002")
        self.assertEqual(result.payload["rows"][1]["sl_no_values"], ["21A"])
        self.assertIn("Print Annotation Checklist", result.html)

    def test_build_print_annotation_checklist_raises_when_sl_no_value_is_missing(self) -> None:
        workbook_snapshot = WorkbookSnapshot(
            sheet_name="Sheet1",
            headers=[
                WorkbookHeader(column_index=1, text="SL.No."),
                WorkbookHeader(column_index=2, text="L/C & S/C No."),
                WorkbookHeader(column_index=3, text="Bangladesh Bank Ref."),
            ],
            rows=[WorkbookRow(row_index=11, values={1: "", 2: "LC-0043", 3: "BB-001"})],
        )

        with self.assertRaises(PrintAnnotationChecklistError) as ctx:
            build_print_annotation_checklist(
                run_report=_build_run_report(),
                mail_outcomes=[_build_single_document_mail_outcome()],
                print_batches=[_build_single_document_batch()],
                workbook_snapshot=workbook_snapshot,
            )

        self.assertEqual(ctx.exception.code, "print_annotation_sl_no_unresolved")

    def test_validate_print_annotation_checklist_accepts_generated_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="ud_ip_exp",
                run_id="run-1",
            )
            run_report = _build_run_report()
            mail_outcomes = [_build_single_document_mail_outcome()]
            print_batches = [_build_single_document_batch()]
            workbook_snapshot = WorkbookSnapshot(
                sheet_name="Sheet1",
                headers=[
                    WorkbookHeader(column_index=1, text="SL.No."),
                    WorkbookHeader(column_index=2, text="L/C & S/C No."),
                    WorkbookHeader(column_index=3, text="Bangladesh Bank Ref."),
                ],
                rows=[WorkbookRow(row_index=11, values={1: "17", 2: "LC-0043", 3: "BB-001"})],
            )

            result = build_print_annotation_checklist(
                run_report=run_report,
                mail_outcomes=mail_outcomes,
                print_batches=print_batches,
                workbook_snapshot=workbook_snapshot,
            )
            persist_print_annotation_checklist(
                artifact_paths=artifact_paths,
                result=result,
            )

            validate_print_annotation_checklist(
                artifact_paths=artifact_paths,
                run_report=run_report,
                print_batches=print_batches,
                mail_outcomes=mail_outcomes,
            )

    def test_build_print_annotation_checklist_skips_supporting_pdfs_without_ud_selection(self) -> None:
        run_report = _build_run_report()
        mail_outcomes = [
            MailOutcomeRecord(
                run_id="run-1",
                mail_id="mail-1",
                workflow_id=WorkflowId.UD_IP_EXP,
                snapshot_index=0,
                processing_status=MailProcessingStatus.WRITTEN,
                final_decision=None,
                decision_reasons=[],
                eligible_for_write=False,
                eligible_for_print=True,
                eligible_for_mail_move=True,
                source_entry_id="entry-1",
                subject_raw="UD subject",
                sender_address="ud@example.com",
                saved_documents=[
                    {
                        "saved_document_id": "doc-1",
                        "normalized_filename": "UD-ONE.pdf",
                        "destination_path": "C:/docs/UD-ONE.pdf",
                        "document_type": "ud_document",
                        "extracted_document_number": "BGMEA/DHK/UD/2026/1001",
                    },
                    {
                        "saved_document_id": "doc-2",
                        "normalized_filename": "supporting.pdf",
                        "destination_path": "C:/docs/supporting.pdf",
                        "document_type": "supporting_pdf",
                    },
                ],
                ud_selection={
                    "document_count": 1,
                    "final_decision": "selected",
                    "documents": [
                        {
                            "document_index": 0,
                            "document_number": "BGMEA/DHK/UD/2026/1001",
                            "source_saved_document_id": "doc-1",
                            "selection": {
                                "candidates": [
                                    {"selected": True, "row_indexes": [11]},
                                ]
                            },
                        },
                    ],
                },
            )
        ]
        print_batches = [
            PrintBatch(
                print_group_id="group-1",
                run_id="run-1",
                mail_id="mail-1",
                print_group_index=0,
                document_paths=["C:/docs/UD-ONE.pdf", "C:/docs/supporting.pdf"],
                document_path_hashes=["hash-1", "hash-2"],
                completion_marker_id="completion-1",
                manual_verification_summary={},
            )
        ]
        workbook_snapshot = WorkbookSnapshot(
            sheet_name="Sheet1",
            headers=[
                WorkbookHeader(column_index=1, text="SL.No."),
                WorkbookHeader(column_index=2, text="L/C & S/C No."),
                WorkbookHeader(column_index=3, text="Bangladesh Bank Ref."),
            ],
            rows=[WorkbookRow(row_index=11, values={1: "17", 2: "LC-0043", 3: "BB-001"})],
        )

        result = build_print_annotation_checklist(
            run_report=run_report,
            mail_outcomes=mail_outcomes,
            print_batches=print_batches,
            workbook_snapshot=workbook_snapshot,
        )

        self.assertEqual(result.payload["checklist_row_count"], 1)
        self.assertEqual([row["document_filename"] for row in result.payload["rows"]], ["UD-ONE.pdf"])

    def test_validate_print_annotation_checklist_accepts_ud_only_subset_of_printed_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="ud_ip_exp",
                run_id="run-1",
            )
            run_report = _build_run_report()
            mail_outcomes = [
                MailOutcomeRecord(
                    run_id="run-1",
                    mail_id="mail-1",
                    workflow_id=WorkflowId.UD_IP_EXP,
                    snapshot_index=0,
                    processing_status=MailProcessingStatus.WRITTEN,
                    final_decision=None,
                    decision_reasons=[],
                    eligible_for_write=False,
                    eligible_for_print=True,
                    eligible_for_mail_move=True,
                    source_entry_id="entry-1",
                    subject_raw="UD subject",
                    sender_address="ud@example.com",
                    saved_documents=[
                        {
                            "saved_document_id": "doc-1",
                            "normalized_filename": "UD-ONE.pdf",
                            "destination_path": "C:/docs/UD-ONE.pdf",
                            "document_type": "ud_document",
                            "extracted_document_number": "BGMEA/DHK/UD/2026/1001",
                        },
                        {
                            "saved_document_id": "doc-2",
                            "normalized_filename": "supporting.pdf",
                            "destination_path": "C:/docs/supporting.pdf",
                            "document_type": "supporting_pdf",
                        },
                    ],
                    ud_selection={
                        "documents": [
                            {
                                "document_index": 0,
                                "document_number": "BGMEA/DHK/UD/2026/1001",
                                "source_saved_document_id": "doc-1",
                                "selection": {
                                    "candidates": [
                                        {"selected": True, "row_indexes": [11]},
                                    ]
                                },
                            },
                        ],
                    },
                )
            ]
            print_batches = [
                PrintBatch(
                    print_group_id="group-1",
                    run_id="run-1",
                    mail_id="mail-1",
                    print_group_index=0,
                    document_paths=["C:/docs/UD-ONE.pdf", "C:/docs/supporting.pdf"],
                    document_path_hashes=["hash-1", "hash-2"],
                    completion_marker_id="completion-1",
                    manual_verification_summary={},
                )
            ]
            workbook_snapshot = WorkbookSnapshot(
                sheet_name="Sheet1",
                headers=[
                    WorkbookHeader(column_index=1, text="SL.No."),
                    WorkbookHeader(column_index=2, text="L/C & S/C No."),
                    WorkbookHeader(column_index=3, text="Bangladesh Bank Ref."),
                ],
                rows=[WorkbookRow(row_index=11, values={1: "17", 2: "LC-0043", 3: "BB-001"})],
            )
            result = build_print_annotation_checklist(
                run_report=run_report,
                mail_outcomes=mail_outcomes,
                print_batches=print_batches,
                workbook_snapshot=workbook_snapshot,
            )
            persist_print_annotation_checklist(
                artifact_paths=artifact_paths,
                result=result,
            )

            validate_print_annotation_checklist(
                artifact_paths=artifact_paths,
                run_report=run_report,
                print_batches=print_batches,
                mail_outcomes=mail_outcomes,
            )

    def test_build_print_annotation_checklist_prefers_persisted_annotation_documents(self) -> None:
        workbook_snapshot = WorkbookSnapshot(
            sheet_name="Sheet1",
            headers=[
                WorkbookHeader(column_index=1, text="SL.No."),
                WorkbookHeader(column_index=2, text="L/C & S/C No."),
                WorkbookHeader(column_index=3, text="Bangladesh Bank Ref."),
            ],
            rows=[
                WorkbookRow(row_index=11, values={1: "17", 2: "LC-0043", 3: "BB-001"}),
            ],
        )
        mail_outcome = MailOutcomeRecord(
            run_id="run-1",
            mail_id="mail-1",
            workflow_id=WorkflowId.UD_IP_EXP,
            snapshot_index=0,
            processing_status=MailProcessingStatus.WRITTEN,
            final_decision=None,
            decision_reasons=[],
            eligible_for_write=False,
            eligible_for_print=True,
            eligible_for_mail_move=True,
            source_entry_id="entry-1",
            subject_raw="UD subject",
            sender_address="ud@example.com",
            saved_documents=[
                {
                    "saved_document_id": "doc-1",
                    "normalized_filename": "UD-ONE.pdf",
                    "destination_path": "C:/docs/UD-ONE.pdf",
                },
                {
                    "saved_document_id": "doc-2",
                    "normalized_filename": "supporting.pdf",
                    "destination_path": "C:/docs/supporting.pdf",
                },
            ],
            staged_write_operations=[],
            ud_selection=None,
        )
        print_batches = [
            PrintBatch(
                print_group_id="group-1",
                run_id="run-1",
                mail_id="mail-1",
                print_group_index=0,
                document_paths=["C:/docs/UD-ONE.pdf", "C:/docs/supporting.pdf"],
                document_path_hashes=["hash-1", "hash-2"],
                completion_marker_id="completion-1",
                manual_verification_summary={},
                annotation_documents=[
                    {
                        "saved_document_id": "doc-1",
                        "document_path": "C:/docs/UD-ONE.pdf",
                        "document_path_hash": "hash-1",
                        "document_filename": "UD-ONE.pdf",
                        "document_number": "BGMEA/DHK/UD/2026/1001",
                        "row_indexes": [11],
                        "checklist_required": True,
                    },
                    {
                        "saved_document_id": "doc-2",
                        "document_path": "C:/docs/supporting.pdf",
                        "document_path_hash": "hash-2",
                        "document_filename": "supporting.pdf",
                        "document_number": "",
                        "row_indexes": [],
                        "checklist_required": False,
                    },
                ],
            )
        ]

        result = build_print_annotation_checklist(
            run_report=_build_run_report(),
            mail_outcomes=[mail_outcome],
            print_batches=print_batches,
            workbook_snapshot=workbook_snapshot,
        )

        self.assertEqual(result.payload["checklist_row_count"], 1)
        self.assertEqual(result.payload["rows"][0]["document_filename"], "UD-ONE.pdf")
        self.assertEqual(result.payload["rows"][0]["ud_or_amendment_no"], "BGMEA/DHK/UD/2026/1001")
        self.assertEqual(result.payload["rows"][0]["row_indexes"], [11])

    def test_build_print_annotation_checklist_falls_back_to_staged_write_rows_for_single_document_runs(self) -> None:
        workbook_snapshot = WorkbookSnapshot(
            sheet_name="Sheet1",
            headers=[
                WorkbookHeader(column_index=1, text="SL.No."),
                WorkbookHeader(column_index=2, text="L/C & S/C No."),
                WorkbookHeader(column_index=3, text="Bangladesh Bank Ref."),
            ],
            rows=[WorkbookRow(row_index=11, values={1: "17", 2: "LC-0043", 3: "BB-001"})],
        )
        mail_outcome = MailOutcomeRecord(
            run_id="run-1",
            mail_id="mail-1",
            workflow_id=WorkflowId.UD_IP_EXP,
            snapshot_index=0,
            processing_status=MailProcessingStatus.WRITTEN,
            final_decision=None,
            decision_reasons=[],
            eligible_for_write=False,
            eligible_for_print=True,
            eligible_for_mail_move=True,
            source_entry_id="entry-1",
            subject_raw="UD subject",
            sender_address="ud@example.com",
            saved_documents=[
                {
                    "saved_document_id": "doc-1",
                    "normalized_filename": "UD-ONE.pdf",
                    "destination_path": "C:/docs/UD-ONE.pdf",
                    "extracted_document_number": "BGMEA/DHK/UD/2026/1001",
                }
            ],
            staged_write_operations=[
                {
                    "row_index": 11,
                    "column_key": "ud_ip_shared",
                },
                {
                    "row_index": 11,
                    "column_key": "ud_ip_date",
                },
            ],
            ud_selection=None,
        )

        result = build_print_annotation_checklist(
            run_report=_build_run_report(),
            mail_outcomes=[mail_outcome],
            print_batches=[_build_single_document_batch()],
            workbook_snapshot=workbook_snapshot,
        )

        self.assertEqual(result.payload["checklist_row_count"], 1)
        self.assertEqual(result.payload["rows"][0]["lc_sc"], "LC-0043")
        self.assertEqual(result.payload["rows"][0]["bangladesh_bank_ref"], "BB-001")
        self.assertEqual(result.payload["rows"][0]["sl_no_values"], ["17"])
        self.assertEqual(result.payload["rows"][0]["row_indexes"], [11])

    def test_build_print_annotation_checklist_falls_back_to_staged_write_rows_for_multiple_documents(self) -> None:
        workbook_snapshot = WorkbookSnapshot(
            sheet_name="Sheet1",
            headers=[
                WorkbookHeader(column_index=1, text="SL.No."),
                WorkbookHeader(column_index=2, text="L/C & S/C No."),
                WorkbookHeader(column_index=3, text="Bangladesh Bank Ref."),
            ],
            rows=[
                WorkbookRow(row_index=11, values={1: "17", 2: "LC-0043", 3: "BB-001"}),
                WorkbookRow(row_index=12, values={1: "18", 2: "LC-0043", 3: "BB-002"}),
            ],
        )
        mail_outcome = MailOutcomeRecord(
            run_id="run-1",
            mail_id="mail-1",
            workflow_id=WorkflowId.UD_IP_EXP,
            snapshot_index=0,
            processing_status=MailProcessingStatus.WRITTEN,
            final_decision=None,
            decision_reasons=[],
            eligible_for_write=False,
            eligible_for_print=True,
            eligible_for_mail_move=True,
            source_entry_id="entry-1",
            subject_raw="UD subject",
            sender_address="ud@example.com",
            saved_documents=[
                {
                    "saved_document_id": "doc-1",
                    "normalized_filename": "UD-ONE.pdf",
                    "destination_path": "C:/docs/UD-ONE.pdf",
                    "extracted_document_number": "BGMEA/DHK/UD/2026/1001",
                },
                {
                    "saved_document_id": "doc-2",
                    "normalized_filename": "UD-TWO.pdf",
                    "destination_path": "C:/docs/UD-TWO.pdf",
                    "extracted_document_number": "BGMEA/DHK/AM/2026/1002",
                },
            ],
            staged_write_operations=[
                {
                    "row_index": 11,
                    "column_key": "ud_ip_shared",
                    "expected_post_write_value": "BGMEA/DHK/UD/2026/1001",
                },
                {
                    "row_index": 11,
                    "column_key": "ud_ip_date",
                    "expected_post_write_value": "01/04/2026",
                },
                {
                    "row_index": 12,
                    "column_key": "ud_ip_shared",
                    "expected_post_write_value": "BGMEA/DHK/AM/2026/1002",
                },
            ],
            ud_selection=None,
        )
        print_batches = [
            PrintBatch(
                print_group_id="group-1",
                run_id="run-1",
                mail_id="mail-1",
                print_group_index=0,
                document_paths=["C:/docs/UD-ONE.pdf", "C:/docs/UD-TWO.pdf"],
                document_path_hashes=["hash-1", "hash-2"],
                completion_marker_id="completion-1",
                manual_verification_summary={},
            )
        ]

        result = build_print_annotation_checklist(
            run_report=_build_run_report(),
            mail_outcomes=[mail_outcome],
            print_batches=print_batches,
            workbook_snapshot=workbook_snapshot,
        )

        self.assertEqual(result.payload["checklist_row_count"], 2)
        self.assertEqual(result.payload["rows"][0]["row_indexes"], [11])
        self.assertEqual(result.payload["rows"][0]["ud_or_amendment_no"], "BGMEA/DHK/UD/2026/1001")
        self.assertEqual(result.payload["rows"][1]["row_indexes"], [12])
        self.assertEqual(result.payload["rows"][1]["ud_or_amendment_no"], "BGMEA/DHK/AM/2026/1002")


def _build_run_report() -> RunReport:
    return RunReport(
        run_id="run-1",
        workflow_id=WorkflowId.UD_IP_EXP,
        tool_version="0.1.0",
        rule_pack_id="ud_ip_exp.default",
        rule_pack_version="1.0.0",
        started_at_utc="2026-05-01T00:00:00Z",
        completed_at_utc=None,
        state_timezone="Asia/Dhaka",
        mail_iteration_order=["mail-1"],
        print_group_order=["group-1"],
        write_phase_status=WritePhaseStatus.COMMITTED,
        print_phase_status=PrintPhaseStatus.PLANNED,
        mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
        hash_algorithm="sha256",
        run_start_backup_hash="a" * 64,
        current_workbook_hash="b" * 64,
        staged_write_plan_hash="c" * 64,
        summary={"pass": 1, "warning": 0, "hard_block": 0},
    )


def _build_single_document_mail_outcome() -> MailOutcomeRecord:
    return MailOutcomeRecord(
        run_id="run-1",
        mail_id="mail-1",
        workflow_id=WorkflowId.UD_IP_EXP,
        snapshot_index=0,
        processing_status=MailProcessingStatus.WRITTEN,
        final_decision=None,
        decision_reasons=[],
        eligible_for_write=False,
        eligible_for_print=True,
        eligible_for_mail_move=True,
        source_entry_id="entry-1",
        subject_raw="UD subject",
        sender_address="ud@example.com",
        saved_documents=[
            {
                "saved_document_id": "doc-1",
                "normalized_filename": "UD-ONE.pdf",
                "destination_path": "C:/docs/UD-ONE.pdf",
                "extracted_document_number": "BGMEA/DHK/UD/2026/1001",
            }
        ],
        ud_selection={
            "candidates": [
                {"selected": True, "row_indexes": [11]},
            ],
            "final_decision": "selected",
        },
    )


def _build_single_document_batch() -> PrintBatch:
    return PrintBatch(
        print_group_id="group-1",
        run_id="run-1",
        mail_id="mail-1",
        print_group_index=0,
        document_paths=["C:/docs/UD-ONE.pdf"],
        document_path_hashes=["hash-1"],
        completion_marker_id="completion-1",
        manual_verification_summary={},
    )


if __name__ == "__main__":
    unittest.main()
