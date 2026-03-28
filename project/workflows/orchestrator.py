from __future__ import annotations

from project.models import EmailMessage, FinalDecision, MailOutcomeRecord, MailProcessingStatus, WorkflowId


def initialize_mail_outcomes(
    *,
    run_id: str,
    workflow_id: WorkflowId,
    mail_snapshot: list[EmailMessage],
) -> list[MailOutcomeRecord]:
    return [
        MailOutcomeRecord(
            run_id=run_id,
            mail_id=mail.mail_id,
            workflow_id=workflow_id,
            snapshot_index=mail.snapshot_index,
            processing_status=MailProcessingStatus.SNAPSHOTTED,
            final_decision=None,
            decision_reasons=["Awaiting validation."],
            eligible_for_write=False,
            eligible_for_print=False,
            eligible_for_mail_move=False,
            source_entry_id=mail.entry_id,
            subject_raw=mail.subject_raw,
            sender_address=mail.sender_address,
        )
        for mail in mail_snapshot
    ]
