import json
from datetime import UTC, datetime
from pathlib import Path

from customs_automation.core.contracts import EmailMessage
from customs_automation.core.run_snapshot import (
    build_run_snapshot,
    order_messages_deterministically,
    write_snapshot,
)


def test_mail_ordering_uses_received_time_then_entry_id() -> None:
    ordered = order_messages_deterministically(
        [
            EmailMessage(entry_id="B", received_time_utc=datetime(2026, 1, 1, 10, 0, tzinfo=UTC), subject=""),
            EmailMessage(entry_id="A", received_time_utc=datetime(2026, 1, 1, 10, 0, tzinfo=UTC), subject=""),
            EmailMessage(entry_id="C", received_time_utc=datetime(2026, 1, 1, 10, 1, tzinfo=UTC), subject=""),
        ]
    )

    assert [item.entry_id for item in ordered] == ["A", "B", "C"]
    assert ordered[0].order_index == 0
    assert "+06:00" in ordered[0].received_time_local_iso


def test_write_snapshot_persists_json(tmp_path: Path) -> None:
    snapshot = build_run_snapshot(
        run_id="run-1",
        workflow_id="export_lc_sc",
        messages=[EmailMessage(entry_id="A", received_time_utc=datetime(2026, 1, 1, 10, 0, tzinfo=UTC), subject="")],
    )

    path = write_snapshot(snapshot, tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["run_id"] == "run-1"
    assert payload["workflow_id"] == "export_lc_sc"
    assert payload["ordered_mail_ids"] == ["A"]
