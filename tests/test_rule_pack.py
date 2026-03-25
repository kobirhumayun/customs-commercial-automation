import pytest

from customs_automation.core.rule_pack import validate_rule_pack_version


def test_validate_rule_pack_version_accepts_semver() -> None:
    validate_rule_pack_version("1.2.3")


def test_validate_rule_pack_version_rejects_invalid_version() -> None:
    with pytest.raises(ValueError):
        validate_rule_pack_version("v1")
