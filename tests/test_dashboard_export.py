from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project.models import WorkflowId
from project.workflows.dashboard_export import build_workflow_dashboard_markdown


class DashboardExportTests(unittest.TestCase):
    def test_build_workflow_dashboard_markdown_renders_core_sections(self) -> None:
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

            markdown = build_workflow_dashboard_markdown(
                run_artifact_root=run_root,
                backup_root=backup_root,
                report_root=report_root,
                workflow_id=WorkflowId.EXPORT_LC_SC,
            )

        self.assertIn("# Workflow Dashboard: export_lc_sc", markdown)
        self.assertIn("## Snapshot", markdown)
        self.assertIn("## Operator Queue", markdown)
        self.assertIn("## Handled Runs", markdown)
        self.assertIn("## Recovery Candidates", markdown)
        self.assertIn("## Generated Summaries", markdown)
        self.assertIn("- Handled with no action needed: 1", markdown)
        self.assertIn("- Duplicate-only handled runs: 1", markdown)
        self.assertIn("- No-write/no-op handled runs: 0", markdown)
        self.assertIn("- Workflow handoffs: 1", markdown)
        self.assertIn("- Run handoffs: 1", markdown)
        self.assertIn("`run-duplicate-only` [duplicate_only_handled]", markdown)
        self.assertIn("## Workflow Handoffs", markdown)
        self.assertIn("## Recent Run Handoffs", markdown)
        self.assertIn("queue=1 recovery=1 recent_handoffs=1", markdown)
        self.assertIn(
            "discrepancies=2 duplicate_skips=3 duplicate_only_mails=1 mixed_duplicate_new_mails=1 print_markers=1 mail_move_markers=1",
            markdown,
        )
        self.assertIn("`run-123`", markdown)


if __name__ == "__main__":
    unittest.main()
