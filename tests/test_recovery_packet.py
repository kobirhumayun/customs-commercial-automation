from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project.models import WorkflowId
from project.workflows.recovery_packet import build_workflow_recovery_packet


class RecoveryPacketTests(unittest.TestCase):
    def test_build_workflow_recovery_packet_includes_prechecks_and_load_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backup_root = root / "backups"
            workflow_root = root / "export_lc_sc"

            run_good = workflow_root / "run-good"
            run_good.mkdir(parents=True, exist_ok=True)
            (run_good / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-good",
                        "workflow_id": "export_lc_sc",
                        "tool_version": "0.1.0",
                        "rule_pack_id": "export_lc_sc.default",
                        "rule_pack_version": "1.0.0",
                        "started_at_utc": "2026-03-30T00:00:00Z",
                        "completed_at_utc": None,
                        "state_timezone": "Asia/Dhaka",
                        "mail_iteration_order": [],
                        "print_group_order": [],
                        "write_phase_status": "uncertain_not_committed",
                        "print_phase_status": "not_started",
                        "mail_move_phase_status": "not_started",
                        "hash_algorithm": "sha256",
                        "run_start_backup_hash": "a" * 64,
                        "current_workbook_hash": "b" * 64,
                        "staged_write_plan_hash": "c" * 64,
                        "summary": {},
                    }
                ),
                encoding="utf-8",
            )
            (run_good / "mail_outcomes.jsonl").write_text("", encoding="utf-8")
            (run_good / "staged_write_plan.json").write_text("[]\n", encoding="utf-8")
            (backup_root / "export_lc_sc" / "run-good").mkdir(parents=True, exist_ok=True)
            ((backup_root / "export_lc_sc" / "run-good") / "master_workbook_backup.xlsx").write_bytes(b"fake")
            ((backup_root / "export_lc_sc" / "run-good") / "backup_hash.txt").write_text("abcd\n", encoding="utf-8")

            run_bad = workflow_root / "run-bad"
            run_bad.mkdir(parents=True, exist_ok=True)
            (run_bad / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-bad",
                        "started_at_utc": "2026-03-31T00:00:00Z",
                        "write_phase_status": "uncertain_not_committed",
                        "print_phase_status": "not_started",
                        "mail_move_phase_status": "not_started",
                        "summary": {},
                        "print_group_order": [],
                    }
                ),
                encoding="utf-8",
            )

            payload = build_workflow_recovery_packet(
                run_artifact_root=root,
                backup_root=backup_root,
                workflow_id=WorkflowId.EXPORT_LC_SC,
                limit=10,
            )

        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["candidate_count"], 2)
        self.assertEqual(payload["load_error_count"], 1)
        self.assertEqual(payload["runs"][0]["run_id"], "run-bad")
        self.assertIn("load_error", payload["runs"][0])
        self.assertEqual(payload["runs"][1]["run_id"], "run-good")
        self.assertTrue(payload["runs"][1]["recovery_precheck"]["needs_recovery_gate"])


if __name__ == "__main__":
    unittest.main()
