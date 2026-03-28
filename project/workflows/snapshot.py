from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from project.models import EmailMessage
from project.utils.ids import build_mail_id
from project.utils.time import utc_timestamp, validate_timezone


@dataclass(slots=True, frozen=True)
class SourceEmailRecord:
    entry_id: str
    received_time: str
    subject_raw: str
    sender_address: str
    body_text: str = ""


def load_snapshot_manifest(path: Path) -> list[SourceEmailRecord]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, dict):
        raw_messages = payload.get("messages")
    else:
        raw_messages = payload

    if not isinstance(raw_messages, list):
        raise ValueError("Snapshot manifest must be a JSON array or an object with a 'messages' array")

    records: list[SourceEmailRecord] = []
    for index, item in enumerate(raw_messages):
        if not isinstance(item, dict):
            raise ValueError(f"Snapshot message at index {index} must be a JSON object")
        entry_id = _require_non_empty_string(item, "entry_id", index)
        received_time = _require_non_empty_string(item, "received_time", index)
        subject_raw = _require_string(item, "subject_raw", index)
        sender_address = _require_string(item, "sender_address", index)
        body_text = _optional_string(item.get("body_text"))
        records.append(
            SourceEmailRecord(
                entry_id=entry_id,
                received_time=received_time,
                subject_raw=subject_raw,
                sender_address=sender_address,
                body_text=body_text,
            )
        )
    return records


def build_email_snapshot(
    source_messages: list[SourceEmailRecord],
    *,
    state_timezone: str,
) -> list[EmailMessage]:
    workflow_timezone = validate_timezone(state_timezone)
    ordered = sorted(
        source_messages,
        key=lambda item: (
            _parse_received_time(item.received_time).astimezone(workflow_timezone),
            item.entry_id,
        ),
    )

    snapshot: list[EmailMessage] = []
    for snapshot_index, message in enumerate(ordered):
        received_at = _parse_received_time(message.received_time)
        snapshot.append(
            EmailMessage(
                mail_id=build_mail_id(message.entry_id),
                entry_id=message.entry_id,
                received_time_utc=utc_timestamp(received_at),
                received_time_workflow_tz=received_at.astimezone(workflow_timezone)
                .replace(microsecond=0)
                .isoformat(),
                subject_raw=message.subject_raw,
                sender_address=message.sender_address,
                snapshot_index=snapshot_index,
                body_text=message.body_text,
            )
        )
    return snapshot


def _parse_received_time(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("received_time values must be timezone-aware ISO-8601 timestamps")
    return parsed


def _require_non_empty_string(item: dict[str, Any], key: str, index: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Snapshot message at index {index} is missing a non-empty '{key}'")
    return value


def _require_string(item: dict[str, Any], key: str, index: int) -> str:
    value = item.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Snapshot message at index {index} is missing a string '{key}'")
    return value


def _optional_string(value: object) -> str:
    return value if isinstance(value, str) else ""
