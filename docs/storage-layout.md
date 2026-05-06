# Run Artifact Storage Layout (Normative)

This document defines the canonical filesystem contract for run artifacts, recovery inputs, and idempotency markers.

## 1) Root directories
Implementations must use configured roots (see `docs/architecture.md`) and create these subtrees:

- `run_artifact_root/<workflow_id>/<run_id>/`
- `backup_root/<workflow_id>/<run_id>/`

## 2) Required file layout per run
Under `run_artifact_root/<workflow_id>/<run_id>/`:

- `run_metadata.json`
- `mail_outcomes.jsonl`
- `staged_write_plan.json`
- `target_probes.jsonl`
- `print_plan.json`
- `print_annotation_checklist.json`
- `print_annotation_checklist.html`
- `print_markers/` (one marker per print group)
- `mail_move_markers/` (one marker per mail move)
- `discrepancies.jsonl`
- `logs/` (structured log fragments if enabled)

Under `backup_root/<workflow_id>/<run_id>/`:

- `master_workbook_backup.xlsx`
- `backup_hash.txt` (lowercase hex SHA-256)

## 3) Naming and identity rules
- `run_id` must be globally unique and timestamp-bearing.
- Marker filenames must include stable operation ids:
  - print marker: `<print_group_operation_id>.json`
  - mail move marker: `<mail_move_operation_id>.json`
- Operation ids must be deterministic from workflow contracts.

## 4) Atomic persistence contract
For every JSON/marker artifact write:
1. write to sibling temp file (same directory)
2. flush and fsync temp file
3. atomic rename to target filename
4. optionally fsync parent directory when supported

Partial/truncated files are invalid artifacts and must trigger hard-block during recovery.

## 5) Retention policy
- Keep all run artifacts for at least 90 days.
- Never purge runs with terminal state `uncertain_not_committed` or unresolved hard-block recovery flags.
- Purge jobs must emit a deletion audit log containing run id, workflow id, and deleted paths.

## 6) Corruption/missing artifact behavior
If any required recovery artifact is missing, unreadable, hash-invalid, or malformed:
- recovery outcome must be hard-block
- no workbook write may start
- discrepancy report must include exact missing/invalid paths and validation failures

## 7) Hash validation requirements
Recovery loaders must validate:
- `hash_algorithm == "sha256"`
- expected hash string format (`^[a-f0-9]{64}$`)
- computed hash equality for backup file and staged write plan canonical serialization

Any mismatch is a hard-block outcome.
