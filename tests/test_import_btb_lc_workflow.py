from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from project.cli import main
from project.workbook import WorkbookHeader, WorkbookRow, WorkbookSnapshot
from project.workflows.import_btb_lc.workflow import (
    _document_from_artifact,
    allocate_import_btb_lc_documents,
    resolve_import_btb_lc_header_mapping,
)


class ImportBTBLCWorkflowTests(unittest.TestCase):
    def test_header_mapping_resolves_import_columns_without_shared_mapping(self) -> None:
        mapping = resolve_import_btb_lc_header_mapping(_workbook_snapshot())

        self.assertIsNotNone(mapping)
        assert mapping is not None
        self.assertEqual(mapping.lc_sc_no, 4)
        self.assertEqual(mapping.up_no, 7)
        self.assertEqual(mapping.export_amount, 6)
        self.assertEqual(mapping.btb_lc_no, 21)
        self.assertEqual(mapping.btb_lc_issue_date, 20)
        self.assertEqual(mapping.import_amount, 22)

    def test_allocation_selects_highest_eligible_export_amount_row(self) -> None:
        snapshot = _workbook_snapshot(
            rows=[
                _row(3, lc="LC-1883260400042", export_amount="100000"),
                _row(4, lc="1883260400042", export_amount="120000"),
                _row(5, lc="LC-1883260400042", export_amount="200000"),
            ]
        )
        document = _document(_artifact(value="50000"))

        result = allocate_import_btb_lc_documents(
            documents=[document],
            workbook_snapshot=snapshot,
            run_id="run-import-test",
        )
        outcome = result.workflow_report["document_outcomes"][0]

        self.assertEqual(outcome["decision"], "pass")
        self.assertEqual(outcome["selected_row_index"], 4)
        self.assertEqual(len(result.staged_write_plan), 3)
        self.assertEqual(
            [operation.column_key for operation in result.staged_write_plan],
            ["btb_lc_no", "btb_lc_issue_date", "import_amount"],
        )

    def test_allocation_hard_blocks_when_no_qualified_row(self) -> None:
        snapshot = _workbook_snapshot(
            rows=[_row(3, lc="LC-9999999999999", export_amount="100000")]
        )
        document = _document(_artifact(value="50000"))

        result = allocate_import_btb_lc_documents(
            documents=[document],
            workbook_snapshot=snapshot,
            run_id="run-import-test",
        )
        outcome = result.workflow_report["document_outcomes"][0]

        self.assertEqual(outcome["decision"], "hard_block")
        self.assertEqual(
            outcome["hard_block_discrepancies"][0]["code"],
            "import_no_qualified_workbook_row",
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
        )
        outcomes = result.workflow_report["document_outcomes"]

        self.assertEqual(outcomes[0]["decision"], "pass")
        self.assertEqual(outcomes[1]["decision"], "warning")
        self.assertEqual(outcomes[1]["warnings"][0]["code"], "import_duplicate_document_same_run")
        self.assertEqual(len(result.staged_write_plan), 3)

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
        self.assertEqual(report["summary"]["staged"], 1)
        self.assertEqual(report["write_execution"]["status"], "not_requested")
        self.assertEqual(len(report["staged_write_plan"]), 3)
        self.assertIn("0742260401049", html)

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
                            "--run-id",
                            "run-import-test",
                        ]
                    )
            summary = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertFalse(summary["html_report_opened"])
        self.assertEqual(summary["warnings"][0]["code"], "import_report_browser_open_failed")


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
            "btb_lc_number": {"canonical": btb},
            "btb_lc_date": {"canonical": date},
            "btb_lc_value": {"canonical": value},
            "currency": {"canonical": currency},
            "seller_pi_numbers": {"canonical": pi_numbers or ["BTL/26/3183"]},
            "related_export_lc_number": {"canonical": related},
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
            WorkbookHeader(column_index=4, text="L/C & S/C No."),
            WorkbookHeader(column_index=6, text="Amount"),
            WorkbookHeader(column_index=7, text="UP No."),
            WorkbookHeader(column_index=20, text="BTB LC Issue Date"),
            WorkbookHeader(column_index=21, text="BTB L/C No."),
            WorkbookHeader(column_index=22, text="Amount"),
        ],
        rows=rows or [_row(3, lc="LC-1883260400042", export_amount="100000")],
    )


def _row(
    row_index: int,
    *,
    lc: str,
    export_amount: str,
    up_no: str = "",
    btb: str = "",
    issue_date: str = "",
    import_amount: str = "",
) -> WorkbookRow:
    return WorkbookRow(
        row_index=row_index,
        values={
            4: lc,
            6: export_amount,
            7: up_no,
            20: issue_date,
            21: btb,
            22: import_amount,
        },
    )


def _workbook_manifest() -> dict:
    snapshot = _workbook_snapshot()
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


if __name__ == "__main__":
    unittest.main()
