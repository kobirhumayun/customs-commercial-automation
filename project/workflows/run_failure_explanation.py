from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from project.models import MailOutcomeRecord, RunReport, WriteOperation
from project.storage.artifacts import RunArtifactPaths


def build_run_failure_explanation(
    *,
    run_report: RunReport,
    mail_outcomes: list[MailOutcomeRecord],
    staged_write_plan: list[WriteOperation],
    artifact_paths: RunArtifactPaths,
) -> dict[str, Any]:
    discrepancies = _load_jsonl(artifact_paths.discrepancies_path)
    target_probes = _load_jsonl(artifact_paths.target_probes_path)
    outcomes_by_mail = {outcome.mail_id: outcome for outcome in mail_outcomes}
    file_numbers_by_target = _index_file_numbers_by_target(staged_write_plan)
    discrepancy_codes_by_mail = _index_discrepancy_codes_by_mail(discrepancies)
    all_discrepancy_codes = {
        code
        for discrepancy in discrepancies
        if (code := _optional_string(discrepancy.get("code")))
    }

    primary_causes: list[dict[str, Any]] = []
    related_causes: list[dict[str, Any]] = []
    for discrepancy in discrepancies:
        code = str(discrepancy.get("code", "")).strip()
        mail_id = _optional_string(discrepancy.get("mail_id"))
        cause = _build_discrepancy_cause(
            discrepancy=discrepancy,
            outcome=outcomes_by_mail.get(mail_id or ""),
            file_numbers_by_target=file_numbers_by_target,
        )
        if _is_secondary_discrepancy(code, mail_id, discrepancy_codes_by_mail, all_discrepancy_codes):
            cause["secondary"] = True
            related_causes.append(cause)
        else:
            primary_causes.append(cause)

    for probe in target_probes:
        if str(probe.get("classification", "")).strip() in {"", "matches_pre_write", "matches_post_write"}:
            continue
        if _has_discrepancy_for_probe(primary_causes + related_causes, probe):
            continue
        primary_causes.append(
            _build_probe_cause(
                probe=probe,
                outcome=outcomes_by_mail.get(str(probe.get("mail_id", ""))),
                file_numbers_by_target=file_numbers_by_target,
            )
        )

    return {
        "run_id": run_report.run_id,
        "workflow_id": run_report.workflow_id.value,
        "overall_status": "attention_required" if primary_causes else "no_failure_causes_found",
        "phase_statuses": {
            "write": run_report.write_phase_status.value,
            "print": run_report.print_phase_status.value,
            "mail_moves": run_report.mail_move_phase_status.value,
        },
        "decision_summary": dict(run_report.summary),
        "primary_cause_count": len(primary_causes),
        "related_cause_count": len(related_causes),
        "primary_causes": primary_causes,
        "related_causes": related_causes,
        "context": _build_context(mail_outcomes),
        "next_steps": _build_next_steps(primary_causes),
    }


def _build_discrepancy_cause(
    *,
    discrepancy: dict[str, Any],
    outcome: MailOutcomeRecord | None,
    file_numbers_by_target: dict[tuple[str, int], str],
) -> dict[str, Any]:
    code = str(discrepancy.get("code", "")).strip()
    details = discrepancy.get("details") if isinstance(discrepancy.get("details"), dict) else {}
    mail_id = _optional_string(discrepancy.get("mail_id"))
    category = _category_for_code(code)
    row_index = _optional_int(details.get("row_index"))
    column_key = _optional_string(details.get("column_key"))
    file_numbers = list(outcome.file_numbers_extracted) if outcome is not None else []
    cause: dict[str, Any] = {
        "category": category,
        "code": code,
        "severity": _optional_string(discrepancy.get("severity")),
        "message": _optional_string(discrepancy.get("message")),
        "mail_id": mail_id,
        "subject": outcome.subject_raw if outcome is not None else None,
        "file_numbers": file_numbers,
        "operator_hint": _operator_hint_for_code(code, details),
    }
    if code == "ud_shared_column_nonblank_policy_unresolved":
        cause["operator_summary"] = (
            "The UD/IP/EXP workflow selected the correct workbook row, but one or more target cells "
            "already contain values. Phase 1 blocks the mail because target cells must be blank before writing."
        )
        cause["workbook_targets"] = _ud_nonblank_workbook_targets(
            details=details,
            file_numbers=file_numbers,
        )
    if code == "export_file_number_missing":
        cause["body_excerpt"] = _excerpt(str(details.get("body_text", "")))
    if row_index is not None or column_key is not None:
        cause["workbook_target"] = {
            "row_index": row_index,
            "column_key": column_key,
            "column_index": _optional_int(details.get("column_index")),
            "observed_value": _optional_string(details.get("observed_value")),
            "expected_pre_write_value": _optional_string(details.get("expected_pre_write_value")),
            "expected_post_write_value": _optional_string(details.get("expected_post_write_value")),
            "failure_reason": _optional_string(details.get("failure_reason")),
            "file_number": file_numbers_by_target.get((mail_id or "", row_index or -1)),
        }
    return cause


