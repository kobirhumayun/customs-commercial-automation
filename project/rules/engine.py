from __future__ import annotations

from collections import OrderedDict
from typing import Any

from project.models.enums import FinalDecision
from project.rules.types import AggregatedRuleEvaluation, LoadedRulePack, RuleDiscrepancy


def evaluate_rule_pack(
    context: Any,
    rule_pack: LoadedRulePack,
) -> AggregatedRuleEvaluation:
    applied_rule_ids: list[str] = []
    decision_reasons: list[str] = []
    discrepancies: OrderedDict[tuple[str, str, str | None], RuleDiscrepancy] = OrderedDict()
    seen_warning = False
    seen_hard_block = False

    for rule_definition in rule_pack.rule_definitions:
        result = rule_definition.evaluator(context)
        if result.rule_id != rule_definition.rule_id:
            raise ValueError(
                f"Rule evaluator returned mismatched rule_id: expected {rule_definition.rule_id}, got {result.rule_id}"
            )

        applied_rule_ids.append(result.rule_id)
        decision_reasons.append(result.rationale)
        if result.outcome == FinalDecision.WARNING:
            seen_warning = True
        if result.outcome == FinalDecision.HARD_BLOCK:
            seen_hard_block = True

        for discrepancy in result.discrepancies:
            key = (discrepancy.code, discrepancy.subject_scope, discrepancy.target_ref)
            if key not in discrepancies:
                discrepancies[key] = RuleDiscrepancy(
                    code=discrepancy.code,
                    severity=discrepancy.severity,
                    message=discrepancy.message,
                    subject_scope=discrepancy.subject_scope,
                    target_ref=discrepancy.target_ref,
                    details=dict(discrepancy.details),
                    source_rule_ids=[result.rule_id],
                )
            else:
                existing = discrepancies[key]
                existing.source_rule_ids.append(result.rule_id)

    if seen_hard_block:
        final_decision = FinalDecision.HARD_BLOCK
    elif seen_warning:
        final_decision = FinalDecision.WARNING
    else:
        final_decision = FinalDecision.PASS

    sorted_discrepancies = sorted(
        discrepancies.values(),
        key=lambda item: (
            0 if item.severity == FinalDecision.HARD_BLOCK else 1,
            item.source_rule_ids[0] if item.source_rule_ids else "",
            item.code,
        ),
    )
    if not decision_reasons:
        decision_reasons.append("No rule discrepancies were emitted.")

    return AggregatedRuleEvaluation(
        applied_rule_ids=applied_rule_ids,
        discrepancies=sorted_discrepancies,
        final_decision=final_decision,
        decision_reasons=decision_reasons,
    )
