from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project.config import load_workflow_config
from project.erp import JsonManifestERPRowProvider
from project.models import (
    MailProcessingStatus,
    WorkbookSessionPreflight,
    WorkflowId,
    WritePhaseStatus,
)
from project.rules import load_rule_pack
from project.workbook import (
    JsonManifestWorkbookSnapshotProvider,
    WorkbookMutationOpenResult,
)
from project.workflows.bootstrap import initialize_workflow_run
from project.workflows.registry import get_workflow_descriptor
from project.workflows.snapshot import build_email_snapshot, load_snapshot_manifest
from project.workflows.validation import validate_run_snapshot
from project.workflows.write_execution import execute_live_write_batch


class WriteExecutionTests(unittest.TestCase):
    def test_execute_live_write_batch_commits_and_emits_commit_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            validation_result, initialized, workbook_snapshot, workbook_path = self._build_staged_validation_result(root)
            status_transitions: list[str] = []
            persisted_probe_counts: list[int] = []

            session = FakeWorkbookMutationSession(workbook_snapshot)

            class FakeProvider:
                def open_write_session(self, *, operator_context, max_attempts=3):
                    return WorkbookMutationOpenResult(
                        preflight=WorkbookSessionPreflight(
                            workbook_path=str(workbook_path),
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
                        session=session,
                    )

            executed = execute_live_write_batch(
                validation_result=validation_result,
                workbook_path=workbook_path,
                operator_context=initialized.run_report.operator_context,
                session_provider=FakeProvider(),
                run_report_persistor=lambda report: status_transitions.append(report.write_phase_status.value),
                target_probe_persistor=lambda probes: persisted_probe_counts.append(len(probes)),
            )

        self.assertEqual(
            status_transitions,
            ["prevalidating_targets", "prevalidated", "applying", "committed"],
        )
        self.assertEqual(persisted_probe_counts, [14, 28])
        self.assertEqual(executed.run_report.write_phase_status, WritePhaseStatus.COMMITTED)
        self.assertIsNotNone(executed.commit_marker)
        self.assertEqual(executed.commit_marker.operation_count, 14)
        self.assertEqual(
            [probe.probe_stage for probe in executed.target_probes[:2]],
            ["prevalidation", "prevalidation"],
        )
        self.assertTrue(
            all(
                probe.classification == "matches_post_write"
                for probe in executed.target_probes
                if probe.probe_stage == "post_write"
            )
        )
        self.assertEqual(executed.mail_outcomes[0].processing_status, MailProcessingStatus.WRITTEN)
        self.assertFalse(executed.mail_outcomes[0].eligible_for_write)

    def test_execute_live_write_batch_marks_uncertain_when_save_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            validation_result, initialized, workbook_snapshot, workbook_path = self._build_staged_validation_result(root)
            status_transitions: list[str] = []

            session = FakeWorkbookMutationSession(workbook_snapshot, save_error=RuntimeError("save conflict"))

            class FakeProvider:
                def open_write_session(self, *, operator_context, max_attempts=3):
                    return WorkbookMutationOpenResult(
                        preflight=WorkbookSessionPreflight(
                            workbook_path=str(workbook_path),
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
                        session=session,
                    )

            executed = execute_live_write_batch(
                validation_result=validation_result,
                workbook_path=workbook_path,
                operator_context=initialized.run_report.operator_context,
                session_provider=FakeProvider(),
                run_report_persistor=lambda report: status_transitions.append(report.write_phase_status.value),
            )

        self.assertEqual(
            status_transitions,
            ["prevalidating_targets", "prevalidated", "applying", "uncertain_not_committed"],
        )
        self.assertEqual(executed.run_report.write_phase_status, WritePhaseStatus.UNCERTAIN_NOT_COMMITTED)
        self.assertIsNone(executed.commit_marker)
        self.assertEqual(executed.discrepancy_reports[-1].code, "workbook_save_conflict")
        self.assertTrue(all(not outcome.eligible_for_print for outcome in executed.mail_outcomes))
        self.assertTrue(all(not outcome.eligible_for_mail_move for outcome in executed.mail_outcomes))

    def test_execute_live_write_batch_treats_excel_native_date_and_numeric_values_as_post_write_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            validation_result, initialized, workbook_snapshot, workbook_path = self._build_staged_validation_result(root)
            status_transitions: list[str] = []

            session = FakeWorkbookMutationSession(
                workbook_snapshot,
                post_write_overrides={
                    (5002, 5): "2026-01-10T00:00:00",
                    (5002, 6): "10000.0",
                    (5002, 7): "2026-02-01T00:00:00",
                    (5002, 8): "2026-03-01T00:00:00",
                    (5002, 9): "5000.0",
                    (5002, 11): "2026-01-15T00:00:00",
                    (5002, 14): "2025-12-20T00:00:00",
                },
            )

            class FakeProvider:
                def open_write_session(self, *, operator_context, max_attempts=3):
                    return WorkbookMutationOpenResult(
                        preflight=WorkbookSessionPreflight(
                            workbook_path=str(workbook_path),
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
                        session=session,
                    )

            executed = execute_live_write_batch(
                validation_result=validation_result,
                workbook_path=workbook_path,
                operator_context=initialized.run_report.operator_context,
                session_provider=FakeProvider(),
                run_report_persistor=lambda report: status_transitions.append(report.write_phase_status.value),
            )

        self.assertEqual(
            status_transitions,
            ["prevalidating_targets", "prevalidated", "applying", "committed"],
        )
        self.assertEqual(executed.run_report.write_phase_status, WritePhaseStatus.COMMITTED)
        self.assertTrue(
            all(
                probe.classification == "matches_post_write"
                for probe in executed.target_probes
                if probe.probe_stage == "post_write"
            )
        )

    def _build_staged_validation_result(self, root: Path):
        workflow_year = __import__("datetime").datetime.now().year
        report_root = root / "reports"
        run_root = root / "runs"
        backup_root = root / "backups"
        workbook_root = root / "workbooks"
        for directory in (report_root, run_root, backup_root, workbook_root):
            directory.mkdir(parents=True, exist_ok=True)

        workbook_path = workbook_root / f"{workflow_year}-master.xlsx"
        workbook_path.write_bytes(b"fake workbook")
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
        return validation_result, initialized, workbook_snapshot, workbook_path


class FakeWorkbookMutationSession:
    def __init__(
        self,
        workbook_snapshot,
        save_error: Exception | None = None,
        post_write_overrides: dict[tuple[int, int], str] | None = None,
    ) -> None:
        self._snapshot = workbook_snapshot
        self._cell_values = {
            (row.row_index, column_index): value
            for row in workbook_snapshot.rows
            for column_index, value in row.values.items()
        }
        self._save_error = save_error
        self._post_write_overrides = post_write_overrides or {}
        self.closed = False
        self.preflight = None

    def capture_snapshot(self):
        return self._snapshot

    def write_cell(self, *, sheet_name: str, row_index: int, column_index: int, value: object) -> None:
        self._cell_values[(row_index, column_index)] = "" if value is None else str(value)

    def read_cell(self, *, sheet_name: str, row_index: int, column_index: int) -> str | None:
        if (row_index, column_index) in self._post_write_overrides:
            return self._post_write_overrides[(row_index, column_index)]
        return self._cell_values.get((row_index, column_index), "")

    def save(self) -> None:
        if self._save_error is not None:
            raise self._save_error

    def close(self) -> None:
        self.closed = True


if __name__ == "__main__":
    unittest.main()

