from __future__ import annotations

from typing import Any


def build_transport_execution_report(
    *,
    print_marker_summary: dict[str, Any],
    mail_move_marker_summary: dict[str, Any],
) -> dict[str, Any]:
    print_markers = list(print_marker_summary.get("markers", []))
    mail_move_markers = list(mail_move_marker_summary.get("markers", []))

    print_adapter_names = sorted(
        {
            str(marker.get("adapter_name", "")).strip()
            for marker in print_markers
            if str(marker.get("adapter_name", "")).strip()
        }
    )
    mail_move_adapter_names = sorted(
        {
            str(marker.get("adapter_name", "")).strip()
            for marker in mail_move_markers
            if str(marker.get("adapter_name", "")).strip()
        }
    )
    manual_verification_visible_count = sum(
        1 for marker in print_markers + mail_move_markers if isinstance(marker.get("manual_verification_summary"), dict)
    )
    duplicate_only_move_count = sum(
        1
        for marker in mail_move_markers
        if str(marker.get("write_disposition", "")).strip() == "duplicate_only_noop"
    )

    return {
        "summary_counts": {
            "print_marker_count": int(print_marker_summary.get("marker_count", 0)),
            "mail_move_marker_count": int(mail_move_marker_summary.get("marker_count", 0)),
            "print_adapter_count": len(print_adapter_names),
            "mail_move_adapter_count": len(mail_move_adapter_names),
            "manual_verification_visible_count": manual_verification_visible_count,
            "duplicate_only_mail_move_count": duplicate_only_move_count,
        },
        "print_execution": print_marker_summary,
        "mail_move_execution": mail_move_marker_summary,
        "adapter_summary": {
            "print_adapters": print_adapter_names,
            "mail_move_adapters": mail_move_adapter_names,
        },
    }
