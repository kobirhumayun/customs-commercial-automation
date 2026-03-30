from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project.models import WorkflowId
from project.workflows.run_index import list_recovery_candidates, list_workflow_runs


class RunIndexTests(unittest.TestCase):
    def test_list_workflow_runs_returns_recent_runs_with_compact_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_root = root / "export_lc_sc"
            run_old = workflow_root / "run-old"
            run_new = workflow_root / "run-new"
            run_old.mkdir(parents=True, exist_ok=True)
            run_new.mkdir(parents=True, exist_ok=True)

            (run_old / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-old",
                        "started_at_utc": "2026-03-29T00:00:00Z",
                        "completed_at_utc": None,
                        "write_phase_status": "committed",
                        "print_phase_status": "planned",
                        "mail_move_phase_status": "not_started",
                        "summary": {"pass": 1, "warning": 0, "hard_block": 0},
                        "print_group_order": ["group-1"],
                    }
                ),
                encoding="utf-8",
            )
            (run_old / "document_manual_verification.json").write_text(
                json.dumps(
                    {
                        "documents": [
                            {"manual_verification_status": "verified"},
                            {"manual_verification_status": "pending"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (run_old / "discrepancies.jsonl").write_text('{"code":"x"}\n{"code":"y"}\n', encoding="utf-8")

            (run_new / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-new",
                        "started_at_utc": "2026-03-30T00:00:00Z",
                        "completed_at_utc": "2026-03-30T01:00:00Z",
                        "write_phase_status": "committed",
                        "print_phase_status": "completed",
                        "mail_move_phase_status": "completed",
                        "summary": {"pass": 2, "warning": 0, "hard_block": 0},
                        "print_group_order": ["group-1", "group-2"],
                    }
                ),
                encoding="utf-8",
            )
            (run_new / "document_manual_verification.json").write_text(
                json.dumps(
                    {
                        "manual_verification_complete": True,
                        "pending_document_count": 0,
                    }
                ),
                encoding="utf-8",
            )

            payload = list_workflow_runs(
                run_artifact_root=root,
                workflow_id=WorkflowId.EXPORT_LC_SC,
                limit=10,
            )

        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["run_count"], 2)
        self.assertEqual(payload["runs"][0]["run_id"], "run-new")
        self.assertEqual(payload["runs"][0]["print_group_count"], 2)
        self.assertEqual(payload["runs"][0]["manual_verification_complete"], True)
        self.assertEqual(payload["runs"][1]["run_id"], "run-old")
        self.assertEqual(payload["runs"][1]["discrepancy_count"], 2)
        self.assertEqual(payload["runs"][1]["manual_verification_pending_count"], 1)

    def test_list_workflow_runs_applies_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_root = root / "export_lc_sc"
            for run_id in ("run-1", "run-2", "run-3"):
                run_dir = workflow_root / run_id
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "run_metadata.json").write_text(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "started_at_utc": f"2026-03-3{run_id[-1]}T00:00:00Z",
                            "write_phase_status": "not_started",
                            "print_phase_status": "not_started",
                            "mail_move_phase_status": "not_started",
                            "summary": {},
                            "print_group_order": [],
                        }
                    ),
                    encoding="utf-8",
                )

            payload = list_workflow_runs(
                run_artifact_root=root,
                workflow_id=WorkflowId.EXPORT_LC_SC,
                limit=2,
            )

        self.assertEqual(payload["run_count"], 2)
        self.assertEqual([item["run_id"] for item in payload["runs"]], ["run-3", "run-2"])

    def test_list_recovery_candidates_returns_only_interrupted_or_uncertain_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_root = root / "export_lc_sc"
            scenarios = {
                "run-clean": {
                    "write_phase_status": "committed",
                    "print_phase_status": "completed",
                    "mail_move_phase_status": "completed",
                    "started_at_utc": "2026-03-28T00:00:00Z",
                },
                "run-uncertain-write": {
                    "write_phase_status": "uncertain_not_committed",
                    "print_phase_status": "not_started",
                    "mail_move_phase_status": "not_started",
                    "started_at_utc": "2026-03-29T00:00:00Z",
                },
                "run-printing": {
                    "write_phase_status": "committed",
                    "print_phase_status": "printing",
                    "mail_move_phase_status": "not_started",
                    "started_at_utc": "2026-03-30T00:00:00Z",
                },
            }
            for run_id, payload in scenarios.items():
                run_dir = workflow_root / run_id
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "run_metadata.json").write_text(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "summary": {},
                            "print_group_order": [],
                            **payload,
                        }
                    ),
                    encoding="utf-8",
                )

            indexed = list_recovery_candidates(
                run_artifact_root=root,
                workflow_id=WorkflowId.EXPORT_LC_SC,
                limit=10,
            )

        self.assertEqual(indexed["run_count"], 2)
        self.assertEqual([item["run_id"] for item in indexed["runs"]], ["run-printing", "run-uncertain-write"])


if __name__ == "__main__":
    unittest.main()
