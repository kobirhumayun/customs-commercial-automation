from __future__ import annotations

import os
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from project.models import WorkflowId
from project.workflows.retention_reporting import build_retention_report


class RetentionReportingTests(unittest.TestCase):
    def test_build_retention_report_returns_only_old_terminal_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_root = root / "runs"
            backup_root = root / "backups"
            report_root = root / "reports"
            workflow_root = run_root / "export_lc_sc"
            workflow_root.mkdir(parents=True, exist_ok=True)

            old_run = workflow_root / "run-old"
            old_run.mkdir(parents=True, exist_ok=True)
            (old_run / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-old",
                        "started_at_utc": "2026-01-01T00:00:00Z",
                        "write_phase_status": "committed",
                        "print_phase_status": "completed",
                        "mail_move_phase_status": "completed",
                        "summary": {},
                        "print_group_order": [],
                    }
                ),
                encoding="utf-8",
            )

            uncertain_run = workflow_root / "run-uncertain"
            uncertain_run.mkdir(parents=True, exist_ok=True)
            (uncertain_run / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-uncertain",
                        "started_at_utc": "2026-01-01T00:00:00Z",
                        "write_phase_status": "uncertain_not_committed",
                        "print_phase_status": "not_started",
                        "mail_move_phase_status": "not_started",
                        "summary": {},
                        "print_group_order": [],
                    }
                ),
                encoding="utf-8",
            )

            old_backup = backup_root / "export_lc_sc" / "run-old"
            old_backup.mkdir(parents=True, exist_ok=True)
            (old_backup / "master_workbook_backup.xlsx").write_bytes(b"fake")
            old_backup_timestamp = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
            os.utime(old_backup, (old_backup_timestamp, old_backup_timestamp))

            workflow_summary = report_root / "workflow_summaries" / "export_lc_sc.summary.json"
            workflow_summary.parent.mkdir(parents=True, exist_ok=True)
            workflow_summary.write_text("{}", encoding="utf-8")
            old_report_timestamp = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
            os.utime(workflow_summary, (old_report_timestamp, old_report_timestamp))

            payload = build_retention_report(
                run_artifact_root=run_root,
                backup_root=backup_root,
                report_root=report_root,
                workflow_id=WorkflowId.EXPORT_LC_SC,
                older_than_days=30,
                now_utc=datetime(2026, 3, 30, tzinfo=UTC),
            )

        self.assertEqual(payload["summary_counts"]["stale_run_count"], 1)
        self.assertEqual(payload["stale_runs"][0]["run_id"], "run-old")
        self.assertEqual(payload["summary_counts"]["stale_backup_count"], 1)
        self.assertEqual(payload["stale_backups"][0]["run_id"], "run-old")
        self.assertEqual(payload["summary_counts"]["stale_report_count"], 1)
        self.assertEqual(payload["stale_reports"][0]["artifact_type"], "workflow_summary")


if __name__ == "__main__":
    unittest.main()
