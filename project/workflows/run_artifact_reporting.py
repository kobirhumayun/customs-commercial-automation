from __future__ import annotations

from pathlib import Path
from typing import Any

from project.storage import RunArtifactPaths


def summarize_run_artifacts(
    *,
    artifact_paths: RunArtifactPaths,
) -> dict[str, Any]:
    return {
        "run_root": str(artifact_paths.run_root),
        "backup_root": str(artifact_paths.backup_root),
        "core_files": {
            "run_metadata": _file_summary(artifact_paths.run_metadata_path, kind="json"),
            "mail_outcomes": _file_summary(artifact_paths.mail_outcomes_path, kind="jsonl"),
            "manual_document_verification": _file_summary(
                artifact_paths.manual_document_verification_path,
                kind="json",
            ),
            "staged_write_plan": _file_summary(artifact_paths.staged_write_plan_path, kind="json"),
            "target_probes": _file_summary(artifact_paths.target_probes_path, kind="jsonl"),
            "print_plan": _file_summary(artifact_paths.print_plan_path, kind="json"),
            "discrepancies": _file_summary(artifact_paths.discrepancies_path, kind="jsonl"),
            "commit_marker": _file_summary(artifact_paths.commit_marker_path, kind="json"),
        },
        "directories": {
            "document_audits": _directory_summary(artifact_paths.document_audits_dir),
            "print_markers": _directory_summary(artifact_paths.print_markers_dir),
            "mail_move_markers": _directory_summary(artifact_paths.mail_move_markers_dir),
            "logs": _directory_summary(artifact_paths.logs_dir),
        },
        "backup_artifacts": {
            "backup_workbook": _file_summary(artifact_paths.backup_workbook_path),
            "backup_hash": _file_summary(artifact_paths.backup_hash_path),
        },
    }


def _file_summary(path: Path, *, kind: str | None = None) -> dict[str, Any]:
    exists = path.exists()
    summary: dict[str, Any] = {
        "path": str(path),
        "exists": exists,
        "nonempty": False,
        "size_bytes": 0,
    }
    if not exists:
        if kind == "jsonl":
            summary["record_count"] = 0
        return summary

    size_bytes = path.stat().st_size
    summary["size_bytes"] = size_bytes
    summary["nonempty"] = size_bytes > 0
    if kind == "jsonl":
        summary["record_count"] = _count_nonempty_lines(path)
    return summary


def _directory_summary(path: Path) -> dict[str, Any]:
    exists = path.exists()
    summary: dict[str, Any] = {
        "path": str(path),
        "exists": exists,
        "file_count": 0,
        "json_file_count": 0,
    }
    if not exists:
        return summary

    files = [child for child in path.iterdir() if child.is_file()]
    summary["file_count"] = len(files)
    summary["json_file_count"] = sum(1 for child in files if child.suffix.lower() == ".json")
    return summary


def _count_nonempty_lines(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
