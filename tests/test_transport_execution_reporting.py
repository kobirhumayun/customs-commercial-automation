from __future__ import annotations

import unittest

from project.workflows.transport_execution_reporting import build_transport_execution_report


class TransportExecutionReportingTests(unittest.TestCase):
    def test_build_transport_execution_report_aggregates_marker_counts_and_adapters(self) -> None:
        payload = build_transport_execution_report(
            print_marker_summary={
                "marker_count": 1,
                "markers": [
                    {
                        "adapter_name": "acrobat",
                        "manual_verification_summary": {"verified_count": 1},
                    }
                ],
            },
            mail_move_marker_summary={
                "marker_count": 2,
                "markers": [
                    {
                        "adapter_name": "win32com_outlook",
                        "manual_verification_summary": {"verified_count": 1},
                        "write_disposition": "duplicate_only_noop",
                    },
                    {
                        "adapter_name": "win32com_outlook",
                        "manual_verification_summary": {"verified_count": 0},
                    },
                ],
            },
        )

        self.assertEqual(payload["summary_counts"]["print_marker_count"], 1)
        self.assertEqual(payload["summary_counts"]["mail_move_marker_count"], 2)
        self.assertEqual(payload["summary_counts"]["print_adapter_count"], 1)
        self.assertEqual(payload["summary_counts"]["mail_move_adapter_count"], 1)
        self.assertEqual(payload["summary_counts"]["manual_verification_visible_count"], 3)
        self.assertEqual(payload["summary_counts"]["duplicate_only_mail_move_count"], 1)
        self.assertEqual(payload["adapter_summary"]["print_adapters"], ["acrobat"])
        self.assertEqual(payload["adapter_summary"]["mail_move_adapters"], ["win32com_outlook"])


if __name__ == "__main__":
    unittest.main()
