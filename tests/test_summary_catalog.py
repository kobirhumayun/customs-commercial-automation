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
            (root / "run_summaries").mkdir(parents=True, exist_ok=True)
            (root / "recovery_packets").mkdir(parents=True, exist_ok=True)
            (root / "retention_reports").mkdir(parents=True, exist_ok=True)

            (root / "workflow_summaries" / "export_lc_sc.summary.json").write_text("{}", encoding="utf-8")
            (root / "run_summaries" / "export_lc_sc.run-123.summary.json").write_text("{}", encoding="utf-8")
            (root / "recovery_packets" / "export_lc_sc.recovery.json").write_text("{}", encoding="utf-8")
            (root / "retention_reports" / "export_lc_sc.retention.json").write_text("{}", encoding="utf-8")

            payload = build_summary_catalog(
                report_root=root,
                workflow_id=WorkflowId.EXPORT_LC_SC,
            )

        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["summary_counts"]["total_summary_count"], 4)
        self.assertEqual(payload["summary_counts"]["run_summary_count"], 1)
        self.assertEqual(payload["run_summaries"][0]["run_id"], "run-123")
        self.assertEqual(payload["recovery_packets"][0]["artifact_type"], "recovery_packet")
        self.assertEqual(payload["retention_summaries"][0]["artifact_type"], "retention_summary")


if __name__ == "__main__":
    unittest.main()
