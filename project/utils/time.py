from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FALLBACK_TIMEZONES: dict[str, tzinfo] = {
    "UTC": UTC,
    "Asia/Dhaka": timezone(timedelta(hours=6), name="Asia/Dhaka"),
}


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def validate_timezone(timezone_name: str) -> tzinfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name in FALLBACK_TIMEZONES:
            return FALLBACK_TIMEZONES[timezone_name]
        raise


def utc_timestamp(value: datetime | None = None) -> str:
    current = value or utc_now()
    return current.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
