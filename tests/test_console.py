from pathlib import Path

from customs_automation.core.console import format_run_summary


def test_format_run_summary_contains_core_fields() -> None:
    output = format_run_summary(
        run_id="run-20260325T120000Z",
        workflow_id="export_lc_sc",
        decision="pass",
        run_state_path=Path("artifacts/runs/run-1/run_state.json"),
        run_report_path=Path("artifacts/runs/run-1/run_report.json"),
        run_snapshot_path=Path("artifacts/runs/run-1/run_snapshot.json"),
    )

    assert "run_id: run-20260325T120000Z" in output
    assert "workflow: export_lc_sc" in output
    assert "decision: pass" in output
    assert "run_snapshot.json" in output
    assert "run_state.json" in output
    assert "run_report.json" in output
