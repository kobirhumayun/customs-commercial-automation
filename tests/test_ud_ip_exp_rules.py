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
    UDAllocationCandidate,
    UDAllocationResult,
    UDDocumentPayload,
    UDIPEXPWorkflowPayload,
)
from project.workflows.validation import WorkflowValidationContext


class UDIPEXPRuleTests(unittest.TestCase):
    def test_load_rule_pack_includes_ud_ip_exp_rules_after_core_rules(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)

        self.assertEqual(rule_pack.rule_pack_id, "ud_ip_exp.default")
        self.assertEqual(rule_pack.rule_pack_version, "1.2.0")
        self.assertEqual(
            [rule.rule_id for rule in rule_pack.rule_definitions],
            [
                "core.mail.sender_present.v1",
                "core.mail.subject_present.v1",
                "ud_ip_exp.erp_rows_present.v1",
                "ud_ip_exp.family_consistent.v1",
                "ud_ip_exp.file_number_present.v1",
                "ud_ip_exp.ip_exp_mail_shape_valid.v1",
                "ud_ip_exp.ip_exp_required_fields_present.v1",
                "ud_ip_exp.ud_allocation_selected.v1",
                "ud_ip_exp.ud_document_present.v1",
                "ud_ip_exp.ud_required_fields_present.v2",
            ],
        )

    def test_rule_pack_passes_for_ud_payload_with_selected_allocation(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        payload = UDIPEXPWorkflowPayload(
            documents=[_ud_document()],
            ud_allocation_result=_selected_allocation_result(),
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

    def test_rule_pack_hard_blocks_missing_value_first_ud_fields(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        payload = UDIPEXPWorkflowPayload(
            documents=[
                UDDocumentPayload(
                    document_number=DocumentExtractionField(" "),
                    document_date=None,
                    lc_sc_number=DocumentExtractionField("LC-0043"),
                    lc_sc_date=None,
                    lc_sc_value=None,
                    quantity_by_unit={},
                )
            ],
            ud_allocation_result=_selected_allocation_result(),
        )

        result = evaluate_rule_pack(_context(payload), rule_pack)

        self.assertEqual(result.final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual([item.code for item in result.discrepancies], ["ud_required_field_missing"])
        self.assertEqual(
            result.discrepancies[0].details["missing_by_document"][0]["missing_fields"],
            ["document_number", "document_date", "lc_sc_date", "lc_sc_value", "quantity_by_unit"],
        )

    def test_rule_pack_hard_blocks_invalid_ud_number_and_dates(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        payload = UDIPEXPWorkflowPayload(
            documents=[
                UDDocumentPayload(
                    document_number=DocumentExtractionField("UD-LC-0127-COTTONEX FASHIONS LTD"),
                    document_date=DocumentExtractionField("2026-99-99"),
                    lc_sc_number=DocumentExtractionField("LC-0127"),
                    lc_sc_date=DocumentExtractionField("2026-99-99"),
                    lc_sc_value=DocumentExtractionField("1000"),
                    quantity_by_unit={"YDS": Decimal("1000")},
                )
            ],
            ud_allocation_result=_selected_allocation_result(),
        )

        result = evaluate_rule_pack(_context(payload), rule_pack)

        self.assertEqual(result.final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual([item.code for item in result.discrepancies], ["ud_required_field_invalid"])
        self.assertEqual(
            result.discrepancies[0].details["invalid_by_document"],
            [
                {
                    "document_index": 0,
                    "document_number": "UD-LC-0127-COTTONEX FASHIONS LTD",
                    "invalid_fields": ["document_number", "document_date", "lc_sc_date"],
                }
            ],
        )

    def test_rule_pack_preserves_ud_allocation_tie_code(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        payload = UDIPEXPWorkflowPayload(
            documents=[_ud_document()],
            ud_allocation_result=_hard_block_allocation_result(
                reason="ud_candidate_tie_after_full_tiebreak",
                code="ud_candidate_tie_after_full_tiebreak",
            ),
        )

        result = evaluate_rule_pack(_context(payload), rule_pack)

        self.assertEqual(result.final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual([item.code for item in result.discrepancies], ["ud_candidate_tie_after_full_tiebreak"])

    def test_rule_pack_passes_exp_ip_payloads_when_shape_and_fields_are_valid(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        payload = UDIPEXPWorkflowPayload(
            documents=[
                IPDocumentPayload(
                    document_number=DocumentExtractionField("IP-002"),
                    document_date=DocumentExtractionField("2026-04-02"),
                    lc_sc_number=DocumentExtractionField("LC-0043"),
                ),
                EXPDocumentPayload(
                    document_number=DocumentExtractionField("EXP-001"),
                    document_date=DocumentExtractionField("2026-04-02"),
                    lc_sc_number=DocumentExtractionField("LC-0043"),
                ),
            ],
        )

        result = evaluate_rule_pack(_context(payload), rule_pack)

        self.assertEqual(result.final_decision, FinalDecision.PASS)
        self.assertEqual(result.discrepancies, [])

    def test_rule_pack_hard_blocks_mixed_ud_and_ip_exp_mail_shape(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        payload = UDIPEXPWorkflowPayload(
            documents=[
                _ud_document(),
                EXPDocumentPayload(
                    document_number=DocumentExtractionField("EXP-001"),
                    document_date=DocumentExtractionField("2026-04-02"),
                    lc_sc_number=DocumentExtractionField("LC-0043"),
                ),
            ],
            ud_allocation_result=_selected_allocation_result(),
        )

        result = evaluate_rule_pack(_context(payload), rule_pack)

        self.assertEqual(result.final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual([item.code for item in result.discrepancies], ["ud_ip_exp_mail_shape_invalid"])

    def test_rule_pack_hard_blocks_missing_body_file_number_when_erp_family_payload_is_supplied(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.UD_IP_EXP)
        payload = UDIPEXPWorkflowPayload(
            documents=[_ud_document()],
            ud_allocation_result=_selected_allocation_result(),
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
            ud_allocation_result=_selected_allocation_result(),
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
            ud_allocation_result=_selected_allocation_result(),
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
        document_number=DocumentExtractionField("BGMEA/DHK/UD/2026/5483/003"),
        document_date=DocumentExtractionField("2026-04-01"),
        lc_sc_number=DocumentExtractionField("LC-0043"),
        lc_sc_date=DocumentExtractionField("2026-01-10"),
        lc_sc_value=DocumentExtractionField("1000"),
        quantity_by_unit={"YDS": Decimal("1000")},
    )


def _selected_allocation_result() -> UDAllocationResult:
    candidate = UDAllocationCandidate(
        candidate_id="11",
        row_indexes=[11],
        matched_quantities=["YDS:1000"],
        quantity_sum="YDS:1000",
        ignored_excess_quantity="YDS:0",
        score_keys={
            "row_index_key": [11],
            "amendment_recency_key": [],
            "blank_field_priority_key": {
                "blank_target_count_desc": -1,
                "nonblank_optional_count_asc": 0,
            },
            "stable_candidate_id_key": "11",
        },
        prewrite_blank_targets_count=1,
        prewrite_nonblank_optional_count=0,
        selected=True,
    )
    return UDAllocationResult(
        required_quantity="YDS:1000",
        quantity_unit="MULTI",
        candidate_count=1,
        candidates=[candidate],
        final_decision="selected",
        final_decision_reason="selected_structured_lc_value_and_quantity",
        selected_candidate_id="11",
    )


def _hard_block_allocation_result(*, reason: str, code: str | None = None) -> UDAllocationResult:
    return UDAllocationResult(
        required_quantity="YDS:1000",
        quantity_unit="MULTI",
        candidate_count=0,
        candidates=[],
        final_decision="hard_block",
        final_decision_reason=reason,
        discrepancy_code=code,
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
        rule_pack_version="1.2.0",
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
