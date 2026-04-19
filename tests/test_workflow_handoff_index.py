from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project.models import WorkflowId
from project.workflows.workflow_handoff_index import list_workflow_handoffs


class WorkflowHandoffIndexTests(unittest.TestCase):
    def test_list_workflow_handoffs_returns_indexed_packets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_root = Path(temp_dir)
            handoff_root = report_root / "workflow_handoffs"
            handoff_root.mkdir(parents=True, exist_ok=True)
            (handoff_root / "export_lc_sc.handoff.json").write_text("{}", encoding="utf-8")

            payload = list_workflow_handoffs(
                report_root=report_root,
                workflow_id=WorkflowId.EXPORT_LC_SC,
            )

        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["handoff_count"], 1)
        self.assertEqual(payload["workflow_handoffs"][0]["artifact_type"], "workflow_handoff")


if __name__ == "__main__":
    unittest.main()
