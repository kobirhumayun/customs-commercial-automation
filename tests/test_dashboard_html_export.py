from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project.models import WorkflowId
from project.workflows.dashboard_html_export import build_workflow_dashboard_html


class DashboardHtmlExportTests(unittest.TestCase):
    def test_build_workflow_dashboard_html_renders_core_sections(self) -> None:
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
            handled_run_dir = run_root / "export_lc_sc" / "run-duplicate-only"
            handled_run_dir.mkdir(parents=True, exist_ok=True)
            (handled_run_dir / "run_metadata.json").write_text(
                """
                {
                  "run_id": "run-duplicate-only",
                  "workflow_id": "export_lc_sc",
                  "tool_version": "0.1.0",
                  "rule_pack_id": "export_lc_sc.default",
                  "rule_pack_version": "1.0.0",
                  "started_at_utc": "2026-03-29T00:00:00Z",
                  "completed_at_utc": "2026-03-29T01:00:00Z",
                  "state_timezone": "Asia/Dhaka",
                  "mail_iteration_order": [],
                  "print_group_order": [],
                  "write_phase_status": "committed",
                  "print_phase_status": "completed",
                  "mail_move_phase_status": "completed",
                  "hash_algorithm": "sha256",
                  "run_start_backup_hash": "%s",
                  "current_workbook_hash": "%s",
                  "staged_write_plan_hash": "%s",
                  "summary": {}
                }
                """ % ("d" * 64, "e" * 64, "f" * 64),
                encoding="utf-8",
            )
            (handled_run_dir / "mail_outcomes.jsonl").write_text(
                """
                {"mail_id":"mail-1","decision_reasons":["Skipped workbook append for P/26/0042 because the file number already exists in the workbook."],"staged_write_operations":[],"write_disposition":"duplicate_only_noop"}
                """.strip()
                + "\n",
                encoding="utf-8",
            )
            (handled_run_dir / "staged_write_plan.json").write_text("[]\n", encoding="utf-8")
            backup_dir = backup_root / "export_lc_sc" / "run-123"
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / "master_workbook_backup.xlsx").write_bytes(b"fake")
            (backup_dir / "backup_hash.txt").write_text("abcd\n", encoding="utf-8")
            (report_root / "run_handoffs").mkdir(parents=True, exist_ok=True)
            (report_root / "run_handoffs" / "export_lc_sc.run-123.handoff.json").write_text(
                """
                {
                  "generated_at_utc": "2026-03-30T00:10:00Z",
                  "handoff_counts": {
                    "mail_count": 1,
                    "discrepancy_count": 2,
                    "manual_verification_pending_count": 1,
                    "duplicate_file_skip_count": 3,
                    "duplicate_only_mail_count": 1,
                    "mixed_duplicate_and_new_mail_count": 1,
                    "print_marker_count": 1,
                    "mail_move_marker_count": 1
                  }
                }
                """,
                encoding="utf-8",
            )
            (report_root / "workflow_handoffs").mkdir(parents=True, exist_ok=True)
            (report_root / "workflow_handoffs" / "export_lc_sc.handoff.json").write_text(
                """
                {
                  "generated_at_utc": "2026-03-30T00:00:00Z",
                  "summary_counts": {
                    "recent_run_count": 1,
                    "operator_queue_count": 1,
                    "recovery_candidate_count": 1,
                    "manual_verification_pending_count": 0,
                    "recent_handoff_count": 1,
                    "total_handoff_count": 1
                  }
                }
                """,
                encoding="utf-8",
            )

            html = build_workflow_dashboard_html(
                run_artifact_root=run_root,
                backup_root=backup_root,
                report_root=report_root,
                workflow_id=WorkflowId.EXPORT_LC_SC,
            )

        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("<h1>Workflow Dashboard: export_lc_sc</h1>", html)
        self.assertIn("<h2>Snapshot</h2>", html)
        self.assertIn("<h2>Operator Queue</h2>", html)
        self.assertIn("<h2>Handled Runs</h2>", html)
        self.assertIn("<h2>Recovery Candidates</h2>", html)
        self.assertIn("<h2>Generated Summaries</h2>", html)
        self.assertIn("Handled with no action needed", html)
        self.assertIn("Duplicate-only handled runs", html)
        self.assertIn("No-write/no-op handled runs", html)
        self.assertIn("Workflow handoffs", html)
        self.assertIn("Run handoffs", html)
        self.assertIn("<code>run-duplicate-only</code>", html)
        self.assertIn("duplicate_only_handled", html)
        self.assertIn("<h2>Workflow Handoffs</h2>", html)
        self.assertIn("<h2>Recent Run Handoffs</h2>", html)
        self.assertIn("<td>1</td><td>1</td><td>1</td>", html)
        self.assertIn("<td>2</td><td>3</td><td>1</td><td>1</td><td>1</td><td>1</td>", html)
        self.assertIn("<code>run-123</code>", html)


if __name__ == "__main__":
    unittest.main()
