from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

RULE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(core|export_lc_sc|ud_ip_exp|import_btb_lc|bb_dashboard_verification)\.[a-z0-9_]+\.[a-z0-9_]+\.v[1-9]\d*$"
)


@dataclass(frozen=True, slots=True)
class RuleRegistry:
    ids: set[str]


class RuleRegistryError(ValueError):
    """Raised when rule registry contracts are violated."""


def load_rule_registry(path: Path | None = None) -> RuleRegistry:
    registry_path = path or Path("rules") / "registry" / "rule_ids.json"
    if not registry_path.exists():
        raise RuleRegistryError(f"Missing rule registry file: {registry_path}")

    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "rule_ids" not in payload:
        raise RuleRegistryError("Rule registry payload must contain 'rule_ids'")

    raw_ids = payload["rule_ids"]
    if not isinstance(raw_ids, list) or not raw_ids:
        raise RuleRegistryError("Rule registry must define a non-empty list of IDs")

    seen: set[str] = set()
    for rule_id in raw_ids:
        validate_rule_id_pattern(rule_id)
        if rule_id in seen:
            raise RuleRegistryError(f"Duplicate rule id in registry: {rule_id}")
        seen.add(rule_id)

    return RuleRegistry(ids=seen)


def validate_rule_id_pattern(rule_id: str) -> None:
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise RuleRegistryError("Rule ID must be a non-empty string")
    if RULE_ID_PATTERN.fullmatch(rule_id.strip()) is None:
        raise RuleRegistryError(f"Rule ID does not match governance pattern: {rule_id}")


def validate_rule_ids_registered(rule_ids: list[str], registry: RuleRegistry) -> None:
    for rule_id in rule_ids:
        validate_rule_id_pattern(rule_id)
        if rule_id not in registry.ids:
            raise RuleRegistryError(f"Rule ID is not registered: {rule_id}")
