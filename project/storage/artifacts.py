from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project.exceptions import ArtifactError
from project.utils.hashing import sha256_file
from project.utils.json import pretty_json_dumps


@dataclass(slots=True, frozen=True)
class RunArtifactPaths:
    run_root: Path
    backup_root: Path
    run_metadata_path: Path
    mail_outcomes_path: Path
    staged_write_plan_path: Path
    target_probes_path: Path
    print_plan_path: Path
    discrepancies_path: Path
    print_markers_dir: Path
    mail_move_markers_dir: Path
    logs_dir: Path
    backup_workbook_path: Path
    backup_hash_path: Path


def create_run_artifact_layout(
    *,
    run_artifact_root: Path,
    backup_root: Path,
    workflow_id: str,
    run_id: str,
) -> RunArtifactPaths:
    run_root = run_artifact_root / workflow_id / run_id
    backup_run_root = backup_root / workflow_id / run_id
    paths = RunArtifactPaths(
        run_root=run_root,
        backup_root=backup_run_root,
        run_metadata_path=run_root / "run_metadata.json",
        mail_outcomes_path=run_root / "mail_outcomes.jsonl",
        staged_write_plan_path=run_root / "staged_write_plan.json",
        target_probes_path=run_root / "target_probes.jsonl",
        print_plan_path=run_root / "print_plan.json",
        discrepancies_path=run_root / "discrepancies.jsonl",
        print_markers_dir=run_root / "print_markers",
        mail_move_markers_dir=run_root / "mail_move_markers",
        logs_dir=run_root / "logs",
        backup_workbook_path=backup_run_root / "master_workbook_backup.xlsx",
        backup_hash_path=backup_run_root / "backup_hash.txt",
    )
    for directory in (
        run_root,
        backup_run_root,
        paths.print_markers_dir,
        paths.mail_move_markers_dir,
        paths.logs_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def initialize_run_artifacts(
    *,
    paths: RunArtifactPaths,
    run_metadata: Any,
    staged_write_plan: list[dict[str, Any]] | None = None,
    print_plan: dict[str, Any] | None = None,
) -> None:
    write_json(paths.run_metadata_path, run_metadata)
    write_json(paths.staged_write_plan_path, staged_write_plan or [])
    write_json(paths.print_plan_path, print_plan or {"print_groups": [], "print_group_order": []})
    atomic_write_text(paths.mail_outcomes_path, "")
    atomic_write_text(paths.target_probes_path, "")
    atomic_write_text(paths.discrepancies_path, "")


def copy_workbook_backup(source_workbook: Path, destination_path: Path) -> str:
    if not source_workbook.exists():
        raise ArtifactError(f"Master workbook does not exist: {source_workbook}")
    shutil.copy2(source_workbook, destination_path)
    backup_hash = sha256_file(destination_path)
    atomic_write_text(destination_path.parent / "backup_hash.txt", f"{backup_hash}\n")
    return backup_hash


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, pretty_json_dumps(payload))


def append_jsonl_record(path: Path, payload: Any) -> None:
    line = pretty_json_dumps(payload).rstrip("\n")
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    new_content = f"{existing}{line}\n"
    atomic_write_text(path, new_content)


def atomic_write_text(path: Path, content: str) -> None:
    temp_path = path.parent / f"{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise ArtifactError(f"Failed to persist artifact atomically: {path}") from exc
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        return
    finally:
        os.close(fd)
