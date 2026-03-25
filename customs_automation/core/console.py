from __future__ import annotations

from pathlib import Path


def format_run_summary(
    *,
    run_id: str,
    workflow_id: str,
    decision: str,
    mail_count: int,
    run_state_path: Path,
    run_report_path: Path,
    run_snapshot_path: Path,
) -> str:
    return (
        f"Run completed\n"
        f"  run_id: {run_id}\n"
        f"  workflow: {workflow_id}\n"
        f"  decision: {decision}\n"
        f"  mail_count: {mail_count}\n"
        f"  run_snapshot: {run_snapshot_path}\n"
        f"  run_state: {run_state_path}\n"
        f"  run_report: {run_report_path}"
    )
