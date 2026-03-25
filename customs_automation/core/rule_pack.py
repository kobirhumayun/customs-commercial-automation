from __future__ import annotations

import re
from collections.abc import Sequence

SEMVER_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")


def validate_rule_pack_version(version: str) -> None:
    if not isinstance(version, str) or not version.strip():
        raise ValueError("RULE_PACK_VERSION must be a non-empty string")
    if SEMVER_PATTERN.fullmatch(version.strip()) is None:
        raise ValueError("RULE_PACK_VERSION must be a semantic version string")


def validate_applied_rule_ids(applied_rule_ids: Sequence[str]) -> None:
    if not applied_rule_ids:
        raise ValueError("At least one rule id is required for report lineage")
    if any(not isinstance(rule_id, str) or not rule_id.strip() for rule_id in applied_rule_ids):
        raise ValueError("Rule IDs must be non-empty strings")
