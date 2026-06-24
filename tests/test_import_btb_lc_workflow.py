from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from project.cli import main
from project.workflows.snapshot import SourceAttachmentRecord, SourceEmailRecord, build_email_snapshot
from project.workbook import WorkbookHeader, WorkbookRow, WorkbookSnapshot
from project.workflows.import_btb_lc.extraction import ExtractedPage
from project.workflows.import_btb_lc.workflow import (
    DirectoryAttachmentContentProvider,
    _build_current_full_mail_outcomes,
    _document_from_artifact,
    allocate_import_btb_lc_documents,
    evaluate_import_mail_relevance,
    load_import_relevance_keywords,
    render_import_btb_lc_html_report,
    run_import_btb_lc_current_full,
    resolve_import_btb_lc_header_mapping,
)


class ImportBTBLCWorkflowTests(unittest.TestCase):
    def test_header_mapping_resolves_import_columns_without_shared_mapping(self) -> None:
        mapping = resolve_import_btb_lc_header_mapping(_workbook_snapshot())

        self.assertIsNotNone(mapping)
        assert mapping is not None
        self.assertEqual(mapping.lc_sc_no, 4)
        self.assertEqual(mapping.sl_no, 1)
        self.assertEqual(mapping.up_no, 7)
        self.assertEqual(mapping.export_amount, 6)
        self.assertEqual(mapping.btb_lc_no, 21)
        self.assertEqual(mapping.btb_lc_issue_date, 20)
        self.assertEqual(mapping.import_amount, 22)
        self.assertEqual(mapping.quantity_kgs, 23)

    def test_allocation_selects_highest_eligible_percentage_row(self) -> None:
        snapshot = _workbook_snapshot(
            rows=[
                _row(3, lc="LC-1883260400042", export_amount="50"),
                _row(4, lc="1883260400042", export_amount="40"),
                _row(5, lc="LC-1883260400042", export_amount="60"),
                _row(6, lc="LC-1883260400042", export_amount="35"),
                _row(7, lc="LC-1883260400042", export_amount="70"),
            ]
        )
        document = _document(_artifact(value="30"))

        result = allocate_import_btb_lc_documents(
            documents=[document],
            workbook_snapshot=snapshot,
            run_id="run-import-test",
            pi_register_provider=_StaticPIRegisterProvider(total_amount="30", quantity_kg="1709"),
        )
        outcome = result.workflow_report["document_outcomes"][0]

        self.assertEqual(outcome["decision"], "pass")
        self.assertEqual(outcome["selected_row_index"], 4)
        self.assertEqual(outcome["selected_sl_no"], "2")
        self.assertEqual(len(result.staged_write_plan), 4)
        self.assertEqual(
            [operation.column_key for operation in result.staged_write_plan],
            ["btb_lc_no", "btb_lc_issue_date", "import_amount", "quantity_kgs"],
        )
        self.assertEqual(outcome["selected_quantity_kgs"], "1709")

    def test_allocation_hard_blocks_when_no_qualified_row(self) -> None:
        snapshot = _workbook_snapshot(
            rows=[_row(3, lc="LC-9999999999999", export_amount="100000")]
        )
        document = _document(_artifact(value="50000"))

        result = allocate_import_btb_lc_documents(
            documents=[document],
            workbook_snapshot=snapshot,
            run_id="run-import-test",
            pi_register_provider=_StaticPIRegisterProvider(),
        )
        outcome = result.workflow_report["document_outcomes"][0]

        self.assertEqual(outcome["decision"], "hard_block")
        self.assertEqual(
            outcome["hard_block_discrepancies"][0]["code"],
            "import_no_qualified_workbook_row",
        )
        self.assertEqual(result.staged_write_plan, [])

    def test_workbook_related_export_lc_filter_removes_spaces_and_leading_zeroes(self) -> None:
        snapshot = _workbook_snapshot(
            rows=[
                _row(3, lc="DC BD1184074", export_amount="100000"),
                _row(4, lc="0001883260400042", export_amount="100000"),
            ]
        )
        documents = [
            _document(
                _artifact(
                    path="C:/source/0742260401049.pdf",
                    related="LC-DCBD1184074",
                ),
                snapshot_index=0,
            ),
            _document(
                _artifact(
                    path="C:/source/0742260401050.pdf",
                    btb="0742260401050",
                    related="LC-1883260400042",
                ),
                snapshot_index=1,
            ),
        ]

        result = allocate_import_btb_lc_documents(
            documents=documents,
            workbook_snapshot=snapshot,
            run_id="run-import-normalization-test",
            pi_register_provider=_StaticPIRegisterProvider(),
        )

        self.assertEqual(
            [outcome["decision"] for outcome in result.workflow_report["document_outcomes"]],
            ["pass", "pass"],
        )
        self.assertEqual(
            [outcome["selected_row_index"] for outcome in result.workflow_report["document_outcomes"]],
            [3, 4],
        )
        self.assertEqual(len(result.staged_write_plan), 8)

    def test_allocation_hard_blocks_when_pi_register_total_mismatches_btb_value(self) -> None:
        document = _document(_artifact(value="50000"))

        result = allocate_import_btb_lc_documents(
            documents=[document],
            workbook_snapshot=_workbook_snapshot(),
            run_id="run-import-test",
            pi_register_provider=_StaticPIRegisterProvider(total_amount="49999.99"),
        )
        outcome = result.workflow_report["document_outcomes"][0]

        self.assertEqual(outcome["decision"], "hard_block")
        self.assertEqual(
            outcome["hard_block_discrepancies"][0]["code"],
            "import_pi_register_amount_mismatch",
        )
        self.assertEqual(result.staged_write_plan, [])

    def test_workbook_duplicate_exact_match_is_warning_noop(self) -> None:
        snapshot = _workbook_snapshot(
            rows=[
                _row(
                    3,
                    lc="LC-1883260400042",
                    export_amount="100000",
                    btb="0742260401049",
                    issue_date="2026-05-05",
                    import_amount="50000.00",
                )
            ]
        )
        document = _document(_artifact(value="50000"))

        result = allocate_import_btb_lc_documents(
            documents=[document],
            workbook_snapshot=snapshot,
            run_id="run-import-test",
            pi_register_provider=_StaticPIRegisterProvider(),
        )
        outcome = result.workflow_report["document_outcomes"][0]

        self.assertEqual(outcome["decision"], "warning")
        self.assertEqual(outcome["write_disposition"], "duplicate_only_noop")
        self.assertEqual(outcome["warnings"][0]["code"], "import_duplicate_document_in_workbook")
        self.assertEqual(result.staged_write_plan, [])

    def test_workbook_duplicate_conflict_is_hard_block(self) -> None:
        snapshot = _workbook_snapshot(
            rows=[
                _row(
                    3,
                    lc="LC-1883260400042",
                    export_amount="100000",
                    btb="0742260401049",
                    import_amount="49000",
                )
            ]
        )
        document = _document(_artifact(value="50000"))

        result = allocate_import_btb_lc_documents(
            documents=[document],
            workbook_snapshot=snapshot,
            run_id="run-import-test",
            pi_register_provider=_StaticPIRegisterProvider(),
        )
        outcome = result.workflow_report["document_outcomes"][0]

        self.assertEqual(outcome["decision"], "hard_block")
        self.assertEqual(
            outcome["hard_block_discrepancies"][0]["code"],
            "import_workbook_duplicate_unverifiable",
        )

    def test_same_run_duplicate_exact_match_is_warning_noop(self) -> None:
        snapshot = _workbook_snapshot(
            rows=[
                _row(3, lc="LC-1883260400042", export_amount="100000"),
                _row(4, lc="LC-1883260400042", export_amount="100000"),
            ]
        )
        documents = [
            _document(_artifact(path="C:/source/one.pdf"), snapshot_index=0),
            _document(_artifact(path="C:/source/two.pdf"), snapshot_index=1),
        ]

        result = allocate_import_btb_lc_documents(
            documents=documents,
            workbook_snapshot=snapshot,
            run_id="run-import-test",
            pi_register_provider=_StaticPIRegisterProvider(),
        )
        outcomes = result.workflow_report["document_outcomes"]

        self.assertEqual(outcomes[0]["decision"], "pass")
        self.assertEqual(outcomes[1]["decision"], "warning")
        self.assertEqual(outcomes[1]["warnings"][0]["code"], "import_duplicate_document_same_run")
        self.assertEqual(len(result.staged_write_plan), 4)

    def test_same_run_duplicate_conflict_is_hard_block(self) -> None:
        snapshot = _workbook_snapshot(
            rows=[
                _row(3, lc="LC-1883260400042", export_amount="100000"),
                _row(4, lc="LC-1883260400042", export_amount="100000"),
            ]
        )
        documents = [
            _document(_artifact(path="C:/source/one.pdf"), snapshot_index=0),
            _document(
                _artifact(path="C:/source/two.pdf", pi_numbers=["BTL/26/9999"]),
                snapshot_index=1,
            ),
        ]

        result = allocate_import_btb_lc_documents(
            documents=documents,
            workbook_snapshot=snapshot,
            run_id="run-import-test",
            pi_register_provider=_StaticPIRegisterProvider(),
        )
        outcomes = result.workflow_report["document_outcomes"]

        self.assertEqual(outcomes[1]["decision"], "hard_block")
        self.assertEqual(
            outcomes[1]["hard_block_discrepancies"][0]["code"],
            "import_duplicate_document_conflict",
        )

    def test_partial_target_state_hard_blocks_matching_family(self) -> None:
        snapshot = _workbook_snapshot(
            rows=[
                _row(
                    3,
                    lc="LC-1883260400042",
                    export_amount="100000",
                    btb="0742260400000",
                    import_amount="",
                )
            ]
        )
        document = _document(_artifact(value="50000"))

        result = allocate_import_btb_lc_documents(
            documents=[document],
            workbook_snapshot=snapshot,
            run_id="run-import-test",
            pi_register_provider=_StaticPIRegisterProvider(),
        )
        outcome = result.workflow_report["document_outcomes"][0]

        self.assertEqual(outcome["decision"], "hard_block")
        self.assertEqual(
            outcome["hard_block_discrepancies"][0]["code"],
            "import_workbook_candidate_invalid",
        )

    def test_cli_file_picker_workflow_reads_json_artifacts_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            artifact_path = input_dir / "0742260401049.pdf.import-btb-lc.json"
            artifact_path.write_text(json.dumps(_artifact(path=str(input_dir / "0742260401049.pdf"))), encoding="utf-8")
            workbook_path = root / "workbook.json"
            workbook_path.write_text(json.dumps(_workbook_manifest()), encoding="utf-8")
            pi_report_path = _pi_report_path(root)

            stdout = io.StringIO()
            with patch("project.cli.open_import_btb_lc_report_in_browser") as open_mock:
                with redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "run-import-btb-lc-file-picker",
                            "--input",
                            str(input_dir),
                            "--output",
                            str(output_dir),
                            "--workbook-json",
                            str(workbook_path),
                            "--erp-pi-report",
                            str(pi_report_path),
                            "--run-id",
                            "run-import-test",
                        ]
                    )
                open_mock.assert_called_once()
                opened_path = Path(open_mock.call_args.kwargs["html_path"])
            summary = json.loads(stdout.getvalue())
            report = json.loads(
                (output_dir / "run-import-test.import-btb-lc.workflow.json").read_text(encoding="utf-8")
            )
            html = (output_dir / "run-import-test.import-btb-lc.workflow.html").read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(opened_path.name, "run-import-test.import-btb-lc.workflow.html")
        self.assertEqual(summary["overall_decision"], "pass")
        self.assertTrue(summary["html_report_open_requested"])
        self.assertTrue(summary["html_report_opened"])
        self.assertTrue(summary["html_output_path"].endswith(".html"))
        self.assertEqual(summary["decision_counts"], {"pass": 1, "warning": 0, "hard_block": 0})
        self.assertEqual(summary["write_disposition_counts"]["new_writes_staged"], 1)
        self.assertEqual(summary["selected_rows"][0]["selected_row_index"], 3)
        self.assertEqual(summary["selected_rows"][0]["selected_sl_no"], "1")
        self.assertEqual(report["summary"]["staged"], 1)
        self.assertEqual(report["write_execution"]["status"], "not_requested")
        self.assertEqual(len(report["staged_write_plan"]), 4)
        self.assertIn("0742260401049", html)
        self.assertIn("<th>SL.No.</th>", html)
        self.assertNotIn("<th>Row</th>", html)
        self.assertIn("05/05/2026", html)
        self.assertIn("<h2>Snapshot</h2>", html)
        self.assertIn("<h2>Summary</h2>", html)
        self.assertIn("Generated at:", html)
        self.assertIn("<td>1883260400042</td>", html)
        self.assertNotIn("<td>LC-1883260400042</td>", html)

    def test_cli_file_picker_workflow_can_use_live_import_pi_register_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("input", "output", "reports", "runs", "backups", "workbooks"):
                (root / name).mkdir(parents=True, exist_ok=True)
            artifact_path = root / "input" / "0742260401049.pdf.import-btb-lc.json"
            artifact_path.write_text(json.dumps(_artifact(path=str(root / "input" / "0742260401049.pdf"))), encoding="utf-8")
            workbook_path = root / "workbook.json"
            workbook_path.write_text(json.dumps(_workbook_manifest()), encoding="utf-8")
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = root / "workflow.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'state_timezone = "Asia/Dhaka"',
                        f'report_root = "{(root / "reports").as_posix()}"',
                        f'run_artifact_root = "{(root / "runs").as_posix()}"',
                        f'backup_root = "{(root / "backups").as_posix()}"',
                        'outlook_profile = "outlook"',
                        f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                        'erp_base_url = "https://export-erp.local"',
                        'import_erp_base_url = "https://import-erp.local"',
                        'import_erp_username = "user"',
                        'import_erp_password = "pass"',
                        'playwright_browser_channel = "msedge"',
                        f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                        "excel_lock_timeout_seconds = 60",
                        "print_enabled = true",
                        'source_working_folder_entry_id = "src-folder"',
                        'destination_success_entry_id = "dst-folder"',
                    ]
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with patch("project.cli.PlaywrightImportPIRegisterProvider", return_value=_StaticPIRegisterProvider()) as provider_mock:
                with redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "run-import-btb-lc-file-picker",
                            "--config",
                            str(config_path),
                            "--input",
                            str(root / "input"),
                            "--output",
                            str(root / "output"),
                            "--workbook-json",
                            str(workbook_path),
                            "--live-erp-pi-register",
                            "--run-id",
                            "run-import-live-pi-test",
                            "--no-open-report",
                        ]
                    )

        summary = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(provider_mock.call_args.kwargs["login_url"], "https://import-erp.local")
        self.assertEqual(summary["overall_decision"], "pass")
        self.assertEqual(summary["staged_write_operation_count"], 4)

    def test_import_report_uses_sl_no_as_text_not_row_sequence(self) -> None:
        snapshot = _workbook_snapshot(
            rows=[_row(11, sl_no="633.0", lc="LC-1883260400042", export_amount="100000")]
        )
        document = _document(_artifact(value="50000"))

        result = allocate_import_btb_lc_documents(
            documents=[document],
            workbook_snapshot=snapshot,
            run_id="run-import-test",
            pi_register_provider=_StaticPIRegisterProvider(),
        )
        outcome = result.workflow_report["document_outcomes"][0]
        html = render_import_btb_lc_html_report(
            {
                **result.workflow_report,
                "completed_at_utc": "2026-06-20T10:30:00Z",
                "state_timezone": "Asia/Dhaka",
            }
        )

        self.assertEqual(outcome["selected_row_index"], 11)
        self.assertEqual(outcome["selected_sl_no"], "633")
        self.assertIn("<td>633</td>", html)
        self.assertNotIn("<td>11</td>", html)
        self.assertIn("20/06/2026 04:30:00 PM (Asia/Dhaka)", html)

    def test_import_report_shows_erp_values_and_candidate_evidence_on_failure(self) -> None:
        document = _document(_artifact(value="39000"))
        result = allocate_import_btb_lc_documents(
            documents=[document],
            workbook_snapshot=_workbook_snapshot(),
            run_id="run-import-report-failure-test",
            pi_register_provider=_StaticPIRegisterProvider(
                total_amount="39000",
                quantity_kg="8297",
            ),
        )
        outcome = result.workflow_report["document_outcomes"][0]
        html = render_import_btb_lc_html_report(result.workflow_report)

        self.assertEqual(outcome["decision"], "hard_block")
        self.assertIn("Calculated Quantity (Kgs)", html)
        self.assertIn("Raw Extracted Values", html)
        self.assertIn("seller PI=BTL/26/3183", html)
        self.assertIn("BTL/26/3183", html)
        self.assertIn("<td>39000</td>", html)
        self.assertIn("<td>8297</td>", html)
        self.assertIn("ratio 39.0000%", html)
        self.assertIn("import_no_qualified_workbook_row", html)

    def test_mixed_result_mail_retains_writable_document_disposition(self) -> None:
        passing_document = _document(_artifact(), snapshot_index=0)
        blocked_artifact = _artifact(
            path="C:/source/0742260401051.pdf",
            btb="0742260401051",
            decision="hard_block",
        )
        blocked_artifact["hard_block_discrepancies"] = [
            {
                "code": "import_pi_number_invalid",
                "severity": "hard_block",
                "message": "Seller PI was invalid.",
                "details": {},
            }
        ]
        blocked_document = _document(blocked_artifact, snapshot_index=0)
        allocation = allocate_import_btb_lc_documents(
            documents=[passing_document, blocked_document],
            workbook_snapshot=_workbook_snapshot(),
            run_id="run-import-mixed-mail-test",
            pi_register_provider=_StaticPIRegisterProvider(),
        )
        mail = _mail_snapshot()[0]
        outcomes = allocation.workflow_report["document_outcomes"]

        mail_outcomes = _build_current_full_mail_outcomes(
            mail_snapshot=[mail],
            relevance_by_mail_id={
                mail.mail_id: {
                    "eligible": True,
                    "import_keyword_revision": "2026-06-16.1",
                }
            },
            document_outcomes=outcomes,
            document_mail_id={
                str(outcome["document_id"]): mail.mail_id for outcome in outcomes
            },
            acquisition_discrepancies=[],
            run_id="run-import-mixed-mail-test",
        )

        self.assertEqual(len(allocation.staged_write_plan), 4)
        self.assertEqual(mail_outcomes[0]["final_decision"], "hard_block")
        self.assertEqual(mail_outcomes[0]["write_disposition"], "new_writes_staged")
        self.assertFalse(mail_outcomes[0]["eligible_for_mail_move"])

    def test_cli_file_picker_can_skip_report_browser_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            (input_dir / "0742260401049.pdf.import-btb-lc.json").write_text(
                json.dumps(_artifact(path=str(input_dir / "0742260401049.pdf"))),
                encoding="utf-8",
            )
            workbook_path = root / "workbook.json"
            workbook_path.write_text(json.dumps(_workbook_manifest()), encoding="utf-8")
            pi_report_path = _pi_report_path(root)

            stdout = io.StringIO()
            with patch("project.cli.open_import_btb_lc_report_in_browser") as open_mock:
                with redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "run-import-btb-lc-file-picker",
                            "--input",
                            str(input_dir),
                            "--output",
                            str(output_dir),
                            "--workbook-json",
                            str(workbook_path),
                            "--erp-pi-report",
                            str(pi_report_path),
                            "--run-id",
                            "run-import-test",
                            "--no-open-report",
                        ]
                    )
                open_mock.assert_not_called()
            summary = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertFalse(summary["html_report_open_requested"])
        self.assertFalse(summary["html_report_opened"])

    def test_cli_file_picker_accepts_repeated_inputs_as_one_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            first = input_dir / "first.json"
            second = input_dir / "second.json"
            first.write_text(json.dumps(_artifact(path=str(input_dir / "first.pdf"))), encoding="utf-8")
            second.write_text(json.dumps(_artifact(path=str(input_dir / "second.pdf"))), encoding="utf-8")
            workbook_path = root / "workbook.json"
            workbook_path.write_text(json.dumps(_workbook_manifest()), encoding="utf-8")
            pi_report_path = _pi_report_path(root)

            stdout = io.StringIO()
            with patch("project.cli.open_import_btb_lc_report_in_browser"):
                with redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "run-import-btb-lc-file-picker",
                            "--input",
                            str(second),
                            "--input",
                            str(first),
                            "--output",
                            str(output_dir),
                            "--workbook-json",
                            str(workbook_path),
                            "--erp-pi-report",
                            str(pi_report_path),
                            "--run-id",
                            "run-import-test",
                            "--no-open-report",
                        ]
                    )
            summary = json.loads(stdout.getvalue())
            report = json.loads(
                (output_dir / "run-import-test.import-btb-lc.workflow.json").read_text(encoding="utf-8")
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["document_count"], 2)
        self.assertEqual(summary["input_paths"], sorted(summary["input_paths"], key=str.casefold))
        self.assertEqual(report["document_outcomes"][0]["filename"], "first.pdf")
        self.assertEqual(report["document_outcomes"][1]["filename"], "second.pdf")

    def test_cli_file_picker_rejects_pdf_outside_import_document_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            import_root = root / "import_docs"
            outside = root / "outside"
            output_dir = root / "output"
            import_root.mkdir()
            outside.mkdir()
            pdf_path = outside / "0742260401049.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\nsynthetic\n")
            workbook_path = root / "workbook.json"
            workbook_path.write_text(json.dumps(_workbook_manifest()), encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(
                    [
                        "run-import-btb-lc-file-picker",
                        "--input",
                        str(pdf_path),
                        "--output",
                        str(output_dir),
                        "--workbook-json",
                        str(workbook_path),
                        "--import-document-root",
                        str(import_root),
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("must resolve beneath import_document_root", stderr.getvalue())

    def test_cli_file_picker_reports_browser_open_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            (input_dir / "0742260401049.pdf.import-btb-lc.json").write_text(
                json.dumps(_artifact(path=str(input_dir / "0742260401049.pdf"))),
                encoding="utf-8",
            )
            workbook_path = root / "workbook.json"
            workbook_path.write_text(json.dumps(_workbook_manifest()), encoding="utf-8")
            pi_report_path = _pi_report_path(root)

            stdout = io.StringIO()
            with patch(
                "project.cli.open_import_btb_lc_report_in_browser",
                side_effect=RuntimeError("browser unavailable"),
            ):
                with redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "run-import-btb-lc-file-picker",
                            "--input",
                            str(input_dir),
                            "--output",
                            str(output_dir),
                            "--workbook-json",
                            str(workbook_path),
                            "--erp-pi-report",
                            str(pi_report_path),
                            "--run-id",
                            "run-import-test",
                        ]
                    )
            summary = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertFalse(summary["html_report_opened"])
        self.assertEqual(summary["warnings"][0]["code"], "import_report_browser_open_failed")

    def test_keyword_relevance_uses_include_and_exclude_hits(self) -> None:
        keywords = load_import_relevance_keywords()
        mail = _mail_snapshot()[0]

        relevant = evaluate_import_mail_relevance(mail, keywords=keywords)
        excluded = evaluate_import_mail_relevance(
            _mail_snapshot(subject="Fabric BTB cancel notice")[0],
            keywords={**keywords, "exclude_keywords": ["cancel"]},
        )

        self.assertTrue(relevant["eligible"])
        self.assertIn("fabric", relevant["include_keyword_hits"])
        self.assertFalse(excluded["eligible"])
        self.assertEqual(excluded["exclude_keyword_hits"], ["cancel"])

    def test_current_full_acquires_promotes_allocates_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attachment_root = root / "attachments"
            attachment_root.mkdir()
            mail_snapshot = _mail_snapshot()
            (attachment_root / "0742260401049.pdf").write_bytes(b"%PDF-1.4\nsynthetic\n")
            output_dir = root / "out"
            import_root = root / "import_docs"
            provider = _StaticPageProvider(
                embedded=[ExtractedPage(1, _sample_text(), "embedded_text", 1.0, True)],
                ocr=[],
            )

            summary = run_import_btb_lc_current_full(
                mail_snapshot=mail_snapshot,
                attachment_provider=DirectoryAttachmentContentProvider(attachment_root),
                output_directory=output_dir,
                workbook_snapshot=_workbook_snapshot(),
                import_document_root=import_root,
                run_id="run-current-test",
                page_provider=provider,
                pi_register_provider=_StaticPIRegisterProvider(),
            )
            report = json.loads(
                (output_dir / "run-current-test.import-btb-lc.current-full.json").read_text(encoding="utf-8")
            )
            promoted_exists = (import_root / "2026" / "0742260401049.pdf").exists()

        self.assertEqual(summary["overall_decision"], "pass")
        self.assertEqual(summary["relevant_mail_count"], 1)
        self.assertEqual(summary["document_count"], 1)
        self.assertEqual(summary["selected_rows"][0]["selected_row_index"], 3)
        self.assertTrue(promoted_exists)
        self.assertEqual(report["mail_outcomes"][0]["final_decision"], "pass")
        self.assertEqual(report["mail_outcomes"][0]["write_disposition"], "new_writes_staged")
        self.assertEqual(report["mail_relevance"][0]["import_keyword_revision"], "2026-06-16.1")

    def test_current_full_subject_ineligible_mail_is_not_actioned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attachment_root = root / "attachments"
            attachment_root.mkdir()
            mail_snapshot = _mail_snapshot(subject="ordinary message")
            (attachment_root / "0742260401049.pdf").write_bytes(b"%PDF-1.4\nsynthetic\n")

            summary = run_import_btb_lc_current_full(
                mail_snapshot=mail_snapshot,
                attachment_provider=DirectoryAttachmentContentProvider(attachment_root),
                output_directory=root / "out",
                workbook_snapshot=_workbook_snapshot(),
                import_document_root=root / "import_docs",
                run_id="run-current-test",
                page_provider=_StaticPageProvider(
                    embedded=[ExtractedPage(1, _sample_text(), "embedded_text", 1.0, True)],
                    ocr=[],
                ),
            )

        self.assertEqual(summary["overall_decision"], "pass")
        self.assertEqual(summary["relevant_mail_count"], 0)
        self.assertEqual(summary["document_count"], 0)
        self.assertEqual(summary["write_disposition_counts"]["not_applicable"], 1)

    def test_cli_current_full_uses_snapshot_and_attachment_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks", "attachments", "import_docs"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = _config_path(root)
            snapshot_path = root / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-1",
                            "received_time": "2026-06-16T08:00:00+06:00",
                            "subject_raw": "Fabric BTB LC",
                            "sender_address": "import@example.com",
                            "attachments": [{"attachment_name": "0742260401049.pdf"}],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (root / "attachments" / "0742260401049.pdf").write_bytes(b"%PDF-1.4\nsynthetic\n")
            workbook_json = root / "workbook.json"
            workbook_json.write_text(json.dumps(_workbook_manifest()), encoding="utf-8")
            pi_report_path = _pi_report_path(root)

            stdout = io.StringIO()
            with patch(
                "project.workflows.import_btb_lc.workflow.extract_import_btb_lc_pdf",
                return_value=_artifact(path=str(root / "attachments" / "0742260401049.pdf")),
            ):
                with patch("project.cli.open_import_btb_lc_report_in_browser") as open_mock:
                    with redirect_stdout(stdout):
                        exit_code = main(
                            [
                                "run-import-btb-lc-current",
                                "--config",
                                str(config_path),
                                "--snapshot-json",
                                str(snapshot_path),
                                "--attachment-directory",
                                str(root / "attachments"),
                                "--output",
                                str(root / "reports"),
                                "--workbook-json",
                                str(workbook_json),
                                "--erp-pi-report",
                                str(pi_report_path),
                                "--run-id",
                                "run-current-test",
                            ]
                        )
                    open_mock.assert_called_once()
            summary = json.loads(stdout.getvalue())
            report = json.loads(
                (root / "reports" / "run-current-test.import-btb-lc.current-full.json").read_text(encoding="utf-8")
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["launcher_path"], "current_full")
        self.assertEqual(summary["overall_decision"], "pass")
        self.assertTrue(summary["html_report_opened"])
        self.assertTrue(
            str(report["source_document_acquisition"][0]["promotion"]["destination_path"]).startswith(
                str(root / "import_docs")
            )
        )

    def test_cli_current_full_moves_to_import_specific_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("reports", "runs", "backups", "workbooks", "attachments", "import_docs"):
                (root / name).mkdir(parents=True, exist_ok=True)
            workflow_year = __import__("datetime").datetime.now().year
            (root / "workbooks" / f"{workflow_year}-master.xlsx").write_bytes(b"fake workbook")
            config_path = _config_path(root)
            snapshot_path = root / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "entry-1",
                            "received_time": "2026-06-16T08:00:00+06:00",
                            "subject_raw": "Fabric BTB LC",
                            "sender_address": "import@example.com",
                            "attachments": [{"attachment_name": "0742260401049.pdf"}],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (root / "attachments" / "0742260401049.pdf").write_bytes(b"%PDF-1.4\nsynthetic\n")
            workbook_json = root / "workbook.json"
            duplicate_snapshot = _workbook_snapshot(
                rows=[
                    _row(
                        3,
                        lc="LC-1883260400042",
                        export_amount="100000",
                        btb="0742260401049",
                        import_amount="50000",
                    )
                ]
            )
            workbook_json.write_text(json.dumps(_workbook_manifest(duplicate_snapshot)), encoding="utf-8")
            pi_report_path = _pi_report_path(root)

            stdout = io.StringIO()
            with patch(
                "project.workflows.import_btb_lc.workflow.extract_import_btb_lc_pdf",
                return_value=_artifact(path=str(root / "attachments" / "0742260401049.pdf")),
            ):
                with patch("project.cli.open_import_btb_lc_report_in_browser"):
                    with redirect_stdout(stdout):
                        exit_code = main(
                            [
                                "run-import-btb-lc-current",
                                "--config",
                                str(config_path),
                                "--snapshot-json",
                                str(snapshot_path),
                                "--attachment-directory",
                                str(root / "attachments"),
                                "--output",
                                str(root / "reports"),
                                "--workbook-json",
                                str(workbook_json),
                                "--erp-pi-report",
                                str(pi_report_path),
                                "--run-id",
                                "run-current-test",
                                "--move-mails",
                                "--simulate-mail-moves",
                                "--no-open-report",
                            ]
                        )
            summary = json.loads(stdout.getvalue())
            report = json.loads(
                (root / "reports" / "run-current-test.import-btb-lc.current-full.json").read_text(encoding="utf-8")
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["mail_move_status"], "completed")
        self.assertEqual(report["mail_move"]["operations"][0]["destination_folder"], "import-dst-folder")


def _document(artifact: dict, *, snapshot_index: int = 0):
    return _document_from_artifact(
        artifact=artifact,
        snapshot_index=snapshot_index,
        attachment_index=None,
    )


def _artifact(
    *,
    path: str = "C:/source/0742260401049.pdf",
    btb: str = "0742260401049",
    date: str = "2026-05-05",
    value: str = "50000",
    currency: str = "USD",
    pi_numbers: list[str] | None = None,
    related: str = "LC-1883260400042",
    decision: str = "pass",
) -> dict:
    return {
        "schema_id": "import_btb_lc_extraction",
        "schema_version": "1.1.0",
        "report_schema_version": "1.1.0",
        "workflow_id": "import_btb_lc",
        "source": {
            "path": path,
            "filename": Path(path).name,
            "file_sha256": "a" * 64,
            "page_limit": 3,
        },
        "bank_detection": {"status": "detected", "bank_id": "the_city_bank_plc"},
        "fields": {
            "btb_lc_number": {"raw": btb, "canonical": btb},
            "btb_lc_date": {"raw": date, "canonical": date},
            "btb_lc_value": {"raw": value, "canonical": value},
            "currency": {"raw": currency, "canonical": currency},
            "seller_pi_numbers": {
                "raw": pi_numbers or ["BTL/26/3183"],
                "canonical": pi_numbers or ["BTL/26/3183"],
            },
            "related_export_lc_number": {"raw": related, "canonical": related},
        },
        "filename_comparison": {"matches": True},
        "warnings": [],
        "hard_block_discrepancies": [],
        "overall_extraction_decision": decision,
    }


def _workbook_snapshot(*, rows: list[WorkbookRow] | None = None) -> WorkbookSnapshot:
    return WorkbookSnapshot(
        sheet_name="Sheet1",
        headers=[
            WorkbookHeader(column_index=1, text="SL.No."),
            WorkbookHeader(column_index=4, text="L/C & S/C No."),
            WorkbookHeader(column_index=6, text="Amount"),
            WorkbookHeader(column_index=7, text="UP No."),
            WorkbookHeader(column_index=20, text="BTB LC Issue Date"),
            WorkbookHeader(column_index=21, text="BTB L/C No."),
            WorkbookHeader(column_index=22, text="Amount"),
            WorkbookHeader(column_index=23, text="Quantity (Kgs)"),
        ],
        rows=rows or [_row(3, lc="LC-1883260400042", export_amount="100000")],
    )


def _row(
    row_index: int,
    *,
    sl_no: str | None = None,
    lc: str,
    export_amount: str,
    up_no: str = "",
    btb: str = "",
    issue_date: str = "",
    import_amount: str = "",
    quantity_kgs: str = "",
) -> WorkbookRow:
    return WorkbookRow(
        row_index=row_index,
        values={
            1: str(row_index - 2 if sl_no is None else sl_no),
            4: lc,
            6: export_amount,
            7: up_no,
            20: issue_date,
            21: btb,
            22: import_amount,
            23: quantity_kgs,
        },
    )


def _workbook_manifest(snapshot: WorkbookSnapshot | None = None) -> dict:
    snapshot = snapshot or _workbook_snapshot()
    return {
        "sheet_name": snapshot.sheet_name,
        "headers": [
            {"column_index": header.column_index, "text": header.text}
            for header in snapshot.headers
        ],
        "rows": [
            {
                "row_index": row.row_index,
                "values": {str(column): value for column, value in row.values.items()},
            }
            for row in snapshot.rows
        ],
    }


def _pi_report_path(root: Path, *, total_amount: str = "50000", quantity_kg: str = "1709") -> Path:
    path = root / "rptPIRegisterCustomsPDL.csv"
    path.write_text(
        "\n".join(
            [
                "SL.,Unit,PI Number,PI Date,Buyer Name,Description of Goods,Tenor,Bankers,HS Code,Qty.Bag,Qty.Kg,Price/KG,Total Amount,File No,LC Number",
                f"1,BADSHA TEXTILES LTD.,BTL/26/3183,20/06/2026,PIONEER DENIM LIMITED,YARN,120,HSBC,5203,35,{quantity_kg},3,{total_amount},B3043/26,",
            ]
        ),
        encoding="utf-8",
    )
    return path


class _StaticPageProvider:
    def __init__(self, *, embedded: list[ExtractedPage], ocr: list[ExtractedPage]) -> None:
        self._embedded = embedded
        self._ocr = ocr

    def embedded_pages(self, *, pdf_path: Path, page_limit: int) -> list[ExtractedPage]:
        del pdf_path
        return [page for page in self._embedded if page.page_number <= page_limit]

    def ocr_pages(self, *, pdf_path: Path, page_numbers: list[int]) -> list[ExtractedPage]:
        del pdf_path
        return [page for page in self._ocr if page.page_number in page_numbers]


class _StaticPIRegisterProvider:
    def __init__(
        self,
        *,
        pi_number: str = "BTL/26/3183",
        total_amount: str = "50000",
        quantity_kg: str = "1709",
    ) -> None:
        self._rows = [
            {
                "pi_number": pi_number,
                "quantity_kg": quantity_kg,
                "total_amount": total_amount,
                "source_row_index": 2,
                "raw_values": {},
            }
        ]

    def lookup_pi_numbers(self, *, pi_numbers: list[str]):
        from project.erp.import_pi import ImportPIRegisterRow

        rows = [
            ImportPIRegisterRow(
                pi_number=str(row["pi_number"]),
                quantity_kg=str(row["quantity_kg"]),
                total_amount=str(row["total_amount"]),
                source_row_index=int(row["source_row_index"]),
                raw_values={},
            )
            for row in self._rows
        ]
        return {pi_number: [row for row in rows if row.pi_number == pi_number] for pi_number in pi_numbers}

    def load_rows(self):
        return [row for rows in self.lookup_pi_numbers(pi_numbers=["BTL/26/3183"]).values() for row in rows]


def _mail_snapshot(*, subject: str = "Fabric BTB LC"):
    return build_email_snapshot(
        [
            SourceEmailRecord(
                entry_id="entry-1",
                received_time="2026-06-16T08:00:00+06:00",
                subject_raw=subject,
                sender_address="import@example.com",
                attachments=[SourceAttachmentRecord(attachment_name="0742260401049.pdf")],
            )
        ],
        state_timezone="Asia/Dhaka",
    )


def _sample_text() -> str:
    return "\n".join(
        [
            "City Bank PLC. Trade Services Division",
            "20:",
            "Documentary Credit Number",
            "0742260401049",
            "31C: Date of Issue",
            "260505",
            "32B: Currency Code, Amount",
            "USD50000,00",
            "41D: Available With ... By ...",
            "45A: Description of Goods and/or Services",
            "AS PER PROFORMA INVOICE NO. BTL/26/3183 DATED 01-01-2026",
            "47A: Additional Conditions",
            "EXPORT L/C NO. 1883260400042 DATE: 26-04-2026",
        ]
    )


def _config_path(root: Path) -> Path:
    config_path = root / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                'state_timezone = "Asia/Dhaka"',
                f'report_root = "{(root / "reports").as_posix()}"',
                f'run_artifact_root = "{(root / "runs").as_posix()}"',
                f'backup_root = "{(root / "backups").as_posix()}"',
                'outlook_profile = "outlook"',
                f'master_workbook_root = "{(root / "workbooks").as_posix()}"',
                'erp_base_url = "https://erp.local"',
                'playwright_browser_channel = "msedge"',
                f'master_workbook_path_template = "{((root / "workbooks") / "{year}-master.xlsx").as_posix()}"',
                "excel_lock_timeout_seconds = 60",
                "print_enabled = true",
                'source_working_folder_entry_id = "src-folder"',
                'destination_success_entry_id = "dst-folder"',
                'import_destination_success_entry_id = "import-dst-folder"',
                f'import_document_root = "{(root / "import_docs").as_posix()}"',
            ]
        ),
        encoding="utf-8",
    )
    return config_path


if __name__ == "__main__":
    unittest.main()
