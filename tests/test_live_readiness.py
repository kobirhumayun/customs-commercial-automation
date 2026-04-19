from __future__ import annotations

import unittest

from project.models import WorkflowId
from project.workflows.live_readiness import (
    build_live_environment_readiness,
    build_print_readiness_section,
    build_workbook_readiness_section,
)


class LiveReadinessTests(unittest.TestCase):
    def test_build_live_environment_readiness_counts_ready_issue_and_not_applicable(self) -> None:
        payload = build_live_environment_readiness(
            workflow_id=WorkflowId.EXPORT_LC_SC,
            snapshot_section={"status": "ready"},
            erp_section={"status": "issue", "error": "ERP unavailable"},
            workbook_section={"status": "ready"},
            print_section=None,
        )

        self.assertEqual(payload["workflow_id"], "export_lc_sc")
        self.assertEqual(payload["overall_status"], "attention_required")
        self.assertEqual(payload["applicable_section_count"], 3)
        self.assertEqual(payload["ready_section_count"], 2)
        self.assertEqual(payload["issue_section_count"], 1)
        self.assertEqual(payload["sections"]["print"]["status"], "not_applicable")

    def test_build_workbook_readiness_section_marks_non_ready_preflight_as_issue(self) -> None:
        payload = build_workbook_readiness_section(
            {
                "workbook_available": True,
                "sheet_name": "2026",
                "header_mapping_status": "resolved",
                "row_count": 12,
                "session_preflight": {"status": "contention_detected"},
            }
        )

        self.assertEqual(payload["status"], "issue")
        self.assertEqual(payload["session_preflight_status"], "contention_detected")

    def test_build_print_readiness_section_respects_disabled_config(self) -> None:
        payload = build_print_readiness_section(
            {
                "available": True,
                "resolved_executable_path": "C:\\Acrobat.exe",
                "printer_name": "Office Printer",
                "blank_separator_exists": True,
            },
            print_enabled=False,
        )

        self.assertEqual(payload["status"], "issue")
        self.assertEqual(payload["print_enabled"], False)
        self.assertIn("print_enabled=false", payload["error"])


if __name__ == "__main__":
    unittest.main()
