from __future__ import annotations

from decimal import Decimal
import unittest

from project.models import FinalDecision, WorkflowId
from project.rules import load_rule_pack
from project.workbook import WorkbookHeader, WorkbookRow, WorkbookSnapshot
from project.workflows.snapshot import SourceEmailRecord, build_email_snapshot
from project.workflows.ud_ip_exp import (
    DocumentExtractionField,
    UDDocumentPayload,
    UDIPEXPQuantity,
)
from project.workflows.ud_ip_exp.validation import assemble_ud_validation


class UDIPEXPValidationAssemblyTests(unittest.TestCase):
    def test_assemble_ud_validation_stages_selected_blank_rows(self) -> None:
        result = assemble_ud_validation(
            run_id="run-1",
            mail=_mail(),
            rule_pack=load_rule_pack(WorkflowId.UD_IP_EXP),
            ud_document=_ud_document(quantity=Decimal("3000")),
            workbook_snapshot=_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000 YDS", 3: "", 4: "", 5: ""}),
                    WorkbookRow(row_index=19, values={1: "LC-0043", 2: "2000 YDS", 3: "", 4: "", 5: ""}),
                ]
            ),
        )

        self.assertEqual(result.rule_evaluation.final_decision, FinalDecision.PASS)
        self.assertEqual(result.workflow_payload.ud_allocation_result.selected_candidate_id, "11-19")
        self.assertEqual(result.staging_result.discrepancies, [])
        self.assertEqual([operation.row_index for operation in result.staging_result.staged_write_operations], [11, 19])

    def test_assemble_ud_validation_hard_blocks_when_no_matching_rows(self) -> None:
        result = assemble_ud_validation(
            run_id="run-1",
            mail=_mail(),
            rule_pack=load_rule_pack(WorkflowId.UD_IP_EXP),
            ud_document=_ud_document(quantity=Decimal("1000")),
            workbook_snapshot=_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-9999", 2: "1000 YDS", 3: "", 4: "", 5: ""}),
                ]
            ),
        )

        self.assertEqual(result.rule_evaluation.final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual([item.code for item in result.rule_evaluation.discrepancies], ["ud_allocation_unresolved"])
        self.assertEqual(result.workflow_payload.ud_allocation_result.final_decision_reason, "no_valid_ud_quantity_combination")
        self.assertEqual(result.staging_result.staged_write_operations, [])

    def test_assemble_ud_validation_reports_invalid_workbook_header_mapping(self) -> None:
        result = assemble_ud_validation(
            run_id="run-1",
            mail=_mail(),
            rule_pack=load_rule_pack(WorkflowId.UD_IP_EXP),
            ud_document=_ud_document(quantity=Decimal("1000")),
            workbook_snapshot=WorkbookSnapshot(
                sheet_name="Sheet1",
                headers=[
                    WorkbookHeader(column_index=1, text="L/C & S/C No."),
                    WorkbookHeader(column_index=2, text="Quantity of Fabrics (Yds/Mtr)"),
                ],
                rows=[],
            ),
        )

        self.assertEqual(result.rule_evaluation.final_decision, FinalDecision.HARD_BLOCK)
        self.assertIn("ud_allocation_unresolved", [item.code for item in result.rule_evaluation.discrepancies])
        self.assertEqual(result.staging_result.discrepancies[0].code, "workbook_header_mapping_invalid")

    def test_assemble_ud_validation_hard_blocks_nonblank_selected_target(self) -> None:
        result = assemble_ud_validation(
            run_id="run-1",
            mail=_mail(),
            rule_pack=load_rule_pack(WorkflowId.UD_IP_EXP),
            ud_document=_ud_document(quantity=Decimal("1000")),
            workbook_snapshot=_snapshot(
                rows=[
                    WorkbookRow(row_index=11, values={1: "LC-0043", 2: "1000 YDS", 3: "UD-OLD", 4: "", 5: ""}),
                ]
            ),
        )

        self.assertEqual(result.rule_evaluation.final_decision, FinalDecision.PASS)
        self.assertEqual(result.staging_result.discrepancies[0].code, "ud_shared_column_nonblank_policy_unresolved")
        self.assertEqual(result.staging_result.staged_write_operations, [])

    def test_assemble_ud_validation_hard_blocks_missing_quantity(self) -> None:
        result = assemble_ud_validation(
            run_id="run-1",
            mail=_mail(),
            rule_pack=load_rule_pack(WorkflowId.UD_IP_EXP),
            ud_document=_ud_document(quantity=None),
            workbook_snapshot=_snapshot(rows=[]),
        )

        self.assertEqual(result.rule_evaluation.final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual(
            [item.code for item in result.rule_evaluation.discrepancies],
            ["ud_allocation_unresolved", "ud_required_field_missing"],
        )
        self.assertEqual(result.staging_result.staged_write_operations, [])


def _ud_document(*, quantity: Decimal | None) -> UDDocumentPayload:
    return UDDocumentPayload(
        document_number=DocumentExtractionField("UD-LC-0043-ANANTA"),
        document_date=DocumentExtractionField("2026-04-01"),
        lc_sc_number=DocumentExtractionField("LC-0043"),
        quantity=UDIPEXPQuantity(amount=quantity, unit="YDS") if quantity is not None else None,
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


if __name__ == "__main__":
    unittest.main()
