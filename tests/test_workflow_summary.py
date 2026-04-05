from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project.models import WorkflowId
from project.workflows.workflow_summary import build_workflow_summary


class WorkflowSummaryTests(unittest.TestCase):
    def test_build_workflow_summary_combines_recent_runs_and_operator_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_root = root / "export_lc_sc"

            recent_run = workflow_root / "run-recent"
            recent_run.mkdir(parents=True, exist_ok=True)
            (recent_run / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-recent",
                        "started_at_utc": "2026-03-30T00:00:00Z",
                        "write_phase_status": "uncertain_not_committed",
                        "print_phase_status": "not_started",
                        "mail_move_phase_status": "not_started",
                        "summary": {"pass": 1, "warning": 0, "hard_block": 0},
                        "print_group_order": [],
                    }
                ),
                encoding="utf-8",
            )
            (recent_run / "mail_outcomes.jsonl").write_text(
                json.dumps(
                    {
                        "mail_id": "mail-recent",
                        "decision_reasons": [
                            "Skipped workbook append for P/26/0042 because the file number already exists in the workbook."
                        ],
                        "staged_write_operations": [],
                        "write_disposition": "duplicate_only_noop",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            clean_run = workflow_root / "run-clean"
            clean_run.mkdir(parents=True, exist_ok=True)
            (clean_run / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-clean",
                        "started_at_utc": "2026-03-29T00:00:00Z",
                        "write_phase_status": "committed",
                        "print_phase_status": "completed",
                        "mail_move_phase_status": "completed",
                        "summary": {"pass": 1, "warning": 0, "hard_block": 0},
                        "print_group_order": [],
                    }
                ),
                encoding="utf-8",
            )

            payload = build_workflow_summary(
                run_artifact_root=root,
                workflow_id=WorkflowId.EXPORT_LC_SC,
                recent_limit=10,
                queue_limit=10,
            )

        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["recent_runs"]["run_count"], 2)
        self.assertEqual(payload["operator_queue"]["queue_count"], 1)
        self.assertEqual(payload["operator_queue"]["runs"][0]["run_id"], "run-recent")
        self.assertEqual(payload["summary_counts"]["recent_run_count"], 2)
        self.assertEqual(payload["summary_counts"]["operator_queue_count"], 1)
        self.assertEqual(payload["summary_counts"]["handled_no_action_count"], 0)
        self.assertEqual(payload["summary_counts"]["duplicate_only_handled_count"], 0)
        self.assertEqual(payload["summary_counts"]["no_write_noop_handled_count"], 0)
        self.assertEqual(payload["summary_counts"]["recent_duplicate_file_skip_count"], 1)
        self.assertEqual(payload["summary_counts"]["recent_duplicate_only_mail_count"], 1)
        self.assertEqual(payload["summary_counts"]["recent_mixed_duplicate_and_new_mail_count"], 0)

    def test_build_workflow_summary_surfaces_duplicate_only_handled_runs_separately_from_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "export_lc_sc" / "run-duplicate-only"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-duplicate-only",
                        "started_at_utc": "2026-04-01T00:00:00Z",
                        "write_phase_status": "committed",
                        "print_phase_status": "completed",
                        "mail_move_phase_status": "completed",
                        "summary": {"pass": 1, "warning": 0, "hard_block": 0},
                        "print_group_order": [],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "mail_outcomes.jsonl").write_text(
                json.dumps(
                    {
                        "mail_id": "mail-1",
                        "decision_reasons": [
                            "Skipped workbook append for P/26/0042 because the file number already exists in the workbook."
                        ],
                        "staged_write_operations": [],
                        "write_disposition": "duplicate_only_noop",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = build_workflow_summary(
                run_artifact_root=root,
                workflow_id=WorkflowId.EXPORT_LC_SC,
                recent_limit=10,
                queue_limit=10,
            )

        self.assertEqual(payload["operator_queue"]["queue_count"], 0)
        self.assertEqual(payload["operator_queue"]["handled_no_action_count"], 1)
        self.assertEqual(payload["summary_counts"]["handled_no_action_count"], 1)
        self.assertEqual(payload["summary_counts"]["duplicate_only_handled_count"], 1)
        self.assertEqual(payload["summary_counts"]["no_write_noop_handled_count"], 0)


if __name__ == "__main__":
    unittest.main()
