from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project.config import load_workflow_config
from project.erp import JsonManifestERPRowProvider
from project.models import WorkbookSessionPreflight, WorkflowId, WritePhaseStatus
from project.rules import load_rule_pack
from project.workbook import JsonManifestWorkbookSnapshotProvider, WorkbookWriteSessionResult
from project.workflows.bootstrap import initialize_workflow_run
from project.workflows.registry import get_workflow_descriptor
from project.workflows.snapshot import build_email_snapshot, load_snapshot_manifest
from project.workflows.validation import validate_run_snapshot
from project.workflows.write_preparation import prepare_live_write_batch


class WritePreparationTests(unittest.TestCase):
    def test_prepare_live_write_batch_marks_run_prevalidated_when_targets_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            validation_result, initialized, workbook_snapshot = self._build_staged_validation_result(root)

            class FakeSessionProvider:
                def open_preflight_session(self, *, operator_context, max_attempts=3):
                    return WorkbookWriteSessionResult(
                        preflight=WorkbookSessionPreflight(
                            workbook_path=str(root / "workbooks" / "fake.xlsx"),
                            adapter_name="fake-xlwings",
                            status="ready",
                            attempt_count=1,
                            host_name=operator_context.host_name if operator_context else "host",
                            process_id=operator_context.process_id if operator_context else 1,
                            session_id="excel-session-001",
                            opened_at_utc="2026-03-28T00:00:00Z",
                            read_only=False,
                            save_capable=True,
                        ),
                        snapshot=workbook_snapshot,
                    )

            prepared = prepare_live_write_batch(
                validation_result=validation_result,
                workbook_path=Path("C:/fake.xlsx"),
                operator_context=initialized.run_report.operator_context,
                session_provider=FakeSessionProvider(),
            )

        self.assertEqual(prepared.run_report.write_phase_status, WritePhaseStatus.PREVALIDATED)
        self.assertEqual(prepared.run_report.workbook_session_preflight.status, "ready")
        self.assertEqual(prepared.run_report.target_prevalidation_summary.status, "passed")
        self.assertEqual(len(prepared.target_probes), 14)
        self.assertTrue(all(probe.classification == "matches_pre_write" for probe in prepared.target_probes))

    def test_prepare_live_write_batch_hard_blocks_on_lock_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            validation_result, initialized, _workbook_snapshot = self._build_staged_validation_result(root)

            class FakeSessionProvider:
                def open_preflight_session(self, *, operator_context, max_attempts=3):
                    return WorkbookWriteSessionResult(
                        preflight=WorkbookSessionPreflight(
                            workbook_path=str(root / "workbooks" / "fake.xlsx"),
                            adapter_name="fake-xlwings",
                            status="lock_conflict",
                            attempt_count=1,
                            host_name=operator_context.host_name if operator_context else "host",
                            process_id=operator_context.process_id if operator_context else 1,
                            read_only=None,
                            save_capable=False,
                        ),
                        snapshot=None,
                        discrepancy_code="workbook_lock_conflict",
                        discrepancy_message="Workbook lock or conflicting open session prevented write-intent preflight.",
                        discrepancy_details={"workbook_path": str(root / "workbooks" / "fake.xlsx")},
                    )

            prepared = prepare_live_write_batch(
                validation_result=validation_result,
                workbook_path=Path("C:/fake.xlsx"),
                operator_context=initialized.run_report.operator_context,
                session_provider=FakeSessionProvider(),
            )

        self.assertEqual(prepared.run_report.write_phase_status, WritePhaseStatus.HARD_BLOCKED_NO_WRITE)
        self.assertEqual(prepared.target_probes, [])
        self.assertEqual(prepared.discrepancy_reports[-1].code, "workbook_lock_conflict")
        self.assertTrue(all(not outcome.eligible_for_write for outcome in prepared.mail_outcomes))
        self.assertTrue(all(not outcome.eligible_for_print for outcome in prepared.mail_outcomes))
        self.assertTrue(all(not outcome.eligible_for_mail_move for outcome in prepared.mail_outcomes))

    def _build_staged_validation_result(self, root: Path):
        workflow_year = __import__("datetime").datetime.now().year
        report_root = root / "reports"
        run_root = root / "runs"
        backup_root = root / "backups"
        workbook_root = root / "workbooks"
        for directory in (report_root, run_root, backup_root, workbook_root):
            directory.mkdir(parents=True, exist_ok=True)

        (workbook_root / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
        config_path = root / "config.toml"
        config_path.write_text(
            "\n".join(
                [
                    'state_timezone = "Asia/Dhaka"',
                    f'report_root = "{report_root.as_posix()}"',
                    f'run_artifact_root = "{run_root.as_posix()}"',
                    f'backup_root = "{backup_root.as_posix()}"',
                    'outlook_profile = "outlook"',
                    f'master_workbook_root = "{workbook_root.as_posix()}"',
                    'erp_base_url = "https://erp.local"',
                    'playwright_browser_channel = "msedge"',
                    f'master_workbook_path_template = "{(workbook_root / "{year}-master.xlsx").as_posix()}"',
                    "excel_lock_timeout_seconds = 60",
                    "print_enabled = true",
                    'source_working_folder_entry_id = "src-folder"',
                    'destination_success_entry_id = "dst-folder"',
                ]
            ),
            encoding="utf-8",
        )
        snapshot_manifest_path = root / "snapshot.json"
        snapshot_manifest_path.write_text(
            json.dumps(
                [
                    {
                        "entry_id": "entry-001",
                        "received_time": "2026-03-28T03:00:00Z",
                        "subject_raw": "LC-0038-ANANTA GARMENTS LTD_AMD_05",
                        "sender_address": "one@example.com",
                        "body_text": "Please process file P/26/42 today.",
                    }
                ]
            ),
            encoding="utf-8",
        )
        erp_manifest_path = root / "erp.json"
        erp_manifest_path.write_text(
            json.dumps(
                [
                    {
                        "file_number": "P/26/0042",
                        "lc_sc_number": "LC-0038",
                        "buyer_name": "ANANTA GARMENTS LTD",
                        "lc_sc_date": "2026-01-10",
                        "source_row_index": 5,
                        "notify_bank": "ABC BANK",
                        "current_lc_value": "10000",
                        "ship_date": "2026-02-01",
                        "expiry_date": "2026-03-01",
                        "lc_qty": "5000",
                        "lc_unit": "YDS",
                        "amd_no": "05",
                        "amd_date": "2026-01-15",
                        "nego_bank": "XYZ BANK",
                        "master_lc_no": "MLC-001",
                        "master_lc_date": "2025-12-20",
                    }
                ]
            ),
            encoding="utf-8",
        )
        workbook_manifest_path = root / "workbook.json"
        workbook_manifest_path.write_text(
            json.dumps(
                {
                    "sheet_name": "Sheet1",
                    "headers": [
                        {"column_index": 1, "text": "File No."},
                        {"column_index": 2, "text": "L/C No."},
                        {"column_index": 3, "text": "Buyer Name"},
                        {"column_index": 4, "text": "L/C Issuing Bank"},
                        {"column_index": 5, "text": "LC Issue Date"},
                        {"column_index": 6, "text": "Amount"},
                        {"column_index": 7, "text": "Shipment Date"},
                        {"column_index": 8, "text": "Expiry Date"},
                        {"column_index": 9, "text": "Quantity of Fabrics (Yds/Mtr)"},
                        {"column_index": 10, "text": "L/C Amnd No."},
                        {"column_index": 11, "text": "L/C Amnd Date"},
                        {"column_index": 12, "text": "Lien Bank"},
                        {"column_index": 13, "text": "Master L/C No."},
                        {"column_index": 14, "text": "Master L/C Issue Dt."},
                        {"column_index": 22, "text": "Amount"},
                    ],
                    "rows": [],
                }
            ),
            encoding="utf-8",
        )

        descriptor = get_workflow_descriptor(WorkflowId.EXPORT_LC_SC)
        config = load_workflow_config(descriptor=descriptor, config_path=config_path)
        rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)
        snapshot = build_email_snapshot(
            load_snapshot_manifest(snapshot_manifest_path),
            state_timezone="Asia/Dhaka",
        )
        initialized = initialize_workflow_run(
            descriptor=descriptor,
            config=config,
            rule_pack=rule_pack,
            mail_snapshot=snapshot,
        )
        workbook_snapshot = JsonManifestWorkbookSnapshotProvider(workbook_manifest_path).load_snapshot()
        validation_result = validate_run_snapshot(
            descriptor=descriptor,
            run_report=initialized.run_report,
            rule_pack=rule_pack,
            erp_row_provider=JsonManifestERPRowProvider(erp_manifest_path),
            workbook_snapshot=workbook_snapshot,
        )
        return validation_result, initialized, workbook_snapshot


if __name__ == "__main__":
    unittest.main()

