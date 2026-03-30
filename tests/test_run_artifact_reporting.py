from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project.storage import create_run_artifact_layout
from project.workflows.run_artifact_reporting import summarize_run_artifacts


class RunArtifactReportingTests(unittest.TestCase):
    def test_summarize_run_artifacts_reports_file_presence_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="export_lc_sc",
                run_id="run-123",
            )
            paths.run_metadata_path.write_text('{"run_id":"run-123"}\n', encoding="utf-8")
            paths.mail_outcomes_path.write_text('{"mail_id":"1"}\n{"mail_id":"2"}\n', encoding="utf-8")
            paths.manual_document_verification_path.write_text('{"document_count":1}\n', encoding="utf-8")
            paths.target_probes_path.write_text('{"probe":1}\n', encoding="utf-8")
            paths.commit_marker_path.write_text('{"committed":true}\n', encoding="utf-8")
            paths.backup_workbook_path.write_bytes(b"fake workbook bytes")
            paths.backup_hash_path.write_text("abcd\n", encoding="utf-8")
            (paths.document_audits_dir / "doc-1.layered.json").write_text("{}", encoding="utf-8")
            (paths.print_markers_dir / "group-1.json").write_text("{}", encoding="utf-8")
            (paths.mail_move_markers_dir / "move-1.json").write_text("{}", encoding="utf-8")
            (paths.logs_dir / "run.log").write_text("hello", encoding="utf-8")

            payload = summarize_run_artifacts(artifact_paths=paths)

        self.assertTrue(payload["core_files"]["run_metadata"]["exists"])
        self.assertEqual(payload["core_files"]["mail_outcomes"]["record_count"], 2)
        self.assertTrue(payload["core_files"]["commit_marker"]["nonempty"])
        self.assertTrue(payload["backup_artifacts"]["backup_workbook"]["exists"])
        self.assertEqual(payload["directories"]["document_audits"]["json_file_count"], 1)
        self.assertEqual(payload["directories"]["print_markers"]["file_count"], 1)
        self.assertEqual(payload["directories"]["mail_move_markers"]["json_file_count"], 1)
        self.assertEqual(payload["directories"]["logs"]["file_count"], 1)


if __name__ == "__main__":
    unittest.main()
