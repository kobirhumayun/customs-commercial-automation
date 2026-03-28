from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from project.models.enums import RuleStage

RuleEvaluator = Callable[[Mapping[str, Any]], Mapping[str, Any]]


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
