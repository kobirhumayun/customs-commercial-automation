from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def summarize_print_markers(*, print_markers_dir: Path) -> dict[str, Any]:
    if not print_markers_dir.exists():
        return {
            "print_markers_dir": str(print_markers_dir),
            "marker_count": 0,
            "markers": [],
        }

    markers = []
    for path in sorted(print_markers_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        receipt = payload.get("print_execution_receipt") if isinstance(payload, dict) else None
        markers.append(
            {
                "path": str(path),
                "print_group_id": payload.get("print_group_id"),
                "mail_id": payload.get("mail_id"),
                "completion_marker_id": payload.get("completion_marker_id"),
                "print_status": payload.get("print_status") or "completed",
                "printed_at_utc": payload.get("printed_at_utc"),
                "printed_document_count": (
                    len(payload.get("printed_document_path_hashes", []))
                    if isinstance(payload.get("printed_document_path_hashes"), list)
                    else 0
                ),
                "total_document_count": (
                    len(payload.get("document_path_hashes", []))
                    if isinstance(payload.get("document_path_hashes"), list)
                    else 0
                ),
                "blank_separator_printed": bool(payload.get("blank_separator_printed", False)),
                "manual_verification_summary": payload.get("manual_verification_summary"),
                "adapter_name": receipt.get("adapter_name") if isinstance(receipt, dict) else None,
                "acknowledgment_mode": (
                    receipt.get("acknowledgment_mode") if isinstance(receipt, dict) else None
                ),
                "executed_command_count": (
                    int(receipt.get("executed_command_count", 0)) if isinstance(receipt, dict) else 0
                ),
                "receipt": receipt,
            }
        )

    return {
        "print_markers_dir": str(print_markers_dir),
        "marker_count": len(markers),
        "markers": markers,
    }
