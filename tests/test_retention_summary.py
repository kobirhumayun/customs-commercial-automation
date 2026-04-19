from __future__ import annotations

import os
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from project.models import WorkflowId
from project.workflows.retention_summary import build_retention_summary


class RetentionSummaryTests(unittest.TestCase):
    def test_build_retention_summary_wraps_retention_report_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_root = root / "runs"
            backup_root = root / "backups"
            report_root = root / "reports"
            workflow_root = run_root / "export_lc_sc"
            workflow_root.mkdir(parents=True, exist_ok=True)

            run_dir = workflow_root / "run-old"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "run_metadata.json").write_text(
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
            report_file = report_root / "workflow_summaries" / "export_lc_sc.summary.json"
            report_file.parent.mkdir(parents=True, exist_ok=True)
            report_file.write_text("{}", encoding="utf-8")
            old_timestamp = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
            os.utime(report_file, (old_timestamp, old_timestamp))

            payload = build_retention_summary(
                run_artifact_root=run_root,
                backup_root=backup_root,
                report_root=report_root,
                workflow_id=WorkflowId.EXPORT_LC_SC,
                older_than_days=30,
            )

        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["older_than_days"], 30)
        self.assertEqual(payload["summary_counts"]["stale_run_count"], 1)
        self.assertEqual(payload["summary_counts"]["stale_report_count"], 1)
        self.assertEqual(payload["retention_report"]["workflow_id"], "export_lc_sc")


if __name__ == "__main__":
    unittest.main()
