from __future__ import annotations

from typing import Any

from project.erp import ERPRowProvider
from project.models import EmailMessage, WorkflowId
from project.workflows.export_lc_sc.payloads import build_export_mail_payload


def build_workflow_payload(
    workflow_id: WorkflowId,
    mail: EmailMessage,
    *,
    erp_row_provider: ERPRowProvider | None = None,
) -> Any | None:
    if workflow_id == WorkflowId.EXPORT_LC_SC:
        return build_export_mail_payload(mail, erp_row_provider=erp_row_provider)
    return None
