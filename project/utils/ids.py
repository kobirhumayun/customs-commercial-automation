from __future__ import annotations

from datetime import UTC

from project.models.enums import WorkflowId
from project.utils.hashing import sha256_hex_text
from project.utils.time import utc_now


def build_run_id(workflow_id: WorkflowId) -> str:
    now = utc_now()
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    suffix = sha256_hex_text(f"{workflow_id.value}|{now.astimezone(UTC).isoformat()}")[:8]
    return f"run-{timestamp}-{workflow_id.value}-{suffix}"


def build_mail_id(entry_id: str) -> str:
    return f"mail-{sha256_hex_text(entry_id)[:16]}"


def build_attachment_id(mail_id: str, attachment_index: int, normalized_filename: str) -> str:
    return sha256_hex_text(f"{mail_id}|{attachment_index}|{normalized_filename}")


def build_saved_document_id(mail_id: str, normalized_filename: str, destination_path: str) -> str:
    return sha256_hex_text(f"{mail_id}|{normalized_filename}|{destination_path}")


def build_write_operation_id(
    run_id: str,
    mail_id: str,
    operation_index_within_mail: int,
    sheet_name: str,
    row_index: int,
    column_key: str,
) -> str:
    material = f"{run_id}|{mail_id}|{operation_index_within_mail}|{sheet_name}|{row_index}|{column_key}"
    return sha256_hex_text(material)


def build_print_group_id(run_id: str, mail_id: str, print_group_index: int) -> str:
    return sha256_hex_text(f"{run_id}|{mail_id}|{print_group_index}")


def build_print_completion_marker_id(
    run_id: str,
    mail_id: str,
    print_group_index: int,
    document_path_hashes: list[str],
) -> str:
    joined_hashes = "|".join(document_path_hashes)
    return sha256_hex_text(f"{run_id}|{mail_id}|{print_group_index}|{joined_hashes}")


def build_mail_move_operation_id(run_id: str, entry_id: str, destination_folder: str) -> str:
    return sha256_hex_text(f"{run_id}|{entry_id}|{destination_folder}")
