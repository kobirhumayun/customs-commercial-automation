from __future__ import annotations

import unittest

from project.workflows.run_recovery_precheck import build_recovery_precheck


class RunRecoveryPrecheckTests(unittest.TestCase):
    def test_build_recovery_precheck_flags_missing_prerequisites_and_contradictions(self) -> None:
        run_status = {
            "run_id": "run-123",
            "workflow_id": "export_lc_sc",
            "phases": {
                "write": {"status": "uncertain_not_committed"},
                "print": {"status": "completed"},
                "mail_moves": {"status": "completed"},
            },
            "manual_verification": {
                "bundle": {"audit_error_count": 1}
            },
        }
        artifact_inventory = {
            "core_files": {
                "run_metadata": {"exists": True, "nonempty": True},
                "staged_write_plan": {"exists": False, "nonempty": False},
                "commit_marker": {"exists": False, "nonempty": False},
            },
            "backup_artifacts": {
                "backup_workbook": {"exists": True, "nonempty": True},
                "backup_hash": {"exists": False, "nonempty": False},
            },
            "directories": {
                "print_markers": {"file_count": 0},
                "mail_move_markers": {"file_count": 0},
            },
        }

        payload = build_recovery_precheck(
            run_status=run_status,
            artifact_inventory=artifact_inventory,
        )

        self.assertTrue(payload["needs_recovery_gate"])
        self.assertFalse(payload["can_attempt_recovery_assessment"])
        self.assertEqual(payload["phase_statuses"]["write_phase_status"], "uncertain_not_committed")
        self.assertEqual(len(payload["missing_prerequisites"]), 2)
        self.assertEqual(len(payload["contradictions"]), 2)
        self.assertEqual(len(payload["advisories"]), 1)
        self.assertEqual(payload["issue_count"], 5)


if __name__ == "__main__":
    unittest.main()
