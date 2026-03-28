from __future__ import annotations

import unittest

from project.models import FinalDecision, OperatorContext, WorkflowId
from project.models.enums import RuleStage
from project.rules import (
    LoadedRulePack,
    RuleDefinition,
    RuleDiscrepancy,
    RuleEvaluationResult,
    evaluate_rule_pack,
    load_rule_pack,
)
from project.workflows.snapshot import build_email_snapshot, SourceEmailRecord
from project.workflows.validation import WorkflowValidationContext


class RuleLoaderTests(unittest.TestCase):
    def test_loader_returns_empty_but_valid_export_rule_pack(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)

        self.assertEqual(rule_pack.rule_pack_id, "export_lc_sc.default")
        self.assertEqual(rule_pack.rule_pack_version, "1.0.0")
        self.assertEqual(
            [rule.rule_id for rule in rule_pack.rule_definitions],
            ["core.mail.sender_present.v1", "core.mail.subject_present.v1"],
        )

    def test_rule_engine_aggregates_precedence_and_deduplicates_discrepancies(self) -> None:
        def warning_rule(context: WorkflowValidationContext) -> RuleEvaluationResult:
            del context
            return RuleEvaluationResult(
                rule_id="core.mail.warning.v1",
                outcome=FinalDecision.WARNING,
                rationale="Warning emitted.",
                discrepancies=(
                    RuleDiscrepancy(
                        code="ocr_non_required_field_low_confidence",
                        severity=FinalDecision.WARNING,
                        message="Low confidence optional field.",
                        subject_scope="mail",
                        target_ref=None,
                    ),
                ),
            )

        def hard_block_rule(context: WorkflowValidationContext) -> RuleEvaluationResult:
            del context
            return RuleEvaluationResult(
                rule_id="export_lc_sc.mail.block.v1",
                outcome=FinalDecision.HARD_BLOCK,
                rationale="Blocking discrepancy emitted.",
                discrepancies=(
                    RuleDiscrepancy(
                        code="ocr_non_required_field_low_confidence",
                        severity=FinalDecision.WARNING,
                        message="Low confidence optional field.",
                        subject_scope="mail",
                        target_ref=None,
                    ),
                ),
            )

        rule_pack = LoadedRulePack(
            rule_pack_id="export_lc_sc.default",
            rule_pack_version="1.0.0",
            rule_definitions=(
                RuleDefinition(
                    rule_id="core.mail.warning.v1",
                    stage=RuleStage.CORE,
                    evaluator=warning_rule,
                ),
                RuleDefinition(
                    rule_id="export_lc_sc.mail.block.v1",
                    stage=RuleStage.WORKFLOW_STANDARD,
                    evaluator=hard_block_rule,
                ),
            ),
        )
        mail = build_email_snapshot(
            [
                SourceEmailRecord(
                    entry_id="entry-001",
                    received_time="2026-03-28T03:00:00Z",
                    subject_raw="Mail",
                    sender_address="sender@example.com",
                )
            ],
            state_timezone="Asia/Dhaka",
        )[0]
        context = WorkflowValidationContext(
            run_id="run-1",
            workflow_id=WorkflowId.EXPORT_LC_SC,
            rule_pack_id=rule_pack.rule_pack_id,
            rule_pack_version=rule_pack.rule_pack_version,
            state_timezone="Asia/Dhaka",
            operator_context=OperatorContext(
                operator_id="user",
                username="user",
                host_name="host",
                process_id=1,
            ),
            mail=mail,
        )

        aggregated = evaluate_rule_pack(context, rule_pack)

        self.assertEqual(aggregated.final_decision, FinalDecision.HARD_BLOCK)
        self.assertEqual(
            aggregated.applied_rule_ids,
            ["core.mail.warning.v1", "export_lc_sc.mail.block.v1"],
        )
        self.assertEqual(len(aggregated.discrepancies), 1)
        self.assertEqual(
            aggregated.discrepancies[0].source_rule_ids,
            ["core.mail.warning.v1", "export_lc_sc.mail.block.v1"],
        )


if __name__ == "__main__":
    unittest.main()
