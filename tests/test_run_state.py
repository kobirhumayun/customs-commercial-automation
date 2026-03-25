import json
from pathlib import Path

from customs_automation.core.run_state import (
    RunContext,
    RunStateStore,
    generate_run_id,
    new_run_state_record,
)


def test_generate_run_id_has_expected_shape() -> None:
    run_id = generate_run_id()
    assert run_id.startswith("run-")
    assert run_id.endswith("Z")


def test_run_state_store_writes_json(tmp_path: Path) -> None:
    context = RunContext(
        run_id="run-20260325T120000Z",
        workflow_id="export_lc_sc",
        rule_pack_id="export_lc_sc.default",
        rule_pack_version="1.0.0",
    )
    store = RunStateStore(base_dir=tmp_path)

    path = store.write_state(new_run_state_record(context))

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_id"] == context.run_id
    assert payload["workflow_id"] == context.workflow_id
    assert payload["write_phase_status"] == "not_started"
