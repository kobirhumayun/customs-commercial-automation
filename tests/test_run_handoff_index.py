from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project.models import WorkflowId
from project.workflows.run_handoff_index import list_run_handoffs


class RunHandoffIndexTests(unittest.TestCase):
    def test_list_run_handoffs_returns_recent_handoff_packets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_root = Path(temp_dir)
            handoff_root = report_root / "run_handoffs"
            handoff_root.mkdir(parents=True, exist_ok=True)
            (handoff_root / "export_lc_sc.run-123.handoff.json").write_text("{}", encoding="utf-8")
            (handoff_root / "export_lc_sc.run-456.handoff.json").write_text("{}", encoding="utf-8")

            payload = list_run_handoffs(
                report_root=report_root,
                workflow_id=WorkflowId.EXPORT_LC_SC,
                limit=1,
            )

        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["handoff_count"], 1)
        self.assertEqual(payload["total_handoff_count"], 2)
        self.assertEqual(payload["run_handoffs"][0]["artifact_type"], "run_handoff")


if __name__ == "__main__":
    unittest.main()
