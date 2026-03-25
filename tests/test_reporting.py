import json
from pathlib import Path

import pytest

from customs_automation.core.contracts import Decision, DiscrepancyEntry
from customs_automation.core.reporting import ReportWriter, build_run_report


def test_build_run_report_requires_rule_ids() -> None:
    with pytest.raises(ValueError):
        build_run_report(
            run_id="run-1",
            workflow_id="export_lc_sc",
            rule_pack_id="export_lc_sc.default",
            rule_pack_version="1.0.0",
            applied_rule_ids=[],
            final_decision=Decision.PASS,
        )


def test_report_writer_persists_report(tmp_path: Path) -> None:
    report = build_run_report(
        run_id="run-1",
        workflow_id="export_lc_sc",
        rule_pack_id="export_lc_sc.default",
        rule_pack_version="1.0.0",
        applied_rule_ids=["core.cli.bootstrap.v1"],
        discrepancies=[
            DiscrepancyEntry(
                code="example_warning",
                severity=Decision.WARNING,
                message="example",
            )
        ],
        final_decision=Decision.PASS,
        mail_count=3,
    )
    writer = ReportWriter(tmp_path)
    output_path = writer.write_run_report(report)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["workflow_id"] == "export_lc_sc"
    assert payload["applied_rule_ids"] == ["core.cli.bootstrap.v1"]
    assert payload["discrepancies"][0]["severity"] == "warning"
    assert payload["mail_count"] == 3
