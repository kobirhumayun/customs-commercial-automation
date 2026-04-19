from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project.models import WorkflowId
from project.workflows.summary_catalog import build_summary_catalog


class SummaryCatalogTests(unittest.TestCase):
    def test_build_summary_catalog_indexes_existing_generated_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "workflow_summaries").mkdir(parents=True, exist_ok=True)
            (root / "workflow_handoffs").mkdir(parents=True, exist_ok=True)
            (root / "run_summaries").mkdir(parents=True, exist_ok=True)
            (root / "run_handoffs").mkdir(parents=True, exist_ok=True)
            (root / "recovery_packets").mkdir(parents=True, exist_ok=True)
            (root / "retention_reports").mkdir(parents=True, exist_ok=True)

            (root / "workflow_summaries" / "export_lc_sc.summary.json").write_text("{}", encoding="utf-8")
            (root / "workflow_handoffs" / "export_lc_sc.handoff.json").write_text(
                json.dumps(
                    {
                        "generated_at_utc": "2026-03-30T00:00:00Z",
                        "summary_counts": {
                            "recent_run_count": 2,
                            "operator_queue_count": 1,
                            "recovery_candidate_count": 1,
                            "manual_verification_pending_count": 0,
                            "recent_handoff_count": 1,
                            "total_handoff_count": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "run_summaries" / "export_lc_sc.run-123.summary.json").write_text("{}", encoding="utf-8")
            (root / "run_handoffs" / "export_lc_sc.run-123.handoff.json").write_text(
                json.dumps(
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
                            "mail_move_marker_count": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "recovery_packets" / "export_lc_sc.recovery.json").write_text("{}", encoding="utf-8")
            (root / "retention_reports" / "export_lc_sc.retention.json").write_text("{}", encoding="utf-8")

            payload = build_summary_catalog(
                report_root=root,
                workflow_id=WorkflowId.EXPORT_LC_SC,
            )

        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["summary_counts"]["total_summary_count"], 6)
        self.assertEqual(payload["summary_counts"]["workflow_handoff_count"], 1)
        self.assertEqual(payload["summary_counts"]["run_summary_count"], 1)
        self.assertEqual(payload["summary_counts"]["run_handoff_count"], 1)
        self.assertEqual(payload["workflow_handoffs"][0]["artifact_type"], "workflow_handoff")
        self.assertEqual(payload["workflow_handoffs"][0]["artifact_metadata"]["operator_queue_count"], 1)
        self.assertEqual(payload["run_summaries"][0]["run_id"], "run-123")
        self.assertEqual(payload["run_handoffs"][0]["run_id"], "run-123")
        self.assertEqual(payload["run_handoffs"][0]["artifact_metadata"]["discrepancy_count"], 2)
        self.assertEqual(payload["run_handoffs"][0]["artifact_metadata"]["duplicate_file_skip_count"], 3)
        self.assertEqual(payload["recovery_packets"][0]["artifact_type"], "recovery_packet")
        self.assertEqual(payload["retention_summaries"][0]["artifact_type"], "retention_summary")


if __name__ == "__main__":
    unittest.main()