def _build_probe_cause(
    *,
    probe: dict[str, Any],
    outcome: MailOutcomeRecord | None,
    file_numbers_by_target: dict[tuple[str, int], str],
) -> dict[str, Any]:
    mail_id = _optional_string(probe.get("mail_id"))
    row_index = _optional_int(probe.get("row_index"))
    return {
        "category": "workbook_prevalidation",
        "code": "workbook_target_probe_failed",
        "severity": "hard_block",
        "message": "A workbook target probe did not match the expected safe state.",
        "mail_id": mail_id,
        "subject": outcome.subject_raw if outcome is not None else None,
        "workbook_target": {
            "row_index": row_index,
            "column_key": _optional_string(probe.get("column_key")),
            "column_index": _optional_int(probe.get("column_index")),
            "observed_value": _optional_string(probe.get("observed_value")),
            "expected_pre_write_value": _optional_string(probe.get("expected_pre_write_value")),
            "expected_post_write_value": _optional_string(probe.get("expected_post_write_value")),
            "classification": _optional_string(probe.get("classification")),
            "file_number": file_numbers_by_target.get((mail_id or "", row_index or -1)),
        },
        "operator_hint": "Check the workbook target row/cell. Append targets must be blank and row-safe before writing.",
    }


def _build_context(mail_outcomes: list[MailOutcomeRecord]) -> dict[str, Any]:
    processing_counts = Counter(outcome.processing_status.value for outcome in mail_outcomes)
    disposition_counts = Counter(
        outcome.write_disposition or "not_recorded"
        for outcome in mail_outcomes
    )
    return {
        "mail_processing_status_counts": dict(sorted(processing_counts.items())),
        "write_disposition_counts": dict(sorted(disposition_counts.items())),
        "duplicate_only_mail_count": disposition_counts.get("duplicate_only_noop", 0),
        "new_write_mail_count": disposition_counts.get("new_writes_staged", 0),
        "mixed_duplicate_and_new_mail_count": disposition_counts.get("mixed_duplicate_and_new_writes", 0),
    }


def _build_next_steps(primary_causes: list[dict[str, Any]]) -> list[str]:
    steps: list[str] = []
    categories = {str(cause.get("category")) for cause in primary_causes}
    if "workbook_prevalidation" in categories:
        steps.append("Inspect the reported workbook row and column; clear stray values or fix the partial row, then rerun.")
    if "mail_validation" in categories:
        steps.append("Move unrelated mails out of the working folder or ensure each intended mail body includes a canonical P/YY/NNNN file number.")
    if "document_storage" in categories:
        steps.append("Resolve the upstream ERP-family/file-number issue first; attachment paths depend on a verified family.")
    if not steps and primary_causes:
        steps.append("Review the primary causes above, correct the listed artifacts or inputs, then rerun the launcher.")
    if not steps:
        steps.append("No primary failure cause was found in the persisted run artifacts.")
    return steps


