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
            backup_dir = backup_root / "export_lc_sc" / "run-123"
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / "master_workbook_backup.xlsx").write_bytes(b"fake")
            (backup_dir / "backup_hash.txt").write_text("abcd\n", encoding="utf-8")
            (report_root / "run_handoffs").mkdir(parents=True, exist_ok=True)
            (report_root / "run_handoffs" / "export_lc_sc.run-123.handoff.json").write_text(
                "{}",
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
        self.assertIn("## Recovery Candidates", markdown)
        self.assertIn("## Generated Summaries", markdown)
        self.assertIn("- Run handoffs: 1", markdown)
        self.assertIn("`run-123`", markdown)


if __name__ == "__main__":
    unittest.main()
