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

### Canonical enum sets (normative)
- `final_decision`: `pass` | `warning` | `hard_block`
- `write_phase_status`: `not_started` | `prevalidating_targets` | `prevalidated` | `applying` | `hard_blocked_no_write` | `uncertain_not_committed` | `committed`
- `print_phase_status`: `not_started` | `planned` | `printing` | `completed` | `hard_blocked` | `uncertain_incomplete`
- `mail_move_phase_status`: `not_started` | `moving` | `completed` | `hard_blocked` | `uncertain_incomplete`

Any undeclared enum value is schema-invalid and must be treated as a hard-block recovery/reporting condition.

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
- `ud_selection` (object; required for `ud_ip_exp` mails that reach UD allocation, optional/null otherwise)

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

### UD/IP/EXP unresolved IP/EXP policy details
When `code` is `ip_exp_policy_unresolved`, `details` must include:
- `run_id`
- `mail_id`
- `sheet_name`
- `target_column_key`
- `target_column_index`
- `target_row_indexes`
- `proposed_shared_column_value`
- `documents`
- `unresolved_policies`

Each `documents[]` item should include:
- `document_kind`
- `document_number`
- `document_date`
- `lc_sc_number`
- `lc_sc_date`
- `lc_sc_value`
- `quantity`
- `quantity_unit`
- `quantity_by_unit`
- `source_saved_document_id`

### UD/IP/EXP selection object
When `ud_selection` is present for `ud_ip_exp`, it must include:
- `required_quantity`
- `quantity_unit`
- `candidate_count`
- `candidates`
- `final_decision` (`selected` | `already_recorded` | `hard_block` | `hard_block_tie`)
- `final_decision_reason`
- `selected_candidate_id` (nullable)
- `discrepancy_code` (nullable)

Each `candidates[]` item should include:
- `candidate_id`
- `row_indexes`
- `matched_quantities`
- `quantity_sum`
- `ignored_excess_quantity`
- `score_keys`
- `prewrite_blank_targets_count`
- `prewrite_nonblank_optional_count`
- `selected`
- `rejection_reason`

Structured UD candidates may also emit `score_keys` entries for `lc_sc_value`, `workbook_value_sum`, `ud_quantity_by_unit`, and `workbook_quantity_by_unit`.

## 5) Print-annotation checklist schema
- `schema_id`: `print_annotation_checklist`
- `schema_version`: `1.0.0`

### Required fields
- `run_id`
- `workflow_id`
- `generated_at_utc`
- `print_group_order`
- `checklist_row_count`
- `rows`

Each `rows[]` item must include:
- `print_sequence`
- `print_group_id`
- `mail_id`
- `workflow_id`
- `ud_or_amendment_no`
- `sl_no_values`
- `mail_subject`
- `document_filename`
- `saved_document_id`
- `document_path_hash`
- `row_indexes`

The checklist JSON must be derived from the same persisted print plan that drives physical print order.
For `ud_ip_exp`, any mismatch between the current print plan and the persisted checklist JSON is a hard-block before print execution.

## 6) Recovery/idempotency artifact schema
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

## 7) Minimal examples

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
