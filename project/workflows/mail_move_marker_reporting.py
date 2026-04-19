from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def summarize_mail_move_markers(*, mail_move_markers_dir: Path) -> dict[str, Any]:
    if not mail_move_markers_dir.exists():
        return {
            "mail_move_markers_dir": str(mail_move_markers_dir),
            "marker_count": 0,
            "markers": [],
        }

    markers = []
    for path in sorted(mail_move_markers_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        receipt = payload.get("move_execution_receipt") if isinstance(payload, dict) else None
        markers.append(
            {
                "path": str(path),
                "mail_move_operation_id": payload.get("mail_move_operation_id"),
                "mail_id": payload.get("mail_id"),
                "entry_id": payload.get("entry_id"),
                "source_folder": payload.get("source_folder"),
                "destination_folder": payload.get("destination_folder"),
                "move_status": payload.get("move_status"),
                "moved_at_utc": payload.get("moved_at_utc"),
                "manual_verification_summary": payload.get("manual_verification_summary"),
                "write_disposition": payload.get("write_disposition"),
                "mail_move_policy_reason": payload.get("mail_move_policy_reason"),
                "adapter_name": receipt.get("adapter_name") if isinstance(receipt, dict) else None,
                "acknowledgment_mode": (
                    receipt.get("acknowledgment_mode") if isinstance(receipt, dict) else None
                ),
                "acknowledged_source_folder": (
                    receipt.get("acknowledged_source_folder") if isinstance(receipt, dict) else None
                ),
                "acknowledged_destination_folder": (
                    receipt.get("acknowledged_destination_folder") if isinstance(receipt, dict) else None
                ),
                "receipt": receipt,
            }
        )

    return {
        "mail_move_markers_dir": str(mail_move_markers_dir),
        "marker_count": len(markers),
        "markers": markers,
    }
