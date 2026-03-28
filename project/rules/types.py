from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from project.models.enums import FinalDecision
from project.models.enums import RuleStage

if TYPE_CHECKING:
    from project.workflows.validation import WorkflowValidationContext

RuleEvaluator = Callable[["WorkflowValidationContext"], "RuleEvaluationResult"]


@dataclass(slots=True, frozen=True)
class RuleDefinition:
    rule_id: str
    stage: RuleStage
    evaluator: RuleEvaluator


@dataclass(slots=True, frozen=True)
class LoadedRulePack:
    rule_pack_id: str
    rule_pack_version: str
    rule_definitions: tuple[RuleDefinition, ...]


@dataclass(slots=True, frozen=True)
class RuleDiscrepancy:
    code: str
    severity: FinalDecision
    message: str
    subject_scope: str
    target_ref: str | None
    details: dict[str, Any] = field(default_factory=dict)
    source_rule_ids: list[str] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class RuleEvaluationResult:
    rule_id: str
    outcome: FinalDecision
    rationale: str
    discrepancies: tuple[RuleDiscrepancy, ...] = ()


@dataclass(slots=True, frozen=True)
class AggregatedRuleEvaluation:
    applied_rule_ids: list[str]
    discrepancies: list[RuleDiscrepancy]
    final_decision: FinalDecision
    decision_reasons: list[str]
