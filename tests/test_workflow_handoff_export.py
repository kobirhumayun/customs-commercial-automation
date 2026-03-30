from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project.models import WorkflowId
from project.workflows.workflow_handoff_export import build_workflow_handoff_export


class WorkflowHandoffExportTests(unittest.TestCase):
    def test_build_workflow_handoff_export_bundles_summary_recovery_and_handoffs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_root = root / "runs"
            backup_root = root / "backups"
            report_root = root / "reports"

            run_dir = run_root / "export_lc_sc" / "run-123"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "run_metadata.json").write_text(
                """
                {
                  "run_id": "run-123",
                  "workflow_id": "export_lc_sc",
                  "tool_version": "0.1.0",
                  "rule_pack_id": "export_lc_sc.default",
                  "rule_pack_version": "1.0.0",
                  "started_at_utc": "2026-03-30T00:00:00Z",
                  "completed_at_utc": null,
                  "state_timezone": "Asia/Dhaka",
                  "mail_iteration_order": [],
                  "print_group_order": [],
                  "write_phase_status": "uncertain_not_committed",
                  "print_phase_status": "not_started",
                  "mail_move_phase_status": "not_started",
                  "hash_algorithm": "sha256",
                  "run_start_backup_hash": "%s",
                  "current_workbook_hash": "%s",
                  "staged_write_plan_hash": "%s",
                  "summary": {}
                }
                """ % ("a" * 64, "b" * 64, "c" * 64),
                encoding="utf-8",
            )
            (run_dir / "mail_outcomes.jsonl").write_text("", encoding="utf-8")
            (run_dir / "staged_write_plan.json").write_text("[]\n", encoding="utf-8")
            backup_dir = backup_root / "export_lc_sc" / "run-123"
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / "master_workbook_backup.xlsx").write_bytes(b"fake")
            (backup_dir / "backup_hash.txt").write_text("abcd\n", encoding="utf-8")
            (report_root / "run_handoffs").mkdir(parents=True, exist_ok=True)
            (report_root / "run_handoffs" / "export_lc_sc.run-123.handoff.json").write_text(
                "{}",
                encoding="utf-8",
            )

            payload = build_workflow_handoff_export(
                run_artifact_root=run_root,
                backup_root=backup_root,
                report_root=report_root,
                workflow_id=WorkflowId.EXPORT_LC_SC,
            )

        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertIn("workflow_summary", payload["workflow_handoff"])
        self.assertIn("recovery_packet", payload["workflow_handoff"])
        self.assertIn("recent_handoffs", payload["workflow_handoff"])
        self.assertEqual(payload["summary_counts"]["recent_handoff_count"], 1)
        self.assertEqual(payload["summary_counts"]["total_handoff_count"], 1)


if __name__ == "__main__":
    unittest.main()
