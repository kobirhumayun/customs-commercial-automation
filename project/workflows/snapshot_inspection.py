from __future__ import annotations

from project.models import EmailMessage
from project.utils.json import to_jsonable


def summarize_mail_snapshot(snapshot: list[EmailMessage]) -> dict[str, object]:
    return {
        "snapshot_count": len(snapshot),
        "mail_iteration_order": [mail.mail_id for mail in snapshot],
        "entry_id_order": [mail.entry_id for mail in snapshot],
        "attachment_count": sum(len(mail.attachments) for mail in snapshot),
        "mails": to_jsonable(snapshot),
    }
