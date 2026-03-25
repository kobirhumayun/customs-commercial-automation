from datetime import UTC, datetime
from pathlib import Path

from customs_automation.core.contracts import EmailMessage
from customs_automation.core.intake import StaticIntakeAdapter
from customs_automation.core.orchestrator import execute_workflow_run
from customs_automation.core.run_state import RunContext, RunStateStore, new_run_state_record


def test_execute_workflow_run_persists_ordered_mail_ids_and_phase(tmp_path: Path) -> None:
    context = RunContext(
        run_id="run-20260325T120000Z",
        workflow_id="export_lc_sc",
        rule_pack_id="export_lc_sc.default",
        rule_pack_version="1.0.0",
    )
    store = RunStateStore(base_dir=tmp_path)
    store.write_state(new_run_state_record(context))

    result = execute_workflow_run(
        context=context,
        run_state_store=store,
        intake_adapter=StaticIntakeAdapter(
            messages=[
                EmailMessage("B", datetime(2026, 1, 1, 10, 0, tzinfo=UTC), ""),
                EmailMessage("A", datetime(2026, 1, 1, 10, 0, tzinfo=UTC), ""),
            ]
        ),
        applied_rule_ids=["core.cli.bootstrap.v1", "export_lc_sc.bootstrap.stub.v1"],
        workflow_exit_code=0,
    )

    persisted = store.read_state(context.run_id)
    assert persisted.mail_iteration_order == ["A", "B"]
    assert persisted.write_phase_status == "prevalidated"
    assert result.run_snapshot_path.name == "run_snapshot.json"
    assert result.run_snapshot_path.exists()
    assert result.run_report.final_decision == "pass"
