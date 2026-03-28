from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


def canonical_json_dumps(value: Any) -> str:
    return json.dumps(
        to_jsonable(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def pretty_json_dumps(value: Any) -> str:
    return json.dumps(
        to_jsonable(value),
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    ) + "\n"
