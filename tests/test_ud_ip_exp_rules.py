from __future__ import annotations

from decimal import Decimal
import unittest

from project.erp import ERPRegisterRow
from project.models import FinalDecision, WorkflowId
from project.rules import evaluate_rule_pack, load_rule_pack
from project.workflows.export_lc_sc.payloads import ExportFileNumberMatch, ExportMailPayload
from project.workflows.snapshot import SourceEmailRecord, build_email_snapshot
from project.workflows.ud_ip_exp import (
    DocumentExtractionField,
    EXPDocumentPayload,
    IPDocumentPayload,
    UDCandidateRow,
    UDDocumentPayload,
    UDIPEXPWorkflowPayload,
    UDIPEXPQuantity,
    allocate_ud_rows,
)
from project.workflows.validation import WorkflowValidationContext


class UDIPEXPRuleTests(unittest.TestCase):
    def test_load_rule_pack_includes_ud_ip_exp_rules_after_core_rules(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)

        self.assertEqual(rule_pack.rule_pack_id, "ud_ip_exp.default")
        self.assertEqual(rule_pack.rule_pack_version, "1.0.0")
        self.assertEqual(
            [rule.rule_id for rule in rule_pack.rule_definitions],
            [
                "core.mail.sender_present.v1",
                "core.mail.subject_present.v1",
                "ud_ip_exp.erp_rows_present.v1",
                "ud_ip_exp.family_consistent.v1",
                "ud_ip_exp.file_number_present.v1",
                "ud_ip_exp.ip_exp_policy_resolved.v1",
                "ud_ip_exp.ud_allocation_selected.v1",
                "ud_ip_exp.ud_document_present.v1",
                "ud_ip_exp.ud_required_fields_present.v1",
            ],
        )

    def test_rule_pack_passes_for_ud_payload_with_selected_allocation(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        payload = UDIPEXPWorkflowPayload(
            documents=[_ud_document()],
            ud_allocation_result=allocate_ud_rows(
                required_quantity=Decimal("1000"),
                quantity_unit="YDS",
                candidate_rows=[
                    UDCandidateRow(
                        row_index=11,
                        lc_sc_number="LC-0043",
                        quantity=Decimal("1000"),
                        quantity_unit="YDS",
                    )
                ],
            ),
        )

        result = evaluate_rule_pack(_context(payload), rule_pack)

        self.assertEqual(result.final_decision, FinalDecision.PASS)
        self.assertEqual(result.discrepancies, [])

    def test_rule_pack_hard_blocks_missing_ud_document(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        payload = UDIPEXPWorkflowPayload(documents=[])

        result = evaluate_rule_pack(_context(payload), rule_pack)

        self.assertEqual(result.final_decision, FinalDecision.HARD_BLOCK)
        self.assertIn("ud_required_document_missing", [item.code for item in result.discrepancies])
        self.assertIn("ud_allocation_unresolved", [item.code for item in result.discrepancies])

    def test_rule_pack_hard_blocks_missing_confirmed_ud_fields(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        payload = UDIPEXPWorkflowPayload(
            documents=[
                UDDocumentPayload(
                    document_number=DocumentExtractionField(" "),
                    document_date=None,
                    lc_sc_number=DocumentExtractionField("LC-0043"),
                    quantity=None,
                )
            ],
            ud_allocation_result=allocate_ud_rows(
                required_quantity=Decimal("1000"),
                quantity_unit="YDS",
                candidate_rows=[
                    UDCandidateRow(
                        row_index=11,
                        lc_sc_number="LC-0043",
                        quantity=Decimal("1000"),
                        quantity_unit="YDS",
                    )
                ],
            ),
        )

        result = evaluate_rule_pack(_context(payload), rule_pack)

        self.assertEqual(result.final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual([item.code for item in result.discrepancies], ["ud_required_field_missing"])
        self.assertEqual(
            result.discrepancies[0].details["missing_by_document"][0]["missing_fields"],
            ["document_number", "document_date", "quantity"],
        )

    def test_rule_pack_preserves_ud_allocation_tie_code(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        payload = UDIPEXPWorkflowPayload(
            documents=[_ud_document()],
            ud_allocation_result=allocate_ud_rows(
                required_quantity=Decimal("1000"),
                quantity_unit="YDS",
                candidate_rows=[
                    UDCandidateRow(
                        row_index=11,
                        lc_sc_number="LC-0043",
                        quantity=Decimal("1000"),
                        quantity_unit="YDS",
                    ),
                    UDCandidateRow(
                        row_index=11,
                        lc_sc_number="LC-0043",
                        quantity=Decimal("1000"),
                        quantity_unit="YDS",
                    ),
                ],
            ),
        )

        result = evaluate_rule_pack(_context(payload), rule_pack)

        self.assertEqual(result.final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual([item.code for item in result.discrepancies], ["ud_candidate_tie_after_full_tiebreak"])

    def test_rule_pack_hard_blocks_ip_exp_payloads_until_policy_resolved(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        payload = UDIPEXPWorkflowPayload(
            documents=[
                _ud_document(),
                IPDocumentPayload(
                    document_number=DocumentExtractionField("IP-002"),
                    document_date=DocumentExtractionField("2026-04-03"),
                    lc_sc_number=DocumentExtractionField("LC-0043"),
                ),
                EXPDocumentPayload(
                    document_number=DocumentExtractionField("EXP-001"),
                    document_date=DocumentExtractionField("2026-04-02"),
                    lc_sc_number=DocumentExtractionField("LC-0043"),
                ),
            ],
            ud_allocation_result=allocate_ud_rows(
                required_quantity=Decimal("1000"),
                quantity_unit="YDS",
                candidate_rows=[
                    UDCandidateRow(
                        row_index=11,
                        lc_sc_number="LC-0043",
                        quantity=Decimal("1000"),
                        quantity_unit="YDS",
                    )
                ],
            ),
        )

        result = evaluate_rule_pack(_context(payload), rule_pack)

        self.assertEqual(result.final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual([item.code for item in result.discrepancies], ["ip_exp_policy_unresolved"])
        self.assertEqual(
            result.discrepancies[0].details["proposed_shared_column_value"],
            "EXP: EXP-001\nIP: IP-002",
        )

    def test_rule_pack_hard_blocks_missing_body_file_number_when_erp_family_payload_is_supplied(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        payload = UDIPEXPWorkflowPayload(
            documents=[_ud_document()],
            ud_allocation_result=allocate_ud_rows(
                required_quantity=Decimal("1000"),
                quantity_unit="YDS",
                candidate_rows=[
                    UDCandidateRow(
                        row_index=11,
                        lc_sc_number="LC-0043",
                        quantity=Decimal("1000"),
                        quantity_unit="YDS",
                    )
                ],
            ),
            export_payload=ExportMailPayload(
                parsed_subject=None,
                file_numbers=[],
                erp_matches=[],
                verified_family=None,
                attachments_in_order=[],
            ),
        )

        result = evaluate_rule_pack(_context(payload), rule_pack)

        self.assertEqual(result.final_decision, FinalDecision.HARD_BLOCK)
        self.assertIn("ud_file_number_missing", [item.code for item in result.discrepancies])

    def test_rule_pack_hard_blocks_missing_erp_row_for_body_file_number(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        payload = UDIPEXPWorkflowPayload(
            documents=[_ud_document()],
            ud_allocation_result=allocate_ud_rows(
                required_quantity=Decimal("1000"),
                quantity_unit="YDS",
                candidate_rows=[
                    UDCandidateRow(
                        row_index=11,
                        lc_sc_number="LC-0043",
                        quantity=Decimal("1000"),
                        quantity_unit="YDS",
                    )
                ],
            ),
            export_payload=ExportMailPayload(
                parsed_subject=None,
                file_numbers=["P/26/0042"],
                erp_matches=[
                    ExportFileNumberMatch(
                        file_number="P/26/0042",
                        canonical_row=None,
                        matched_rows=[],
                    )
                ],
                verified_family=None,
                attachments_in_order=[],
            ),
        )

        result = evaluate_rule_pack(_context(payload), rule_pack)

        self.assertEqual(result.final_decision, FinalDecision.HARD_BLOCK)
        self.assertIn("ud_erp_row_missing", [item.code for item in result.discrepancies])

    def test_rule_pack_hard_blocks_inconsistent_erp_family_for_body_file_numbers(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        first_row = _erp_row(file_number="P/26/0042", lc_sc_number="LC-0043")
        second_row = _erp_row(file_number="P/26/0043", lc_sc_number="LC-0099")
        payload = UDIPEXPWorkflowPayload(
            documents=[_ud_document()],
            ud_allocation_result=allocate_ud_rows(
                required_quantity=Decimal("1000"),
                quantity_unit="YDS",
                candidate_rows=[
                    UDCandidateRow(
                        row_index=11,
                        lc_sc_number="LC-0043",
                        quantity=Decimal("1000"),
                        quantity_unit="YDS",
                    )
                ],
            ),
            export_payload=ExportMailPayload(
                parsed_subject=None,
                file_numbers=["P/26/0042", "P/26/0043"],
                erp_matches=[
                    ExportFileNumberMatch(
                        file_number="P/26/0042",
                        canonical_row=first_row,
                        matched_rows=[first_row],
                    ),
                    ExportFileNumberMatch(
                        file_number="P/26/0043",
                        canonical_row=second_row,
                        matched_rows=[second_row],
                    ),
                ],
                verified_family=None,
                attachments_in_order=[],
            ),
        )

        result = evaluate_rule_pack(_context(payload), rule_pack)

        self.assertEqual(result.final_decision, FinalDecision.HARD_BLOCK)
        self.assertIn("ud_family_inconsistent", [item.code for item in result.discrepancies])


def _ud_document() -> UDDocumentPayload:
    return UDDocumentPayload(
        document_number=DocumentExtractionField("UD-LC-0043-ANANTA"),
        document_date=DocumentExtractionField("2026-04-01"),
        lc_sc_number=DocumentExtractionField("LC-0043"),
        quantity=UDIPEXPQuantity(amount=Decimal("1000"), unit="YDS"),
    )


def _context(payload: UDIPEXPWorkflowPayload) -> WorkflowValidationContext:
    mail = build_email_snapshot(
        [
            SourceEmailRecord(
                entry_id="entry-ud-001",
                received_time="2026-04-01T03:00:00Z",
                subject_raw="UD-LC-0043-ANANTA",
                sender_address="sender@example.com",
            )
        ],
        state_timezone="Asia/Dhaka",
    )[0]
    return WorkflowValidationContext(
        run_id="run-1",
        workflow_id=WorkflowId.UD_IP_EXP,
        rule_pack_id="ud_ip_exp.default",
        rule_pack_version="1.0.0",
        state_timezone="Asia/Dhaka",
        operator_context=None,
        mail=mail,
        workflow_payload=payload,
    )


def _erp_row(*, file_number: str, lc_sc_number: str) -> ERPRegisterRow:
    return ERPRegisterRow(
        file_number=file_number,
        lc_sc_number=lc_sc_number,
        buyer_name="ANANTA GARMENTS LTD",
        lc_sc_date="2026-01-10",
        source_row_index=1,
        lc_qty="1000",
        lc_unit="YDS",
    )


if __name__ == "__main__":
    unittest.main()
