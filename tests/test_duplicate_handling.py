from __future__ import annotations

import unittest

from project.workflows.duplicate_handling import (
    classify_write_disposition,
    summarize_duplicate_decision_reasons,
)


class DuplicateHandlingTests(unittest.TestCase):
    def test_summarize_duplicate_decision_reasons_counts_ud_duplicate_messages(self) -> None:
        summary = summarize_duplicate_decision_reasons(
            [
                "Ignored duplicate UD/AM document BGMEA/DHK/UD/2026/5483/003 within the same mail.",
                "Skipped UD shared-column write for BGMEA/DHK/UD/2026/5483/003 because the same document was already staged earlier in this run.",
                "Skipped UD shared-column write for BGMEA/DHK/UD/2026/5483/003 because it is already recorded in the workbook.",
            ]
        )

        self.assertEqual(summary["duplicate_file_skip_count"], 3)
        self.assertEqual(summary["duplicate_in_workbook_file_count"], 1)
        self.assertEqual(summary["duplicate_in_run_file_count"], 2)

    def test_classify_write_disposition_marks_ud_same_mail_duplicate_plus_write_as_mixed(self) -> None:
        disposition = classify_write_disposition(
            decision_reasons=[
                "Ignored duplicate UD/AM document BGMEA/DHK/UD/2026/5483/003 within the same mail.",
                "Staged UD shared-column write for BGMEA/DHK/UD/2026/5483/003 to rows [11].",
            ],
            staged_write_operations=[{"row_index": 11}],
        )

        self.assertEqual(disposition, "mixed_duplicate_and_new_writes")


if __name__ == "__main__":
    unittest.main()
