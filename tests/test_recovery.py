from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from project.config import load_workflow_config
from project.erp import JsonManifestERPRowProvider
from project.reporting.persistence import write_run_metadata, write_staged_write_plan
from project.rules import load_rule_pack
from project.utils.json import to_jsonable
from project.workbook import JsonManifestWorkbookSnapshotProvider
from project.workflows.bootstrap import initialize_workflow_run
from project.workflows.recovery import assess_recovery
from project.workflows.registry import get_workflow_descriptor
from project.workflows.snapshot import build_email_snapshot, load_snapshot_manifest
from project.workflows.validation import validate_run_snapshot
from project.models import WorkflowId, WritePhaseStatus


class RecoveryTests(unittest.TestCase):
    def test_assess_recovery_returns_safe_reapply_for_uncertain_prewrite_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            initialized, validation_result, workbook_snapshot, workbook_path = self._build_run_with_staged_plan(root)
            uncertain_report = replace(
                validation_result.run_report,
                write_phase_status=WritePhaseStatus.UNCERTAIN_NOT_COMMITTED,
            )
            write_run_metadata(initialized.artifact_paths, to_jsonable(uncertain_report))
            write_staged_write_plan(initialized.artifact_paths, to_jsonable(validation_result.staged_write_plan))

            assessment = assess_recovery(
                workflow_id=WorkflowId.EXPORT_LC_SC,
                run_artifact_root=initialized.config.run_artifact_root,
                backup_root=initialized.config.backup_root,
                run_id=initialized.run_report.run_id,
                workbook_snapshot=workbook_snapshot,
                current_workbook_path=Path(initialized.master_workbook_path),
            )

        self.assertEqual(assessment.outcome, "safe_reapply_staged_writes")
        self.assertEqual(len(assessment.discrepancies), 0)
        self.assertTrue(all(probe.classification == "matches_pre_write" for probe in assessment.target_probes))

    def test_assess_recovery_returns_safe_resume_for_committed_postwrite_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            initialized, validation_result, _workbook_snapshot, _workbook_path = self._build_run_with_staged_plan(root)
            committed_report = replace(
                validation_result.run_report,
                write_phase_status=WritePhaseStatus.COMMITTED,
            )
            write_run_metadata(initialized.artifact_paths, to_jsonable(committed_report))
            write_staged_write_plan(initialized.artifact_paths, to_jsonable(validation_result.staged_write_plan))
            postwrite_snapshot = JsonManifestWorkbookSnapshotProvider(
                self._write_postwrite_manifest(root, validation_result.staged_write_plan)
            ).load_snapshot()

            assessment = assess_recovery(
                workflow_id=WorkflowId.EXPORT_LC_SC,
                run_artifact_root=initialized.config.run_artifact_root,
                backup_root=initialized.config.backup_root,
                run_id=initialized.run_report.run_id,
                workbook_snapshot=postwrite_snapshot,
                current_workbook_path=Path(initialized.master_workbook_path),
            )

        self.assertEqual(assessment.outcome, "safe_resume")
        self.assertEqual(len(assessment.discrepancies), 0)
        self.assertTrue(all(probe.classification == "matches_post_write" for probe in assessment.target_probes))

    def test_assess_recovery_hard_blocks_metadata_probe_contradiction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            initialized, validation_result, workbook_snapshot, _workbook_path = self._build_run_with_staged_plan(root)
            committed_report = replace(
                validation_result.run_report,
                write_phase_status=WritePhaseStatus.COMMITTED,
            )
            write_run_metadata(initialized.artifact_paths, to_jsonable(committed_report))
            write_staged_write_plan(initialized.artifact_paths, to_jsonable(validation_result.staged_write_plan))

            assessment = assess_recovery(
                workflow_id=WorkflowId.EXPORT_LC_SC,
                run_artifact_root=initialized.config.run_artifact_root,
                backup_root=initialized.config.backup_root,
                run_id=initialized.run_report.run_id,
                workbook_snapshot=workbook_snapshot,
                current_workbook_path=Path(initialized.master_workbook_path),
            )

        self.assertEqual(assessment.outcome, "hard_block")
        self.assertEqual(assessment.discrepancies[0].code, "metadata_probe_contradiction")

    def _build_run_with_staged_plan(self, root: Path):
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
                        "ship_remarks": "BB-REF-2026-0042",
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
                        {"column_index": 33, "text": "Bangladesh Bank Ref."},
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
        return initialized, validation_result, workbook_snapshot, workbook_path

    def _write_postwrite_manifest(self, root: Path, staged_write_plan) -> Path:
        rows_by_index: dict[int, dict[str, str]] = {}
        column_index_by_key = {
            "file_no": 1,
            "lc_sc_no": 2,
            "buyer_name": 3,
            "lc_issuing_bank": 4,
            "lc_issue_date": 5,
            "export_amount": 6,
            "shipment_date": 7,
            "expiry_date": 8,
            "quantity_fabrics": 9,
            "lc_amnd_no": 10,
            "lc_amnd_date": 11,
            "lien_bank": 12,
            "master_lc_no": 13,
            "master_lc_issue_date": 14,
            "bangladesh_bank_ref": 33,
        }
        for operation in staged_write_plan:
            row_values = rows_by_index.setdefault(operation.row_index, {})
            row_values[str(column_index_by_key[operation.column_key])] = str(
                operation.expected_post_write_value or ""
            )
        manifest_path = root / "postwrite_workbook.json"
        manifest_path.write_text(
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
                        {"column_index": 33, "text": "Bangladesh Bank Ref."},
                    ],
                    "rows": [
                        {"row_index": row_index, "values": values}
                        for row_index, values in rows_by_index.items()
                    ],
                }
            ),
            encoding="utf-8",
        )
        return manifest_path


if __name__ == "__main__":
    unittest.main()

