from __future__ import annotations

from typing import Any

from project.storage import RunArtifactPaths, append_jsonl_record, write_json
from project.storage.artifacts import atomic_write_text
from project.utils.json import canonical_json_dumps


def write_run_metadata(paths: RunArtifactPaths, payload: Any) -> None:
    write_json(paths.run_metadata_path, payload)


def write_mail_outcomes(paths: RunArtifactPaths, payloads: list[Any]) -> None:
    if not payloads:
        atomic_write_text(paths.mail_outcomes_path, "")
        return
    content = "".join(f"{canonical_json_dumps(payload)}\n" for payload in payloads)
    atomic_write_text(paths.mail_outcomes_path, content)


def append_mail_outcome(paths: RunArtifactPaths, payload: Any) -> None:
    append_jsonl_record(paths.mail_outcomes_path, payload)


def write_discrepancies(paths: RunArtifactPaths, payloads: list[Any]) -> None:
    if not payloads:
        atomic_write_text(paths.discrepancies_path, "")
        return
    content = "".join(f"{canonical_json_dumps(payload)}\n" for payload in payloads)
    atomic_write_text(paths.discrepancies_path, content)


def append_discrepancy(paths: RunArtifactPaths, payload: Any) -> None:
    append_jsonl_record(paths.discrepancies_path, payload)


def write_staged_write_plan(paths: RunArtifactPaths, payload: Any) -> None:
    write_json(paths.staged_write_plan_path, payload)


def write_target_probes(paths: RunArtifactPaths, payloads: list[Any]) -> None:
    if not payloads:
        atomic_write_text(paths.target_probes_path, "")
        return
    content = "".join(f"{canonical_json_dumps(payload)}\n" for payload in payloads)
    atomic_write_text(paths.target_probes_path, content)


def write_print_plan(paths: RunArtifactPaths, payload: Any) -> None:
    write_json(paths.print_plan_path, payload)
