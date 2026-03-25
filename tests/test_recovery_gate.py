import json
from pathlib import Path

from customs_automation.core.recovery_gate import find_blocking_prior_run


def test_find_blocking_prior_run_returns_run_id_for_uncertain_state(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "run_state.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "workflow_id": "export_lc_sc",
                "write_phase_status": "uncertain_not_committed",
            }
        ),
        encoding="utf-8",
    )

    blocking_run_id = find_blocking_prior_run(tmp_path, "export_lc_sc")
    assert blocking_run_id == "run-1"


def test_find_blocking_prior_run_ignores_other_workflows(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-2"
    run_dir.mkdir(parents=True)
    (run_dir / "run_state.json").write_text(
        json.dumps(
            {
                "run_id": "run-2",
                "workflow_id": "import_btb_lc",
                "write_phase_status": "uncertain_not_committed",
            }
        ),
        encoding="utf-8",
    )

    assert find_blocking_prior_run(tmp_path, "export_lc_sc") is None
