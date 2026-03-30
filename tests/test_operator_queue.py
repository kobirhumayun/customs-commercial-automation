from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project.models import WorkflowId
from project.workflows.operator_queue import build_operator_queue


class OperatorQueueTests(unittest.TestCase):
    def test_build_operator_queue_prioritizes_recovery_before_manual_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_root = root / "export_lc_sc"

            recovery_run = workflow_root / "run-recovery"
            recovery_run.mkdir(parents=True, exist_ok=True)
            (recovery_run / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-recovery",
                        "started_at_utc": "2026-03-30T00:00:00Z",
                        "write_phase_status": "uncertain_not_committed",
                        "print_phase_status": "not_started",
                        "mail_move_phase_status": "not_started",
                        "summary": {},
                        "print_group_order": [],
                    }
                ),
                encoding="utf-8",
            )

            manual_run = workflow_root / "run-manual"
            manual_run.mkdir(parents=True, exist_ok=True)
            (manual_run / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-manual",
                        "started_at_utc": "2026-03-31T00:00:00Z",
                        "write_phase_status": "committed",
                        "print_phase_status": "completed",
                        "mail_move_phase_status": "completed",
                        "summary": {},
                        "print_group_order": [],
                    }
                ),
                encoding="utf-8",
            )
            (manual_run / "document_manual_verification.json").write_text(
                json.dumps(
                    {
                        "manual_verification_complete": False,
                        "pending_document_count": 2,
                    }
                ),
                encoding="utf-8",
            )

            payload = build_operator_queue(
                run_artifact_root=root,
                workflow_id=WorkflowId.EXPORT_LC_SC,
                limit=10,
            )

        self.assertEqual(payload["queue_count"], 2)
        self.assertEqual(payload["recovery_candidate_count"], 1)
        self.assertEqual(payload["manual_verification_pending_count"], 1)
        self.assertEqual(payload["runs"][0]["run_id"], "run-recovery")
        self.assertEqual(payload["runs"][0]["queue_priority"], "recovery")
        self.assertEqual(payload["runs"][1]["run_id"], "run-manual")
        self.assertEqual(payload["runs"][1]["queue_priority"], "manual_verification")

    def test_build_operator_queue_applies_limit_after_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_root = root / "export_lc_sc"
            for run_id, started_at in (
                ("run-1", "2026-03-29T00:00:00Z"),
                ("run-2", "2026-03-30T00:00:00Z"),
                ("run-3", "2026-03-31T00:00:00Z"),
            ):
                run_dir = workflow_root / run_id
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "run_metadata.json").write_text(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "started_at_utc": started_at,
                            "write_phase_status": "uncertain_not_committed",
                            "print_phase_status": "not_started",
                            "mail_move_phase_status": "not_started",
                            "summary": {},
                            "print_group_order": [],
                        }
                    ),
                    encoding="utf-8",
                )

            payload = build_operator_queue(
                run_artifact_root=root,
                workflow_id=WorkflowId.EXPORT_LC_SC,
                limit=2,
            )

        self.assertEqual(payload["queue_count"], 2)
        self.assertEqual([item["run_id"] for item in payload["runs"]], ["run-3", "run-2"])


if __name__ == "__main__":
    unittest.main()
