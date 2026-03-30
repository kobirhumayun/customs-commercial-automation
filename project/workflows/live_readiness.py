from __future__ import annotations

from typing import Any

from project.models import WorkflowId


def build_live_environment_readiness(
    *,
    workflow_id: WorkflowId,
    snapshot_section: dict[str, Any] | None,
    erp_section: dict[str, Any] | None,
    workbook_section: dict[str, Any] | None,
    print_section: dict[str, Any] | None,
) -> dict[str, Any]:
    sections = {
        "snapshot": snapshot_section or _not_applicable_section("snapshot"),
        "erp": erp_section or _not_applicable_section("erp"),
        "workbook": workbook_section or _not_applicable_section("workbook"),
        "print": print_section or _not_applicable_section("print"),
    }
    applicable_sections = [
        section for section in sections.values() if section.get("status") != "not_applicable"
    ]
    ready_count = sum(1 for section in applicable_sections if section.get("status") == "ready")
    issue_count = sum(1 for section in applicable_sections if section.get("status") != "ready")
    return {
        "workflow_id": workflow_id.value,
        "overall_status": "ready" if issue_count == 0 else "attention_required",
        "applicable_section_count": len(applicable_sections),
        "ready_section_count": ready_count,
        "issue_section_count": issue_count,
        "sections": sections,
    }


def build_snapshot_readiness_section(snapshot_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ready",
        "snapshot_count": int(snapshot_payload.get("snapshot_count", 0)),
        "attachment_count": int(snapshot_payload.get("attachment_count", 0)),
        "entry_id_order_preview": list(snapshot_payload.get("entry_id_order", []))[:10],
        "mail_iteration_order_preview": list(snapshot_payload.get("mail_iteration_order", []))[:10],
    }


def build_erp_readiness_section(
    *,
    requested_file_numbers: list[str],
    erp_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if erp_payload is None:
        return {
            "status": "ready",
            "lookup_scope": "connectivity_only",
            "requested_file_numbers": list(requested_file_numbers),
            "canonical_file_numbers": [],
            "match_count": 0,
        }
    return {
        "status": "ready",
        "lookup_scope": "file_number_lookup",
        "requested_file_numbers": list(erp_payload.get("requested_file_numbers", [])),
        "canonical_file_numbers": list(erp_payload.get("canonical_file_numbers", [])),
        "match_count": int(erp_payload.get("match_count", 0)),
    }


def build_workbook_readiness_section(workbook_payload: dict[str, Any]) -> dict[str, Any]:
    session_preflight = workbook_payload.get("session_preflight") or {}
    header_mapping_status = str(workbook_payload.get("header_mapping_status") or "not_available")
    workbook_available = bool(workbook_payload.get("workbook_available"))
    preflight_status = str(session_preflight.get("status") or "")
    ready = workbook_available and preflight_status in {"", "ready"} and header_mapping_status in {
        "resolved",
        "not_applicable",
    }
    return {
        "status": "ready" if ready else "issue",
        "workbook_available": workbook_available,
        "sheet_name": workbook_payload.get("sheet_name"),
        "header_mapping_status": header_mapping_status,
        "row_count": int(workbook_payload.get("row_count", 0)),
        "session_preflight_status": preflight_status or None,
    }


def build_print_readiness_section(print_payload: dict[str, Any], *, print_enabled: bool) -> dict[str, Any]:
    if not print_enabled:
        return {
            "status": "issue",
            "print_enabled": False,
            "available": bool(print_payload.get("available", False)),
            "error": "Live print execution is disabled by configuration (print_enabled=false).",
        }
    available = bool(print_payload.get("available", False))
    return {
        "status": "ready" if available else "issue",
        "print_enabled": True,
        "available": available,
        "resolved_executable_path": print_payload.get("resolved_executable_path"),
        "printer_name": print_payload.get("printer_name"),
        "blank_separator_exists": bool(print_payload.get("blank_separator_exists", False)),
        "error": print_payload.get("error"),
    }


def build_issue_section(section: str, error: str) -> dict[str, Any]:
    return {
        "status": "issue",
        "section": section,
        "error": error,
    }


def _not_applicable_section(name: str) -> dict[str, Any]:
    return {"status": "not_applicable", "section": name}
