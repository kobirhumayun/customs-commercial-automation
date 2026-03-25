from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from customs_automation.core.hashing import sha256_hex_from_text


@dataclass(frozen=True, slots=True)
class StagedWriteOperation:
    mail_order_index: int
    operation_index_within_mail: int
    sheet_name: str
    row_index: int
    column_key: str
    expected_pre_write_value: str | None
    expected_post_write_value: str | None



def canonicalize_staged_write_plan(operations: list[StagedWriteOperation]) -> str:
    ordered = sorted(
        operations,
        key=lambda operation: (operation.mail_order_index, operation.operation_index_within_mail),
    )
    serialized = [asdict(operation) for operation in ordered]
    return json.dumps(serialized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_staged_write_plan_hash(operations: list[StagedWriteOperation]) -> str:
    return sha256_hex_from_text(canonicalize_staged_write_plan(operations))
