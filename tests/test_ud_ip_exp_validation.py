from __future__ import annotations

from decimal import Decimal
import unittest

from project.erp import ERPRegisterRow
from project.models import FinalDecision, WorkflowId
from project.rules import load_rule_pack
from project.workbook import WorkbookHeader, WorkbookRow, WorkbookSnapshot
from project.workflows.export_lc_sc.payloads import ExportFileNumberMatch, ExportMailPayload
from project.workflows.snapshot import SourceEmailRecord, build_email_snapshot
from project.workflows.ud_ip_exp import (
    DocumentExtractionField,
    UDDocumentPayload,
)
from project.workflows.ud_ip_exp.validation import assemble_ud_validation


class UDIPEXPValidationAssemblyTests(unittest.TestCase):
    def test_assemble_ud_validation_stages_selected_blank_rows(self) -> None:
        result = assemble_ud_validation(
            run_id="run-1",
            mail=_mail(),
            rule_pack=load_rule_pack(WorkflowId.UD_IP_EXP),
            ud_document=_ud_document(lc_sc_value="3000", quantity="3000"),
            workbook_snapshot=_structured_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000 YDS", 3: "", 4: "", 5: "", 6: "1000", 7: "", 8: ""}),
                    WorkbookRow(row_index=19, values={1: "LC-0043", 2: "2000 YDS", 3: "", 4: "", 5: "", 6: "2000", 7: "", 8: ""}),
                ]
            ),
            export_payload=_export_payload(lc_sc_number="LC-0043"),
            ud_receive_date="2026-04-22",
        )

        self.assertEqual(result.rule_evaluation.final_decision, FinalDecision.PASS)
        self.assertEqual(result.workflow_payload.ud_allocation_result.selected_candidate_id, "11-19")
        self.assertEqual(result.staging_result.discrepancies, [])
        self.assertEqual(
            [operation.row_index for operation in result.staging_result.staged_write_operations if operation.column_key == "ud_ip_shared"],
            [11, 19],
        )

    def test_assemble_ud_validation_hard_blocks_when_value_match_is_unresolved(self) -> None:
        result = assemble_ud_validation(
            run_id="run-1",
            mail=_mail(),
            rule_pack=load_rule_pack(WorkflowId.UD_IP_EXP),
            ud_document=_ud_document(lc_sc_value="1000", quantity="1000"),
            workbook_snapshot=_structured_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-9999", 2: "1000 YDS", 3: "", 4: "", 5: "", 6: "1000", 7: "", 8: ""}),
                ]
            ),
            export_payload=_export_payload(lc_sc_number="LC-0043"),
        )

        self.assertEqual(result.rule_evaluation.final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual([item.code for item in result.rule_evaluation.discrepancies], ["ud_lc_value_match_unresolved"])
        self.assertEqual(result.workflow_payload.ud_allocation_result.final_decision_reason, "ud_lc_value_match_unresolved")
        self.assertEqual(result.staging_result.staged_write_operations, [])

    def test_assemble_ud_validation_reports_invalid_workbook_header_mapping(self) -> None:
        result = assemble_ud_validation(
            run_id="run-1",
            mail=_mail(),
            rule_pack=load_rule_pack(WorkflowId.UD_IP_EXP),
            ud_document=_ud_document(lc_sc_value="1000", quantity="1000"),
            workbook_snapshot=WorkbookSnapshot(
                sheet_name="Sheet1",
                headers=[
                    WorkbookHeader(column_index=1, text="L/C & S/C No."),
                    WorkbookHeader(column_index=2, text="Quantity of Fabrics (Yds/Mtr)"),
                ],
                rows=[],
            ),
            export_payload=_export_payload(lc_sc_number="LC-0043"),
        )

        self.assertEqual(result.rule_evaluation.final_decision, FinalDecision.HARD_BLOCK)
        self.assertIn("ud_allocation_unresolved", [item.code for item in result.rule_evaluation.discrepancies])
        self.assertEqual(result.staging_result.discrepancies[0].code, "workbook_header_mapping_invalid")

    def test_assemble_ud_validation_hard_blocks_nonblank_selected_target(self) -> None:
        result = assemble_ud_validation(
            run_id="run-1",
            mail=_mail(),
            rule_pack=load_rule_pack(WorkflowId.UD_IP_EXP),
            ud_document=_ud_document(lc_sc_value="1000", quantity="1000"),
            workbook_snapshot=_structured_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000 YDS", 3: "UD-OLD", 4: "", 5: "", 6: "1000", 7: "", 8: ""}),
                ]
            ),
            export_payload=_export_payload(lc_sc_number="LC-0043"),
        )

        self.assertEqual(result.rule_evaluation.final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual([item.code for item in result.rule_evaluation.discrepancies], ["ud_target_row_conflict"])
        self.assertEqual(result.staging_result.discrepancies, [])
        self.assertEqual(result.staging_result.staged_write_operations, [])

    def test_assemble_ud_validation_hard_blocks_missing_value_first_evidence(self) -> None:
        result = assemble_ud_validation(
            run_id="run-1",
            mail=_mail(),
            rule_pack=load_rule_pack(WorkflowId.UD_IP_EXP),
            ud_document=UDDocumentPayload(
                document_number=DocumentExtractionField("BGMEA/DHK/UD/2026/5483/003"),
                document_date=DocumentExtractionField("2026-04-01"),
                lc_sc_number=DocumentExtractionField("LC-0043"),
            ),
            workbook_snapshot=_structured_snapshot(rows=[]),
            export_payload=_export_payload(lc_sc_number="LC-0043"),
        )

        self.assertEqual(result.rule_evaluation.final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual(
            [item.code for item in result.rule_evaluation.discrepancies],
            ["ud_allocation_unresolved", "ud_required_field_missing"],
        )
        self.assertEqual(result.staging_result.staged_write_operations, [])

    def test_assemble_structured_ud_validation_uses_erp_lc_date_value_and_writes_dates(self) -> None:
        result = assemble_ud_validation(
            run_id="run-1",
            mail=_mail(),
            rule_pack=load_rule_pack(WorkflowId.UD_IP_EXP),
            ud_document=_ud_document(lc_sc_value="17375.80", quantity="3050", lc_sc_number="1345260400434", document_date="2026-03-31", lc_sc_date="2026-03-16"),
            workbook_snapshot=_structured_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "1345260400434", 2: "1000 YDS", 3: "", 4: "", 5: "", 6: "10000", 7: "", 8: ""}),
                    WorkbookRow(row_index=12, values={1: "1345260400434", 2: "2000 YDS", 3: "", 4: "", 5: "", 6: "7375.80", 7: "", 8: ""}),
                ]
            ),
            export_payload=_export_payload(lc_sc_number="1345260400434", lc_sc_date="2026-03-16", file_number="P/26/0434"),
            ud_receive_date="2026-04-22",
        )

        self.assertEqual(result.rule_evaluation.final_decision, FinalDecision.PASS)
        self.assertEqual(result.workflow_payload.ud_allocation_result.selected_candidate_id, "11-12")
        self.assertEqual(
            [(operation.row_index, operation.column_key, operation.expected_post_write_value) for operation in result.staging_result.staged_write_operations],
            [
                (11, "ud_ip_shared", "BGMEA/DHK/UD/2026/5483/003"),
                (11, "ud_ip_date", "31/03/2026"),
                (11, "ud_recv_date", "22/04/2026"),
                (12, "ud_ip_shared", "BGMEA/DHK/UD/2026/5483/003"),
                (12, "ud_ip_date", "31/03/2026"),
                (12, "ud_recv_date", "22/04/2026"),
            ],
        )

    def test_assemble_structured_ud_validation_hard_blocks_lc_date_mismatch(self) -> None:
        result = assemble_ud_validation(
            run_id="run-1",
            mail=_mail(),
            rule_pack=load_rule_pack(WorkflowId.UD_IP_EXP),
            ud_document=_ud_document(lc_sc_value="17375.80", quantity="3050", lc_sc_number="1345260400434", document_date="2026-03-31", lc_sc_date="2026-03-17"),
            workbook_snapshot=_structured_snapshot(rows=[]),
            export_payload=_export_payload(lc_sc_number="1345260400434", lc_sc_date="2026-03-16", file_number="P/26/0434"),
            ud_receive_date="2026-04-22",
        )

        self.assertEqual(result.rule_evaluation.final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual(result.workflow_payload.ud_allocation_result.discrepancy_code, "ud_lc_date_mismatch")


def _ud_document(
    *,
    lc_sc_value: str,
    quantity: str,
    lc_sc_number: str = "LC-0043",
    document_date: str = "2026-04-01",
    lc_sc_date: str = "2026-01-10",
) -> UDDocumentPayload:
    return UDDocumentPayload(
        document_number=DocumentExtractionField("BGMEA/DHK/UD/2026/5483/003"),
        document_date=DocumentExtractionField(document_date),
        lc_sc_number=DocumentExtractionField(lc_sc_number),
        lc_sc_date=DocumentExtractionField(lc_sc_date),
        lc_sc_value=DocumentExtractionField(lc_sc_value),
        quantity_by_unit={"YDS": Decimal(quantity)},
    )


def _export_payload(*, lc_sc_number: str, lc_sc_date: str = "2026-01-10", file_number: str = "P/26/0042") -> ExportMailPayload:
    row = ERPRegisterRow(
        file_number=file_number,
        lc_sc_number=lc_sc_number,
        buyer_name="NALIN TEX LTD",
        lc_sc_date=lc_sc_date,
        source_row_index=7,
        ship_remarks="",
    )
    return ExportMailPayload(
        parsed_subject=None,
        file_numbers=[file_number],
        erp_matches=[ExportFileNumberMatch(file_number=file_number, canonical_row=row, matched_rows=[row])],
        verified_family=row.family,
        attachments_in_order=[],
    )


def _mail():
    return build_email_snapshot(
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


if __name__ == "__main__":
    unittest.main()
