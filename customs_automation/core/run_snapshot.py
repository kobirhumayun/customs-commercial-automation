from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from zoneinfo import ZoneInfo

from customs_automation.core.contracts import EmailMessage, MailOrderRecord

BANGLADESH_STATE_TIMEZONE = ZoneInfo("Asia/Dhaka")


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    run_id: str
    workflow_id: str
    ordered_mail_ids: list[str]
    mail_order_records: list[MailOrderRecord]


def order_messages_deterministically(
    messages: list[EmailMessage],
    state_timezone: ZoneInfo = BANGLADESH_STATE_TIMEZONE,
) -> list[MailOrderRecord]:
    ordered = sorted(
        messages,
        key=lambda mail: (
            mail.received_time_utc.astimezone(UTC),
            mail.entry_id,
        ),
    )

    return [
        MailOrderRecord(
            entry_id=mail.entry_id,
            received_time_utc=mail.received_time_utc.astimezone(UTC),
            received_time_local_iso=mail.received_time_utc.astimezone(state_timezone).isoformat(),
            order_index=index,
        )
        for index, mail in enumerate(ordered)
    ]


def build_run_snapshot(run_id: str, workflow_id: str, messages: list[EmailMessage]) -> RunSnapshot:
    ordered_records = order_messages_deterministically(messages=messages)
    return RunSnapshot(
        run_id=run_id,
        workflow_id=workflow_id,
        ordered_mail_ids=[record.entry_id for record in ordered_records],
        mail_order_records=ordered_records,
    )
