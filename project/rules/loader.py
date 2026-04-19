from __future__ import annotations

import importlib
import tomllib
from pathlib import Path
from types import ModuleType

from project.exceptions import RulePackError
from project.models.enums import RuleStage, WorkflowId
from project.rules.types import LoadedRulePack, RuleDefinition
from project.utils.validation import is_semver

RULE_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "rules" / "registry.toml"
STAGE_ORDER = {
    RuleStage.CORE: 0,
    RuleStage.WORKFLOW_STANDARD: 1,
    RuleStage.WORKFLOW_EXCEPTION: 2,
}


def load_rule_pack(workflow_id: WorkflowId) -> LoadedRulePack:
    registered_rule_ids = _load_rule_registry_ids()
    core_module = _load_required_module("project.rules.core")
    workflow_module = _load_required_module(f"project.rules.workflows.{workflow_id.value}")
    modules = [core_module, workflow_module]
    exceptions_module = _load_optional_module(
        f"project.rules.workflows.{workflow_id.value}.exceptions"
    )
    if exceptions_module is not None:
        modules.append(exceptions_module)

    workflow_pack_id = _validate_pack_metadata(workflow_module)
    workflow_pack_version = str(getattr(workflow_module, "RULE_PACK_VERSION"))

    rules: list[RuleDefinition] = []
    seen_rule_ids: set[str] = set()
    for module in modules:
        rules.extend(
            _extract_rule_definitions(
                module=module,
                registered_rule_ids=registered_rule_ids,
                seen_rule_ids=seen_rule_ids,
            )
        )

    rules.sort(key=lambda rule: (STAGE_ORDER[rule.stage], rule.rule_id))
    return LoadedRulePack(
        rule_pack_id=workflow_pack_id,
        rule_pack_version=workflow_pack_version,
        rule_definitions=tuple(rules),
    )


def _load_rule_registry_ids() -> set[str]:
    if not RULE_REGISTRY_PATH.exists():
        raise RulePackError(f"Rule registry is missing: {RULE_REGISTRY_PATH}")
    with RULE_REGISTRY_PATH.open("rb") as handle:
        content = tomllib.load(handle)
    rule_ids = content.get("rule_ids", [])
    if not isinstance(rule_ids, list) or not all(isinstance(item, str) for item in rule_ids):
        raise RulePackError("Rule registry must expose 'rule_ids' as an array of strings")
    return set(rule_ids)


def _load_required_module(module_path: str) -> ModuleType:
    try:
        return importlib.import_module(module_path)
    except ImportError as exc:
        raise RulePackError(f"Unable to load required rule module: {module_path}") from exc


def _load_optional_module(module_path: str) -> ModuleType | None:
    try:
        return importlib.import_module(module_path)
    except ModuleNotFoundError:
        return None


def _validate_pack_metadata(module: ModuleType) -> str:
    pack_id = getattr(module, "RULE_PACK_ID", None)
    pack_version = getattr(module, "RULE_PACK_VERSION", None)
    if not isinstance(pack_id, str) or not pack_id.strip():
        raise RulePackError(f"{module.__name__} is missing RULE_PACK_ID")
    if not isinstance(pack_version, str) or not is_semver(pack_version):
        raise RulePackError(f"{module.__name__} has an invalid RULE_PACK_VERSION")
    if not hasattr(module, "RULE_DEFINITIONS"):
        raise RulePackError(f"{module.__name__} is missing RULE_DEFINITIONS")
    return pack_id


def _extract_rule_definitions(
    module: ModuleType,
    registered_rule_ids: set[str],
    seen_rule_ids: set[str],
) -> list[RuleDefinition]:
    _validate_pack_metadata(module)
    raw_definitions = getattr(module, "RULE_DEFINITIONS")
    if not isinstance(raw_definitions, (list, tuple)):
        raise RulePackError(f"{module.__name__}.RULE_DEFINITIONS must be a sequence")

    extracted: list[RuleDefinition] = []
    for item in raw_definitions:
        if not isinstance(item, RuleDefinition):
            raise RulePackError(f"{module.__name__} contains a non-RuleDefinition rule entry")
        if item.rule_id in seen_rule_ids:
            raise RulePackError(f"Duplicate rule_id detected: {item.rule_id}")
        if item.stage not in STAGE_ORDER:
            raise RulePackError(f"Unknown rule stage for {item.rule_id}: {item.stage}")
        if registered_rule_ids and item.rule_id not in registered_rule_ids:
            raise RulePackError(f"Rule id is missing from the registry: {item.rule_id}")
        seen_rule_ids.add(item.rule_id)
        extracted.append(item)
    return extracted
