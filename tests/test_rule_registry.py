import json
from pathlib import Path

import pytest

from customs_automation.core.rule_registry import (
    RuleRegistry,
    RuleRegistryError,
    load_rule_registry,
    validate_rule_id_pattern,
    validate_rule_ids_registered,
)


def test_rule_id_pattern_accepts_supported_scope() -> None:
    validate_rule_id_pattern("export_lc_sc.bootstrap.stub.v1")


def test_rule_id_pattern_rejects_invalid_shape() -> None:
    with pytest.raises(RuleRegistryError):
        validate_rule_id_pattern("core.bad")


def test_validate_rule_ids_registered_rejects_unknown_id() -> None:
    registry = RuleRegistry(ids={"core.cli.bootstrap.v1"})
    with pytest.raises(RuleRegistryError):
        validate_rule_ids_registered(["ud_ip_exp.bootstrap.stub.v1"], registry)


def test_load_rule_registry_from_file(tmp_path: Path) -> None:
    registry_file = tmp_path / "rule_ids.json"
    registry_file.write_text(
        json.dumps({"rule_ids": ["core.cli.bootstrap.v1", "ud_ip_exp.bootstrap.stub.v1"]}),
        encoding="utf-8",
    )

    registry = load_rule_registry(registry_file)

    assert "core.cli.bootstrap.v1" in registry.ids
    assert "ud_ip_exp.bootstrap.stub.v1" in registry.ids
