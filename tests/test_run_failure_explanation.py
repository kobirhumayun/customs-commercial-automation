from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project.models import (
    FinalDecision,
    MailMovePhaseStatus,
    MailOutcomeRecord,
    MailProcessingStatus,
    PrintPhaseStatus,
    RunReport,
    WorkflowId,
    WritePhaseStatus,
)
from project.storage import create_run_artifact_layout
from project.workflows.run_failure_explanation import build_run_failure_explanation


class RunFailureExplanationTests(unittest.TestCase):
    def test_ud_nonblank_target_cells_are_explained_as_workbook_prevalidation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_paths = create_run_artifact_layout(
                run_artifact_root=root / "runs",
                backup_root=root / "backups",
                workflow_id="ud_ip_exp",
                run_id="run-ud",
            )
            artifact_paths.discrepancies_path.write_text(
                json.dumps(
                    {
                        "run_id": "run-ud",
                        "workflow_id": "ud_ip_exp",
                        "severity": "hard_block",
                        "code": "ud_shared_column_nonblank_policy_unresolved",
                        "message": "Selected UD target row has a non-blank target cell.",
                        "mail_id": "mail-1",
                        "details": {
                            "selected_candidate_id": "5",
                            "target_column_keys": ["ud_ip_shared", "ud_ip_date", "ud_recv_date"],
                            "target_rows": [
                                {
                                    "row_index": 5,
                                    "column_key": "ud_ip_date",
                                    "observed_value": "2026-04-21T00:00:00",
                                },
                                {
                                    "row_index": 5,
                                    "column_key": "ud_recv_date",
                                    "observed_value": "2026-04-27T00:00:00",
                                },
                            ],
                        },
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "run_id": "run-ud",
                        "workflow_id": "ud_ip_exp",
                        "severity": "hard_block",
                        "code": "mail_move_gate_unsatisfied",
                        "message": "Mail moves are blocked until prior run phases reach terminal success.",
                        "mail_id": None,
                        "details": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            artifact_paths.target_probes_path.write_text("", encoding="utf-8")
            run_report = RunReport(
                run_id="run-ud",
                workflow_id=WorkflowId.UD_IP_EXP,
                tool_version="0.1.0",
                rule_pack_id="ud_ip_exp.default",
                rule_pack_version="1.0.0",
                started_at_utc="2026-04-28T00:00:00Z",
                completed_at_utc=None,
                state_timezone="Asia/Dhaka",
                mail_iteration_order=["mail-1"],
                print_group_order=[],
                write_phase_status=WritePhaseStatus.NOT_STARTED,
                print_phase_status=PrintPhaseStatus.NOT_STARTED,
                mail_move_phase_status=MailMovePhaseStatus.NOT_STARTED,
                hash_algorithm="sha256",
                run_start_backup_hash="a" * 64,
                current_workbook_hash="b" * 64,
                staged_write_plan_hash="c" * 64,
                summary={"pass": 0, "warning": 0, "hard_block": 1},
            )
            outcome = MailOutcomeRecord(
                run_id="run-ud",
                mail_id="mail-1",
                workflow_id=WorkflowId.UD_IP_EXP,
                snapshot_index=0,
                processing_status=MailProcessingStatus.BLOCKED,
                final_decision=FinalDecision.HARD_BLOCK,
                decision_reasons=[],
                eligible_for_write=False,
                eligible_for_print=False,
                eligible_for_mail_move=False,
                source_entry_id="entry-1",
                subject_raw="UD-LC-1227-RAM APPAREL LTD",
                sender_address="commercial2@example.com",
                file_numbers_extracted=["P/26/0652"],
                write_disposition="no_write_noop",
            )

            payload = build_run_failure_explanation(
                run_report=run_report,
                mail_outcomes=[outcome],
                staged_write_plan=[],
                artifact_paths=artifact_paths,
            )

        self.assertEqual(payload["overall_status"], "attention_required")
        self.assertEqual(payload["primary_cause_count"], 1)
        self.assertEqual(payload["related_cause_count"], 1)
        self.assertEqual(payload["related_causes"][0]["code"], "mail_move_gate_unsatisfied")
        cause = payload["primary_causes"][0]
        self.assertEqual(cause["category"], "workbook_prevalidation")
        self.assertEqual(cause["subject"], "UD-LC-1227-RAM APPAREL LTD")
        self.assertEqual(cause["file_numbers"], ["P/26/0652"])
        self.assertIn("target cells must be blank", cause["operator_summary"])
        self.assertEqual(
            [target["column_key"] for target in cause["workbook_targets"]],
            ["ud_ip_date", "ud_recv_date"],
        )
        self.assertEqual(cause["workbook_targets"][0]["required_pre_write_state"], "blank")
        self.assertIn("Clear the mistakenly retained", cause["operator_hint"])


if __name__ == "__main__":
    unittest.main()
