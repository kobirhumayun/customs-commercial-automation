import json
from pathlib import Path

from customs_automation.cli import main


def test_cli_writes_artifacts_to_custom_root(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            [
                {
                    "entry_id": "A",
                    "received_time_utc": "2026-01-01T10:00:00+00:00",
                    "subject": "mail",
                }
            ]
        ),
        encoding="utf-8",
    )

    artifacts_root = tmp_path / "custom-runs"
    exit_code = main(
        [
            "export-lc-sc",
            "--snapshot-input",
            str(snapshot_path),
            "--artifacts-root",
            str(artifacts_root),
        ]
    )

    assert exit_code == 0
    run_dirs = [path for path in artifacts_root.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert (run_dir / "run_state.json").exists()
    assert (run_dir / "run_snapshot.json").exists()
    assert (run_dir / "run_report.json").exists()
    assert (run_dir / "mail_A.json").exists()


def test_cli_blocks_when_prior_uncertain_run_exists(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "custom-runs"
    run_dir = artifacts_root / "run-20260325T010000Z"
    run_dir.mkdir(parents=True)
    (run_dir / "run_state.json").write_text(
        json.dumps(
            {
                "run_id": "run-20260325T010000Z",
                "workflow_id": "export_lc_sc",
                "write_phase_status": "uncertain_not_committed",
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["export-lc-sc", "--artifacts-root", str(artifacts_root)])
    assert exit_code == 2


def test_recovery_check_returns_success_for_safe_resume() -> None:
    exit_code = main(
        [
            "recovery-check",
            "--write-phase-status",
            "committed",
            "--probe",
            "matches_post_write",
        ]
    )
    assert exit_code == 0


def test_recovery_check_returns_block_for_contradiction() -> None:
    exit_code = main(
        [
            "recovery-check",
            "--write-phase-status",
            "not_started",
            "--probe",
            "matches_post_write",
        ]
    )
    assert exit_code == 2


def test_list_workflows_outputs_registered_workflow_commands(capsys) -> None:
    exit_code = main(["list-workflows"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "export-lc-sc" in output
    assert "ud-ip-exp" in output
    assert "import-btb-lc" in output
    assert "bb-dashboard-verification" in output


def test_show_run_outputs_run_metadata(tmp_path: Path, capsys) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            [
                {
                    "entry_id": "A",
                    "received_time_utc": "2026-01-01T10:00:00+00:00",
                    "subject": "mail",
                }
            ]
        ),
        encoding="utf-8",
    )
    artifacts_root = tmp_path / "custom-runs"
    exit_code = main(
        [
            "export-lc-sc",
            "--snapshot-input",
            str(snapshot_path),
            "--artifacts-root",
            str(artifacts_root),
        ]
    )
    assert exit_code == 0

    run_id = next(path.name for path in artifacts_root.iterdir() if path.is_dir())
    show_exit = main(["show-run", "--run-id", run_id, "--artifacts-root", str(artifacts_root)])
    output = capsys.readouterr().out

    assert show_exit == 0
    assert "Run summary" in output
    assert f"run_id: {run_id}" in output
    assert "workflow_id: export_lc_sc" in output


def test_list_workflows_json_output(capsys) -> None:
    exit_code = main(["list-workflows", "--json"])
    output = capsys.readouterr().out

    payload = json.loads(output)
    assert exit_code == 0
    assert isinstance(payload, list)
    assert any(row["command"] == "export-lc-sc" for row in payload)


def test_show_run_json_output(tmp_path: Path, capsys) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            [
                {
                    "entry_id": "A",
                    "received_time_utc": "2026-01-01T10:00:00+00:00",
                    "subject": "mail",
                }
            ]
        ),
        encoding="utf-8",
    )
    artifacts_root = tmp_path / "custom-runs"
    _ = main(
        [
            "export-lc-sc",
            "--snapshot-input",
            str(snapshot_path),
            "--artifacts-root",
            str(artifacts_root),
        ]
    )
    _ = capsys.readouterr()

    run_id = next(path.name for path in artifacts_root.iterdir() if path.is_dir())
    show_exit = main(["show-run", "--run-id", run_id, "--artifacts-root", str(artifacts_root), "--json"])
    output = capsys.readouterr().out

    payload = json.loads(output)
    assert show_exit == 0
    assert payload["run_id"] == run_id
    assert payload["workflow_id"] == "export_lc_sc"
