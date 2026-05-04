from __future__ import annotations

import json
from decimal import Decimal
import tempfile
import unittest
from pathlib import Path

from project.erp import ERPRegisterRow
from project.models import (
    FinalDecision,
    RunReport,
    WorkflowId,
    WritePhaseStatus,
    PrintPhaseStatus,
    MailMovePhaseStatus,
)
from project.rules import load_rule_pack
from project.workbook import WorkbookHeader, WorkbookRow, WorkbookSnapshot
from project.workflows.registry import get_workflow_descriptor
from project.workflows.snapshot import SourceEmailRecord, build_email_snapshot
from project.workflows.ud_ip_exp import (
    DocumentExtractionField,
    EXPDocumentPayload,
    IPDocumentPayload,
    JsonManifestUDDocumentPayloadProvider,
    MappingUDDocumentPayloadProvider,
    UDDocumentPayload,
    UDIPEXPQuantity,
)
from project.workflows.validation import validate_run_snapshot


class UDIPEXPManifestValidationTests(unittest.TestCase):
    def test_json_manifest_ud_document_provider_loads_payload_by_entry_id(self) -> None:
        mail = _mail("entry-ud-001", "UD-LC-0043-ANANTA")
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "ud-payloads.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-ud-001",
                            "document_number": "UD-LC-0043-ANANTA",
                            "document_date": "2026-04-01",
                            "lc_sc_number": "LC-0043",
                            "quantity": "1000",
                            "quantity_unit": "YDS",
                            "document_number_confidence": 0.99,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            provider = JsonManifestUDDocumentPayloadProvider(manifest_path)
            payload = provider.get_ud_document(mail)

        self.assertIsNotNone(payload)
        self.assertEqual(payload.document_number.value, "UD-LC-0043-ANANTA")
        self.assertEqual(payload.document_number.confidence, 0.99)
        self.assertEqual(payload.quantity.amount, Decimal("1000"))

    def test_json_manifest_provider_canonicalizes_document_number(self) -> None:
        mail = _mail("entry-ud-001", "UD-LC-0043-ANANTA")
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "ud-payloads.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-ud-001",
                            "document_kind": "IP",
                            "document_number": "ip lc 0043 vintage denim studio ltd.",
                            "document_date": "2026-04-03",
                            "lc_sc_number": "LC-0043",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            provider = JsonManifestUDDocumentPayloadProvider(manifest_path)
            documents = provider.get_documents(mail)

        self.assertEqual(documents[0].document_number.value, "IP-LC-0043-VINTAGE DENIM STUDIO LTD")

    def test_json_manifest_provider_rejects_document_kind_number_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "ud-payloads.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-ud-001",
                            "document_kind": "UD",
                            "document_number": "IP-LC-0043-VINTAGE DENIM STUDIO LTD",
                            "document_date": "2026-04-03",
                            "lc_sc_number": "LC-0043",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "does not match document_number"):
                JsonManifestUDDocumentPayloadProvider(manifest_path)

    def test_json_manifest_provider_loads_multiple_document_kinds_for_one_mail(self) -> None:
        mail = _mail("entry-ud-001", "UD-LC-0043-ANANTA")
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "ud-payloads.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-ud-001",
                            "document_kind": "UD",
                            "document_number": "UD-LC-0043-ANANTA",
                            "document_date": "2026-04-01",
                            "lc_sc_number": "LC-0043",
                            "quantity": "1000",
                            "quantity_unit": "YDS",
                        },
                        {
                            "entry_id": "entry-ud-001",
                            "document_kind": "EXP",
                            "document_number": "EXP-001",
                            "document_date": "2026-04-02",
                            "lc_sc_number": "LC-0043",
                        },
                        {
                            "entry_id": "entry-ud-001",
                            "document_kind": "IP",
                            "document_number": "IP-002",
                            "document_date": "2026-04-03",
                            "lc_sc_number": "LC-0043",
                        },
                    ]
                ),
                encoding="utf-8",
            )

            provider = JsonManifestUDDocumentPayloadProvider(manifest_path)
            documents = provider.get_documents(mail)
            ud_payload = provider.get_ud_document(mail)

        self.assertEqual([document.document_kind.value for document in documents], ["UD", "EXP", "IP"])
        self.assertIsNotNone(ud_payload)
        self.assertEqual(ud_payload.document_number.value, "UD-LC-0043-ANANTA")

    def test_validate_run_snapshot_for_ud_manifest_stages_writes_and_reports_selection(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        mail = _mail("entry-ud-001", "UD-LC-0043-ANANTA")
        validation_result = validate_run_snapshot(
            descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
            run_report=_run_report(rule_pack, [mail]),
            rule_pack=rule_pack,
            erp_row_provider=_erp_provider(),
            workbook_snapshot=_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000 YDS", 3: "", 4: "", 5: ""}),
                ]
            ),
            ud_document_provider=MappingUDDocumentPayloadProvider(
                {mail.entry_id: _ud_document("BGMEA/DHK/UD/2026/5483/003", quantity=Decimal("1000"))}
            ),
        )

        self.assertEqual(validation_result.run_report.summary, {"pass": 1, "warning": 0, "hard_block": 0})
        self.assertEqual(len(validation_result.staged_write_plan), 1)
        self.assertEqual(validation_result.staged_write_plan[0].row_index, 11)
        self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.PASS)
        self.assertTrue(validation_result.mail_outcomes[0].eligible_for_write)
        self.assertTrue(validation_result.mail_outcomes[0].eligible_for_print)
        self.assertTrue(validation_result.mail_outcomes[0].eligible_for_mail_move)
        self.assertEqual(validation_result.mail_outcomes[0].file_numbers_extracted, ["P/26/0042"])
        self.assertEqual(validation_result.mail_outcomes[0].ud_selection["selected_candidate_id"], "11")
        self.assertEqual(validation_result.mail_reports[0].ud_selection["final_decision"], "selected")
        self.assertEqual(validation_result.discrepancy_reports, [])

    def test_validate_run_snapshot_marks_already_recorded_ud_as_duplicate_noop(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        mail = _mail("entry-ud-001", "UD-LC-0043-ANANTA")
        ud_document = UDDocumentPayload(
            document_number=DocumentExtractionField("BGMEA/DHK/UD/2026/5483/003"),
            document_date=DocumentExtractionField("2026-03-31"),
            lc_sc_number=DocumentExtractionField("LC-0043"),
            lc_sc_date=DocumentExtractionField("2026-01-10"),
            lc_sc_value=DocumentExtractionField("1000"),
            quantity_by_unit={"YDS": Decimal("1000")},
        )

        validation_result = validate_run_snapshot(
            descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
            run_report=_run_report(rule_pack, [mail]),
            rule_pack=rule_pack,
            erp_row_provider=_erp_provider(),
            workbook_snapshot=_structured_snapshot(
                rows=[
                    WorkbookRow(
                        row_index=11,
                        values={
                            1: "LC-0043",
                            2: "1000 YDS",
                            3: "BGMEA/DHK/UD/2026/5483/003",
                            4: "",
                            5: "",
                            6: "1000",
                            7: "31/03/2026",
                            8: "01/04/2026",
                        },
                    ),
                ]
            ),
            ud_document_provider=MappingUDDocumentPayloadProvider({mail.entry_id: ud_document}),
        )

        self.assertEqual(validation_result.run_report.summary, {"pass": 1, "warning": 0, "hard_block": 0})
        self.assertEqual(validation_result.staged_write_plan, [])
        self.assertEqual(validation_result.mail_outcomes[0].write_disposition, "duplicate_only_noop")
        self.assertFalse(validation_result.mail_outcomes[0].eligible_for_write)
        self.assertFalse(validation_result.mail_outcomes[0].eligible_for_print)
        self.assertTrue(validation_result.mail_outcomes[0].eligible_for_mail_move)
        self.assertEqual(validation_result.mail_outcomes[0].ud_selection["final_decision"], "already_recorded")
        self.assertEqual(
            validation_result.mail_outcomes[0].ud_selection["final_decision_reason"],
            "ud_already_recorded",
        )

    def test_validate_run_snapshot_hard_blocks_mixed_ud_ip_exp_manifest_until_policy_resolved(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        mail = _mail("entry-ud-001", "UD-LC-0043-ANANTA")
        validation_result = validate_run_snapshot(
            descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
            run_report=_run_report(rule_pack, [mail]),
            rule_pack=rule_pack,
            erp_row_provider=_erp_provider(),
            workbook_snapshot=_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000 YDS", 3: "", 4: "", 5: ""}),
                ]
            ),
            ud_document_provider=MappingUDDocumentPayloadProvider(
                {
                    mail.entry_id: [
                        _ud_document("BGMEA/DHK/UD/2026/5483/003", quantity=Decimal("1000")),
                        EXPDocumentPayload(
                            document_number=DocumentExtractionField("EXP-001"),
                            document_date=DocumentExtractionField("2026-04-02"),
                            lc_sc_number=DocumentExtractionField("LC-0043"),
                        ),
                        IPDocumentPayload(
                            document_number=DocumentExtractionField("IP-002"),
                            document_date=DocumentExtractionField("2026-04-03"),
                            lc_sc_number=DocumentExtractionField("LC-0043"),
                        ),
                    ]
                }
            ),
        )

        self.assertEqual(validation_result.run_report.summary, {"pass": 0, "warning": 0, "hard_block": 1})
        self.assertEqual(validation_result.staged_write_plan, [])
        self.assertEqual(
            [report.code for report in validation_result.discrepancy_reports],
            ["ip_exp_policy_unresolved"],
        )
        self.assertEqual(
            validation_result.discrepancy_reports[0].details["proposed_shared_column_value"],
            "EXP: EXP-001\nIP: IP-002",
        )
        self.assertEqual(validation_result.mail_outcomes[0].ud_selection["selected_candidate_id"], "11")

    def test_validate_run_snapshot_stages_multiple_ud_documents_in_one_mail_without_reusing_rows(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        mail = _mail("entry-ud-001", "UD-LC-0043-ONE AND TWO")
        validation_result = validate_run_snapshot(
            descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
            run_report=_run_report(rule_pack, [mail]),
            rule_pack=rule_pack,
            erp_row_provider=_erp_provider(),
            workbook_snapshot=_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000 YDS", 3: "", 4: "", 5: ""}),
                    WorkbookRow(row_index=12, values={1: "LC-0043", 2: "1000 YDS", 3: "", 4: "", 5: ""}),
                ]
            ),
            ud_document_provider=MappingUDDocumentPayloadProvider(
                {
                    mail.entry_id: [
                        _ud_document("BGMEA/DHK/UD/2026/5483/004", quantity=Decimal("1000"), document_date="2026-04-02"),
                        _ud_document("BGMEA/DHK/UD/2026/5483/003", quantity=Decimal("1000"), document_date="2026-04-01"),
                    ]
                }
            ),
        )

        self.assertEqual(validation_result.run_report.summary, {"pass": 1, "warning": 0, "hard_block": 0})
        self.assertEqual(
            [
                (operation.operation_index_within_mail, operation.row_index, operation.expected_post_write_value)
                for operation in validation_result.staged_write_plan
            ],
            [
                (0, 11, "BGMEA/DHK/UD/2026/5483/003"),
                (1, 12, "BGMEA/DHK/UD/2026/5483/004"),
            ],
        )
        self.assertEqual(validation_result.mail_outcomes[0].ud_selection["document_count"], 2)
        self.assertEqual(validation_result.mail_outcomes[0].ud_selection["final_decision"], "selected")

    def test_validate_run_snapshot_ignores_same_mail_duplicate_ud_document_number(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        mail = _mail("entry-ud-001", "UD-LC-0043-DUPLICATE")
        duplicate_documents = [
            _ud_document("BGMEA/DHK/UD/2026/5483/003", quantity=Decimal("1000")),
            _ud_document("BGMEA/DHK/UD/2026/5483/003", quantity=Decimal("1000")),
        ]

        validation_result = validate_run_snapshot(
            descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
            run_report=_run_report(rule_pack, [mail]),
            rule_pack=rule_pack,
            erp_row_provider=_erp_provider(),
            workbook_snapshot=_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000 YDS", 3: "", 4: "", 5: ""}),
                ]
            ),
            ud_document_provider=MappingUDDocumentPayloadProvider({mail.entry_id: duplicate_documents}),
        )

        self.assertEqual(validation_result.run_report.summary, {"pass": 0, "warning": 1, "hard_block": 0})
        self.assertEqual(len(validation_result.staged_write_plan), 1)
        self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.WARNING)
        self.assertEqual(validation_result.mail_outcomes[0].write_disposition, "mixed_duplicate_and_new_writes")
        self.assertEqual(
            [report.code for report in validation_result.discrepancy_reports],
            ["ud_duplicate_document_same_mail"],
        )

    def test_validate_run_snapshot_hard_blocks_same_mail_duplicate_ud_number_when_evidence_differs(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        mail = _mail("entry-ud-001", "UD-LC-0043-CONFLICT")
        conflict_documents = [
            _ud_document("BGMEA/DHK/UD/2026/5483/003", quantity=Decimal("1000"), document_date="2026-04-01"),
            _ud_document("BGMEA/DHK/UD/2026/5483/003", quantity=Decimal("1000"), document_date="2026-04-02"),
        ]

        validation_result = validate_run_snapshot(
            descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
            run_report=_run_report(rule_pack, [mail]),
            rule_pack=rule_pack,
            erp_row_provider=_erp_provider(),
            workbook_snapshot=_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000 YDS", 3: "", 4: "", 5: ""}),
                ]
            ),
            ud_document_provider=MappingUDDocumentPayloadProvider({mail.entry_id: conflict_documents}),
        )

        self.assertEqual(validation_result.run_report.summary, {"pass": 0, "warning": 0, "hard_block": 1})
        self.assertEqual(validation_result.staged_write_plan, [])
        self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual(
            [report.code for report in validation_result.discrepancy_reports],
            ["ud_live_document_conflict"],
        )

    def test_validate_run_snapshot_for_ud_without_payload_hard_blocks(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        mail = _mail("entry-ud-001", "UD-LC-0043-ANANTA")
        validation_result = validate_run_snapshot(
            descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
            run_report=_run_report(rule_pack, [mail]),
            rule_pack=rule_pack,
            erp_row_provider=_erp_provider(),
            workbook_snapshot=_snapshot(rows=[]),
        )

        self.assertEqual(validation_result.run_report.summary, {"pass": 0, "warning": 0, "hard_block": 1})
        self.assertEqual(validation_result.staged_write_plan, [])
        self.assertEqual(
            [report.code for report in validation_result.discrepancy_reports],
            ["ud_allocation_unresolved", "ud_required_document_missing"],
        )
        self.assertIsNone(validation_result.mail_outcomes[0].ud_selection)

    def test_validate_run_snapshot_marks_later_legacy_ud_mail_as_same_run_duplicate_noop(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        first_mail = _mail("entry-ud-001", "UD-LC-0043-ONE")
        second_mail = _mail("entry-ud-002", "UD-LC-0043-TWO")
        validation_result = validate_run_snapshot(
            descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
            run_report=_run_report(rule_pack, [first_mail, second_mail]),
            rule_pack=rule_pack,
            erp_row_provider=_erp_provider(),
            workbook_snapshot=_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000 YDS", 3: "", 4: "", 5: ""}),
                ]
            ),
            ud_document_provider=MappingUDDocumentPayloadProvider(
                {
                    first_mail.entry_id: _ud_document("BGMEA/DHK/UD/2026/5483/003", quantity=Decimal("1000")),
                    second_mail.entry_id: _ud_document("BGMEA/DHK/UD/2026/5483/003", quantity=Decimal("1000")),
                }
            ),
        )

        self.assertEqual(validation_result.run_report.summary, {"pass": 1, "warning": 1, "hard_block": 0})
        self.assertEqual(len(validation_result.staged_write_plan), 1)
        self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.PASS)
        self.assertEqual(validation_result.mail_outcomes[1].final_decision, FinalDecision.WARNING)
        self.assertEqual(validation_result.mail_outcomes[1].write_disposition, "duplicate_only_noop")
        self.assertEqual(
            [report.code for report in validation_result.discrepancy_reports],
            ["ud_duplicate_document_same_run"],
        )

    def test_validate_run_snapshot_hard_blocks_same_mail_structured_ud_row_conflict(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        mail = _mail("entry-ud-001", "UD-LC-0043-MULTI")
        validation_result = validate_run_snapshot(
            descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
            run_report=_run_report(rule_pack, [mail]),
            rule_pack=rule_pack,
            erp_row_provider=_erp_provider(),
            workbook_snapshot=_structured_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000 YDS", 3: "", 4: "", 5: "", 6: "1000", 7: "", 8: ""}),
                    WorkbookRow(row_index=12, values={1: "LC-0043", 2: "500 YDS", 3: "", 4: "", 5: "", 6: "500", 7: "", 8: ""}),
                ]
            ),
            ud_document_provider=MappingUDDocumentPayloadProvider(
                {
                    mail.entry_id: [
                        _structured_ud_document(
                            "BGMEA/DHK/UD/2026/5483/003",
                            document_date="2026-04-01",
                            lc_sc_value="1000",
                            quantity="1000",
                        ),
                        _structured_ud_document(
                            "BGMEA/DHK/UD/2026/5483/004",
                            document_date="2026-04-02",
                            lc_sc_value="1500",
                            quantity="1500",
                        ),
                    ]
                }
            ),
        )

        self.assertEqual(validation_result.run_report.summary, {"pass": 0, "warning": 0, "hard_block": 1})
        self.assertEqual(validation_result.staged_write_plan, [])
        self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual(
            [report.code for report in validation_result.discrepancy_reports],
            ["ud_target_row_conflict"],
        )

    def test_validate_run_snapshot_hard_blocks_later_legacy_ud_mail_with_row_conflict(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        first_mail = _mail("entry-ud-001", "UD-LC-0043-ONE")
        second_mail = _mail("entry-ud-002", "UD-LC-0043-TWO")
        validation_result = validate_run_snapshot(
            descriptor=get_workflow_descriptor(WorkflowId.UD_IP_EXP),
            run_report=_run_report(rule_pack, [first_mail, second_mail]),
            rule_pack=rule_pack,
            erp_row_provider=_erp_provider(),
            workbook_snapshot=_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000 YDS", 3: "", 4: "", 5: ""}),
                ]
            ),
            ud_document_provider=MappingUDDocumentPayloadProvider(
                {
                    first_mail.entry_id: _ud_document("BGMEA/DHK/UD/2026/5483/003", quantity=Decimal("1000")),
                    second_mail.entry_id: _ud_document("BGMEA/DHK/UD/2026/5483/004", quantity=Decimal("1000")),
                }
            ),
        )

        self.assertEqual(validation_result.run_report.summary, {"pass": 1, "warning": 0, "hard_block": 1})
        self.assertEqual(len(validation_result.staged_write_plan), 1)
        self.assertEqual(validation_result.mail_outcomes[0].final_decision, FinalDecision.PASS)
        self.assertEqual(validation_result.mail_outcomes[1].final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual(
            validation_result.mail_outcomes[1].discrepancies[0]["code"],
            "ud_target_row_conflict",
        )


def _ud_document(
    document_number: str,
    *,
    quantity: Decimal | None,
    document_date: str = "2026-04-01",
) -> UDDocumentPayload:
    return UDDocumentPayload(
        document_number=DocumentExtractionField(document_number),
        document_date=DocumentExtractionField(document_date),
        lc_sc_number=DocumentExtractionField("LC-0043"),
        quantity=UDIPEXPQuantity(amount=quantity, unit="YDS") if quantity is not None else None,
    )


def _structured_ud_document(
    document_number: str,
    *,
    document_date: str,
    lc_sc_value: str,
    quantity: str,
) -> UDDocumentPayload:
    return UDDocumentPayload(
        document_number=DocumentExtractionField(document_number),
        document_date=DocumentExtractionField(document_date),
        lc_sc_number=DocumentExtractionField("LC-0043"),
        lc_sc_date=DocumentExtractionField("2026-01-10"),
        lc_sc_value=DocumentExtractionField(lc_sc_value),
        quantity_by_unit={"YDS": Decimal(quantity)},
    )


def _mail(entry_id: str, subject: str):
    return build_email_snapshot(
        [
            SourceEmailRecord(
                entry_id=entry_id,
                received_time="2026-04-01T03:00:00Z",
                subject_raw=subject,
                sender_address="sender@example.com",
                body_text="Please process commercial file P/26/0042.",
            )
        ],
        state_timezone="Asia/Dhaka",
    )[0]


def _run_report(rule_pack, mails):
    return RunReport(
        run_id="run-1",
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


def _snapshot(*, rows: list[WorkbookRow]) -> WorkbookSnapshot:
    return WorkbookSnapshot(
        sheet_name="Sheet1",
        headers=[
            WorkbookHeader(column_index=1, text="L/C & S/C No."),
            WorkbookHeader(column_index=2, text="Quantity of Fabrics (Yds/Mtr)"),
            WorkbookHeader(column_index=3, text="UD No. & IP No."),
            WorkbookHeader(column_index=4, text="L/C Amnd No."),
            WorkbookHeader(column_index=5, text="L/C Amnd Date"),
        ],
        rows=rows,
    )


def _structured_snapshot(*, rows: list[WorkbookRow]) -> WorkbookSnapshot:
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
        rows=rows,
    )


class _ERPProvider:
    def lookup_rows(self, *, file_numbers):
        row = ERPRegisterRow(
            file_number="P/26/0042",
            lc_sc_number="LC-0043",
            buyer_name="ANANTA GARMENTS LTD",
            lc_sc_date="2026-01-10",
            source_row_index=1,
            lc_qty="1000",
            lc_unit="YDS",
        )
        return {file_number: [row] if file_number == "P/26/0042" else [] for file_number in file_numbers}


def _erp_provider():
    return _ERPProvider()


if __name__ == "__main__":
    unittest.main()
