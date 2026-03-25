from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from customs_automation.core.contracts import EmailMessage


class IntakeAdapter(Protocol):
    def list_working_messages(self) -> list[EmailMessage]:
        """Return all messages currently present in the workflow working queue."""


@dataclass(frozen=True, slots=True)
class StaticIntakeAdapter:
    messages: list[EmailMessage]

    def list_working_messages(self) -> list[EmailMessage]:
        return list(self.messages)


@dataclass(frozen=True, slots=True)
class JsonFileIntakeAdapter:
    snapshot_path: Path

    def list_working_messages(self) -> list[EmailMessage]:
        payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Snapshot input must be a JSON array")

        messages: list[EmailMessage] = []
        for row in payload:
            if not isinstance(row, dict):
                raise ValueError("Snapshot items must be JSON objects")

            entry_id = row.get("entry_id")
            received_time_utc = row.get("received_time_utc")
            subject = row.get("subject", "")

            if not isinstance(entry_id, str) or not entry_id.strip():
                raise ValueError("entry_id is required and must be a non-empty string")
            if not isinstance(received_time_utc, str) or not received_time_utc.strip():
                raise ValueError("received_time_utc is required and must be an ISO timestamp string")
            if not isinstance(subject, str):
                raise ValueError("subject must be a string")

            parsed_time = datetime.fromisoformat(received_time_utc)
            if parsed_time.tzinfo is None:
                raise ValueError("received_time_utc must include timezone info")

            messages.append(
                EmailMessage(
                    entry_id=entry_id,
                    received_time_utc=parsed_time,
                    subject=subject,
                )
            )

        return messages
