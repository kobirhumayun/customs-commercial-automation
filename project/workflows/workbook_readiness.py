from __future__ import annotations

from project.models import WorkflowId, WorkbookSessionPreflight, WriteOperation
from project.utils.json import to_jsonable
from project.workbook import WorkbookSnapshot, prevalidate_staged_write_plan, resolve_export_header_mapping


def summarize_workbook_readiness(
    *,
    workflow_id: WorkflowId,
    workbook_snapshot: WorkbookSnapshot | None,
    session_preflight: WorkbookSessionPreflight | None = None,
    staged_write_plan: list[WriteOperation] | None = None,
    run_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "workflow_id": workflow_id.value,
        "session_preflight": to_jsonable(session_preflight),
        "workbook_available": workbook_snapshot is not None,
    }

    if workbook_snapshot is None:
        payload.update(
            {
                "sheet_name": None,
                "header_count": 0,
                "row_count": 0,
                "headers": [],
                "header_mapping_status": "not_available",
                "header_mapping": None,
            }
        )
    else:
        header_mapping = _resolve_workflow_mapping(workflow_id, workbook_snapshot)
        payload.update(
            {
                "sheet_name": workbook_snapshot.sheet_name,
                "header_count": len(workbook_snapshot.headers),
                "row_count": len(workbook_snapshot.rows),
                "headers": to_jsonable(workbook_snapshot.headers),
                "header_mapping_status": _mapping_status(workflow_id, header_mapping),
                "header_mapping": header_mapping,
            }
        )

    if staged_write_plan is not None:
        payload["staged_write_operation_count"] = len(staged_write_plan)
        if workbook_snapshot is None:
            payload["target_prevalidation"] = {
                "status": "not_run",
                "reason": "workbook_unavailable",
                "summary": None,
                "discrepancy_count": 0,
                "discrepancy_codes": [],
            }
        else:
            effective_run_id = run_id or "inspect-workbook-readiness"
            result = prevalidate_staged_write_plan(
                workflow_id=workflow_id,
                run_id=effective_run_id,
                workbook_snapshot=workbook_snapshot,
                staged_write_plan=staged_write_plan,
            )
            payload["target_prevalidation"] = {
                "status": result.summary.status,
                "reason": None,
                "summary": to_jsonable(result.summary),
                "discrepancy_count": len(result.discrepancy_reports),
                "discrepancy_codes": [report.code for report in result.discrepancy_reports],
            }

    return payload


def _resolve_workflow_mapping(
    workflow_id: WorkflowId,
    workbook_snapshot: WorkbookSnapshot,
) -> dict[str, int] | None:
    if workflow_id == WorkflowId.EXPORT_LC_SC:
        return resolve_export_header_mapping(workbook_snapshot)
    return {}


def _mapping_status(workflow_id: WorkflowId, mapping: dict[str, int] | None) -> str:
    if workflow_id != WorkflowId.EXPORT_LC_SC:
        return "not_applicable"
    return "resolved" if mapping is not None else "invalid"
