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