def _index_file_numbers_by_target(staged_write_plan: list[WriteOperation]) -> dict[tuple[str, int], str]:
    index: dict[tuple[str, int], str] = {}
    for operation in staged_write_plan:
        if operation.column_key != "file_no":
            continue
        value = "" if operation.expected_post_write_value is None else str(operation.expected_post_write_value)
        if value.strip():
            index[(operation.mail_id, operation.row_index)] = value.strip()
    return index


def _index_discrepancy_codes_by_mail(discrepancies: list[dict[str, Any]]) -> dict[str, set[str]]:
    codes_by_mail: dict[str, set[str]] = {}
    for discrepancy in discrepancies:
        mail_id = _optional_string(discrepancy.get("mail_id"))
        code = _optional_string(discrepancy.get("code"))
        if not mail_id or not code:
            continue
        codes_by_mail.setdefault(mail_id, set()).add(code)
    return codes_by_mail


def _is_secondary_discrepancy(
    code: str,
    mail_id: str | None,
    discrepancy_codes_by_mail: dict[str, set[str]],
    all_discrepancy_codes: set[str],
) -> bool:
    if code == "document_storage_path_unresolved" and mail_id:
        return "export_file_number_missing" in discrepancy_codes_by_mail.get(mail_id, set())
    if code == "mail_move_gate_unsatisfied":
        return any(other_code != "mail_move_gate_unsatisfied" for other_code in all_discrepancy_codes)
    return False


def _has_discrepancy_for_probe(causes: list[dict[str, Any]], probe: dict[str, Any]) -> bool:
    mail_id = _optional_string(probe.get("mail_id"))
    row_index = _optional_int(probe.get("row_index"))
    column_key = _optional_string(probe.get("column_key"))
    for cause in causes:
        target = cause.get("workbook_target")
        if not isinstance(target, dict):
            continue
        if (
            cause.get("mail_id") == mail_id
            and target.get("row_index") == row_index
            and target.get("column_key") == column_key
        ):
            return True
    return False


def _category_for_code(code: str) -> str:
    if code.startswith("export_") or code.startswith("mail_"):
        return "mail_validation"
    if code.startswith("document_"):
        return "document_storage"
    if code.startswith("workbook_") or code == "ud_shared_column_nonblank_policy_unresolved":
        return "workbook_prevalidation"
    if code.startswith("print_"):
        return "print"
    return "run_safety"


def _operator_hint_for_code(code: str, details: dict[str, Any]) -> str:
    if code == "export_file_number_missing":
        return "The email body did not contain a canonical P/YY/NNNN file number."
    if code == "document_storage_path_unresolved":
        return "Attachment storage could not be resolved because no verified ERP family was available."
    if code == "workbook_target_prevalidation_failed":
        if details.get("failure_reason") == "row_eligibility_failed":
            return "The selected append row was not fully safe; check for existing values in the reported row/column."
        return "The workbook target did not match the required pre-write state."
    if code == "ud_shared_column_nonblank_policy_unresolved":
        return (
            "Do not rerun as-is. Clear the mistakenly retained UD target date/value cells in the reported workbook "
            "row, or restore the full previously written UD record, then run the workflow again."
        )
    return "Review the discrepancy message and details for the exact failed contract."


def _ud_nonblank_workbook_targets(
    *,
    details: dict[str, Any],
    file_numbers: list[str],
) -> list[dict[str, Any]]:
    target_rows = details.get("target_rows")
    if not isinstance(target_rows, list):
        return []
    targets: list[dict[str, Any]] = []
    for item in target_rows:
        if not isinstance(item, dict):
            continue
        targets.append(
            {
                "row_index": _optional_int(item.get("row_index")),
                "column_key": _optional_string(item.get("column_key")),
                "observed_value": _optional_string(item.get("observed_value")),
                "required_pre_write_state": "blank",
                "failure_reason": "target_cell_already_contains_value",
                "file_numbers": file_numbers,
            }
        )
    return targets


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            records.append(item)
    return records


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text != "" else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _excerpt(value: str, *, max_length: int = 240) -> str | None:
    text = " ".join(value.split())
    if not text:
        return None
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."
