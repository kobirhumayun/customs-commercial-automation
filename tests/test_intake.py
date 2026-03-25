import json
from pathlib import Path

import pytest

from customs_automation.cli import build_parser
from customs_automation.core.intake import JsonFileIntakeAdapter


def test_json_file_intake_adapter_loads_messages(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            [
                {
                    "entry_id": "A",
                    "received_time_utc": "2026-01-01T10:00:00+00:00",
                    "subject": "hello",
                }
            ]
        ),
        encoding="utf-8",
    )

    messages = JsonFileIntakeAdapter(snapshot_path=snapshot_path).list_working_messages()
    assert len(messages) == 1
    assert messages[0].entry_id == "A"


def test_json_file_intake_adapter_rejects_missing_timezone(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps([{"entry_id": "A", "received_time_utc": "2026-01-01T10:00:00", "subject": "x"}]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        JsonFileIntakeAdapter(snapshot_path=snapshot_path).list_working_messages()


def test_cli_parser_accepts_snapshot_input_option() -> None:
    parser = build_parser()
    parsed = parser.parse_args(["export-lc-sc", "--snapshot-input", "sample.json"])
    assert parsed.snapshot_input.name == "sample.json"
