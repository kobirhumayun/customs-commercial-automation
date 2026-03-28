from __future__ import annotations

from typing import Any

from project.storage import RunArtifactPaths, append_jsonl_record, write_json


def write_run_metadata(paths: RunArtifactPaths, payload: Any) -> None:
    write_json(paths.run_metadata_path, payload)


def append_mail_outcome(paths: RunArtifactPaths, payload: Any) -> None:
    append_jsonl_record(paths.mail_outcomes_path, payload)


def append_discrepancy(paths: RunArtifactPaths, payload: Any) -> None:
    append_jsonl_record(paths.discrepancies_path, payload)


def write_staged_write_plan(paths: RunArtifactPaths, payload: Any) -> None:
    write_json(paths.staged_write_plan_path, payload)


def write_print_plan(paths: RunArtifactPaths, payload: Any) -> None:
    write_json(paths.print_plan_path, payload)
