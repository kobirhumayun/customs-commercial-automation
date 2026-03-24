# Report Schemas (Normative)

All workflows must emit versioned JSON payloads using the schema contracts in this document.
All discrepancy `code` values must be sourced from `docs/discrepancy-codes.md`.

## 1) Schema versioning policy
- Every payload includes `schema_id` and `schema_version`.
- Backward-compatible additive field changes increment **minor**.
- Breaking changes increment **major**.
- Patch changes are documentation/clarification only.

## 2) Run-level report schema
- `schema_id`: `run_report`
- `schema_version`: `1.0.0`

### Required fields
- `run_id` (string)
- `workflow_id` (string)
- `tool_version` (string)
- `rule_pack_id` (string)
- `rule_pack_version` (string)
- `started_at_utc` (ISO-8601 UTC)
- `completed_at_utc` (ISO-8601 UTC or null if incomplete)
- `state_timezone` (IANA timezone string)
- `mail_iteration_order` (array of mail ids in processing order)
- `print_group_order` (array; may be empty)
- `write_phase_status` (enum)
- `print_phase_status` (enum)
- `mail_move_phase_status` (enum)
- `hash_algorithm` (`sha256`)
- `run_start_backup_hash` (64-char lowercase hex)
- `current_workbook_hash` (64-char lowercase hex)
- `staged_write_plan_hash` (64-char lowercase hex)
- `summary` (object with pass/warning/hard_block counts)

## 3) Mail-level report schema
- `schema_id`: `mail_report`
- `schema_version`: `1.0.0`

### Required fields
- `run_id` (string)
- `mail_id` (string)
- `workflow_id` (string)
- `rule_pack_id` (string)
- `rule_pack_version` (string)
- `applied_rule_ids` (ordered array of strings)
- `final_decision` (`pass` | `warning` | `hard_block`)
- `decision_reasons` (array)
- `file_numbers_extracted` (array)
- `saved_documents` (array)
- `staged_write_operations` (array)
- `discrepancies` (array)
- `import_keyword_revision` (string; required for import workflow, optional/null otherwise)

## 4) Discrepancy report schema
- `schema_id`: `discrepancy_report`
- `schema_version`: `1.0.0`

### Required fields
- `run_id`
- `mail_id` (nullable for run-scoped issues)
- `workflow_id`
- `severity` (`warning` | `hard_block`)
- `code` (stable machine-readable string)
- `message` (human-readable string)
- `rule_id` (nullable if not rule-derived)
- `details` (object)
- `created_at_utc` (ISO-8601 UTC)

### Discrepancy code requirements
- `code` must exist in `docs/discrepancy-codes.md`.
- `severity` must match the catalog entry for that code.
- If `rule_id` is null, `details` must include `non_rule_source` describing the emitting subsystem.

## 5) Recovery/idempotency artifact schema
- `schema_id`: `recovery_artifact`
- `schema_version`: `1.0.0`

### Required fields
- `run_id`
- `workflow_id`
- `write_phase_status`
- `print_phase_status`
- `mail_move_phase_status`
- `hash_algorithm`
- `run_start_backup_hash`
- `current_workbook_hash`
- `staged_write_plan_hash`
- `post_write_probe_summary`
- `print_completion_markers` (array of marker ids)
- `mail_move_completion_markers` (array of marker ids)

## 6) Minimal examples

### Run report (minimal)
```json
{
  "schema_id": "run_report",
  "schema_version": "1.0.0",
  "run_id": "run-2026-03-24T09-30-00Z",
  "workflow_id": "export_lc_sc",
  "tool_version": "0.1.0",
  "rule_pack_id": "export_lc_sc.default",
  "rule_pack_version": "1.4.0",
  "started_at_utc": "2026-03-24T09:30:00Z",
  "completed_at_utc": null,
  "state_timezone": "Asia/Dhaka",
  "mail_iteration_order": [],
  "print_group_order": [],
  "write_phase_status": "not_started",
  "print_phase_status": "not_started",
  "mail_move_phase_status": "not_started",
  "hash_algorithm": "sha256",
  "run_start_backup_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "current_workbook_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "staged_write_plan_hash": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "summary": {"pass": 0, "warning": 0, "hard_block": 0}
}
```

### Mail report (full shape sketch)
```json
{
  "schema_id": "mail_report",
  "schema_version": "1.0.0",
  "run_id": "run-2026-03-24T09-30-00Z",
  "mail_id": "00000000A1B2C3D4",
  "workflow_id": "import_btb_lc",
  "rule_pack_id": "import_btb_lc.default",
  "rule_pack_version": "1.2.0",
  "applied_rule_ids": ["core.x", "import.y"],
  "final_decision": "warning",
  "decision_reasons": ["cosmetic subject token mismatch"],
  "file_numbers_extracted": ["P/26/0042"],
  "saved_documents": [{"path": "C:/.../doc1.pdf"}],
  "staged_write_operations": [],
  "discrepancies": [{"code": "subject_token_variation"}],
  "import_keyword_revision": "2026-03-24.1"
}
```
