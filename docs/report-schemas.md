# Report Schemas (Normative)

All workflows must emit the canonical shared run, mail-outcome, discrepancy, checklist, and recovery JSON payloads using the schema contracts in this document.
All discrepancy `code` values must be sourced from `docs/discrepancy-codes.md`.

## 1) Schema versioning policy
- Every payload governed by this document includes `schema_id`, `schema_version`, and `report_schema_version`.
- `schema_id` remains a stable payload-type discriminator even when the artifact filename also identifies the payload.
- `schema_version` is a backward-compatible identity field consumed by finalized workflows. New payloads must keep it equal to `report_schema_version`; it must not be removed from finalized workflow outputs.
- `run_metadata.json` is the canonical persisted run-level JSON report artifact.
- `mail_outcomes.jsonl` is the canonical persisted mail-level JSON report stream, with one `mail_outcome_record` object per line. The internal `mail_report` projection is not a replacement for this persisted contract.
- Backward-compatible additive field changes increment **minor**.
- Breaking changes increment **major**.
- Patch changes are documentation/clarification only.
- Existing finalized workflows must continue emitting their current `1.0.0` identity/version fields until an explicitly approved schema upgrade. The initial `import_btb_lc` implementation must emit `schema_version=1.1.0` and `report_schema_version=1.1.0` because its launcher, relevance, and per-document outcome fields are additive structural extensions.
- Implementing `import_btb_lc` must not change the shared `REPORT_SCHEMA_VERSION` value used by finalized workflows. Import version selection must be workflow-scoped.

## 2) Run-level report schema
- `schema_id`: `run_report`
- Base finalized-workflow `schema_version` and `report_schema_version`: `1.0.0`
- `import_btb_lc` `schema_version` and `report_schema_version`: `1.1.0`

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
- `print_group_order` (array; must be empty when the workflow/launcher path has no print phase)
- `write_phase_status` (enum)
- `print_phase_status` (enum; finalized workflows preserve their established value semantics; `import_btb_lc` persists `completed` because it has no print phase)
- `mail_move_phase_status` (enum; finalized workflows preserve their established value semantics; `import_btb_lc` File Picker Path persists `completed` because it has no mail-move phase)
- `hash_algorithm` (`sha256`)
- `run_start_backup_hash` (64-char lowercase hex)
- `current_workbook_hash` (64-char lowercase hex)
- `staged_write_plan_hash` (64-char lowercase hex)
- `summary` (object with pass/warning/hard_block counts)

### `import_btb_lc` conditional run fields (`1.1.0`)
- `launcher_path` (`current_full` | `file_picker`; required)
- `import_keyword_revision` (string for `current_full`; null for `file_picker`)
- `import_relevance_summary` (object; required)
  - `candidate_count`
  - `not_applicable_count`

The shared `summary.pass`, `summary.warning`, and `summary.hard_block` counts cover actionable candidate mail units only. Subject-ineligible Current Full Path mails are counted under `import_relevance_summary.not_applicable_count`.

### Canonical enum sets (normative)
- `final_decision`: `pass` | `warning` | `hard_block`
- `write_phase_status`: `not_started` | `prevalidating_targets` | `prevalidated` | `applying` | `hard_blocked_no_write` | `uncertain_not_committed` | `committed`
- `print_phase_status`: `not_started` | `planned` | `printing` | `completed` | `hard_blocked` | `uncertain_incomplete`
- `mail_move_phase_status`: `not_started` | `moving` | `completed` | `hard_blocked` | `uncertain_incomplete`

Any undeclared enum value is schema-invalid and must be treated as a hard-block recovery/reporting condition.

## 3) Mail-level outcome schema
- Persisted `schema_id`: `mail_outcome_record`
- Internal non-persisted projection `schema_id`: `mail_report`
- Base finalized-workflow `schema_version` and `report_schema_version`: `1.0.0`
- `import_btb_lc` `schema_version` and `report_schema_version`: `1.1.0`

### Required fields
- `run_id` (string)
- `mail_id` (string)
- `workflow_id` (string)
- `snapshot_index` (integer)
- `processing_status` (`snapshotted` | `validation_pending` | `validated` | `blocked` | `staged_for_write` | `written` | `printed` | `moved`)
- `rule_pack_id` (string)
- `rule_pack_version` (string)
- `applied_rule_ids` (ordered array of strings)
- `final_decision` (`pass` | `warning` | `hard_block`; nullable only in the initialized pre-validation artifact)
- `decision_reasons` (array)
- `eligible_for_write` (boolean)
- `eligible_for_print` (boolean)
- `eligible_for_mail_move` (boolean)
- `source_entry_id` (string; Outlook `EntryID` or an import-scoped synthetic source identifier)
- `subject_raw` (string)
- `sender_address` (string)
- `file_numbers_extracted` (array; populated for `export_lc_sc` and `ud_ip_exp`, empty for `import_btb_lc`)
- `btb_lc_numbers_extracted` (array; required for `import_btb_lc`, optional/null otherwise)
- `pi_numbers_extracted` (array; required for `import_btb_lc`, optional/null otherwise)
- `related_export_lc_numbers_extracted` (array; required for `import_btb_lc`, optional/null otherwise)
- `saved_documents` (array)
- `staged_write_operations` (array)
- `discrepancies` (array)
- `import_keyword_revision` (string; required for `import_btb_lc` Current Full Path, optional/null for `import_btb_lc` File Picker Path and non-import workflows)
- `ud_selection` (object; required for `ud_ip_exp` mails that reach UD allocation, optional/null otherwise)

### `import_btb_lc` conditional mail fields (`1.1.0`)
- `launcher_path` (`current_full` | `file_picker`; required)
- `processing_disposition` (`candidate` | `not_applicable`; required)
- `import_relevance` (object; required for `current_full`, null for `file_picker`)
- `import_document_outcomes` (array; required)

For a Current Full Path subject-ineligible mail:
- `processing_disposition = not_applicable`
- `final_decision = pass` for backward enum compatibility
- `import_document_outcomes = []`
- all extracted-identifier arrays are empty
- `saved_documents`, `staged_write_operations`, and `discrepancies` are empty
- no write, print, or mail-move operation id is present

`import_relevance` must include:
- `normalized_subject`
- `include_keyword_hits`
- `exclude_keyword_hits`
- `eligible`
- `import_keyword_revision`

Each `import_document_outcomes[]` item must include:
- `import_document_outcome_id` (SHA-256 of `mail_id`, source identity, and source file SHA-256)
- `mail_id`
- `saved_document_id` (nullable for File Picker Path when represented by direct source lineage)
- `source_path`
- `source_file_sha256`
- `source_filename`
- `attachment_index` (nullable for File Picker Path)
- `document_classification` (`import_btb_lc` | `non_import` | `ambiguous`)
- `document_processing_disposition` (`candidate` | `ignored_non_import` | `blocked`)
- `storage_decision` (`source_evidence_only` | `promoted_new` | `reused_existing_same_hash` | `file_picker_existing` | `blocked_filename_content_conflict`)
- `canonical_storage_path` (string or null)
- `extraction_page_limit` (must be `3`)
- `btb_lc_number_raw` (nullable when unavailable)
- `btb_lc_number` (nullable when invalid/unavailable)
- `btb_lc_date_raw` (nullable when unavailable)
- `btb_lc_date` (ISO `YYYY-MM-DD`, nullable when invalid/unavailable)
- `btb_lc_value_raw` (nullable when unavailable)
- `btb_lc_value` (canonical decimal string, nullable when invalid/unavailable)
- `currency_raw` (nullable when unavailable)
- `currency` (nullable when invalid/unavailable)
- `pi_numbers_raw` (ordered array; empty when unavailable)
- `pi_numbers` (ordered canonical array; empty when invalid/unavailable)
- `pi_register_validation` (object; ERP PI register aggregation evidence, including per-PI row indexes, aggregated PI total amount, aggregated PI quantity kg, and exact-match decision)
- `related_export_lc_raw` (nullable when unavailable)
- `related_export_lc` (nullable when invalid/unavailable)
- `field_provenance` (object keyed by extracted field)
- `filename_match` (boolean or null when no valid BTB number is available)
- `duplicate_classification` (`none` | `workbook_exact` | `same_mail_exact` | `same_run_exact` | `conflict`)
- `duplicate_evidence` (object or null)
- `candidate_rows` (ordered array)
- `selected_sl_no` (string or null; resolved from the workbook `SL.No.` column and treated as text, not inferred from row order)
- `selected_row_index` (integer or null; audit trace only)
- `selected_quantity_kgs` (canonical decimal string or null; sourced only from the aggregated ERP import PI register quantity after exact value match)
- `allocation_attempts` (ordered array; empty for duplicates or pre-allocation hard blocks)
- `document_decision` (`pass` | `warning` | `hard_block`)
- `warning_codes` (array)
- `discrepancy_codes` (array containing every emitted warning or hard-block code; `warning_codes` is its warning-only subset)
- `staged_write_operation_ids` (array)

Each `candidate_rows[]` item must include:
- `row_index`
- `sl_no` when available
- `canonical_export_lc`
- `up_no_blank`
- `btb_lc_no_blank`
- `btb_lc_issue_date_blank`
- `import_amount_blank`
- `quantity_kgs_blank`
- `target_cells_blank_at_evaluation` (boolean; true only when all four import destination cells are blank)
- `row_reserved_in_run`
- `export_amount`
- `lower_bound_40_percent`
- `upper_bound_80_percent`
- `value_eligible`
- `rejection_reasons`
- `selected`

Each `allocation_attempts[]` item must include:
- `attempt_index`
- `candidate_rows`
- `tentative_selected_row_index`
- `reservation_released` (normally `false`; retained for backward-compatible audit payloads)
- `restart_reason` (nullable; retained for backward compatibility)

For every staged `import_btb_lc` write operation:
- `column_key` must identify `btb_lc_no`, `btb_lc_issue_date`, `import_amount`, or `quantity_kgs`; no other workbook column is writable by this workflow
- `expected_pre_write_value` must be canonical blank
- live target-prevalidation evidence must record the observed value for each destination cell
- `import_target_cell_already_populated` details must include `sheet_name`, `row_index`, `column_key`, `expected_pre_write_value`, and `observed_value`
- a populated target discovered after staging must produce zero applied workbook mutations for the complete atomic batch

When `duplicate_classification` is `same_mail_exact` or `same_run_exact`, `duplicate_evidence` must include the primary `mail_id` and `import_document_outcome_id`. The primary must be an accepted non-hard-block document; a hard-blocked parent mail does not invalidate a primary document that independently passed.

The legacy `btb_lc_numbers_extracted`, `pi_numbers_extracted`, and `related_export_lc_numbers_extracted` arrays are ordered projections from `import_document_outcomes`. They must never be zipped together or treated as the authoritative document relationship model.
Import BTB LC HTML rendering may apply display-only formatting: BTB LC dates use `DD/MM/YYYY`, generated timestamps use the configured state timezone, and Related Export LC values omit a leading `LC-` prefix. The canonical JSON fields remain unchanged. Each document row must render every available extracted or derived value even for a hard-block outcome: bank, BTB LC number/date/value/currency, ordered seller PI numbers, ERP aggregated PI amount and quantity, related export LC, candidate-row evidence, selected `SL.No.`, write disposition, decision reasons, warnings, and discrepancies. A later failure must not suppress values established by an earlier successful extraction or ERP-validation phase.

For `document_processing_disposition=candidate`, any null canonical required field must correspond to a document-level hard-block discrepancy explaining why the value was unavailable or invalid. Deterministic `ignored_non_import` records may keep import extraction fields null without a document discrepancy.
`candidate_rows` represents the decisive/final allocation attempt; `allocation_attempts` remains a backward-compatible ordered audit collection when more than one attempt exists.

## 4) Discrepancy report schema
- `schema_id`: `discrepancy_report`
- Base finalized-workflow `schema_version` and `report_schema_version`: `1.0.0`
- `import_btb_lc` `schema_version` and `report_schema_version`: `1.1.0`

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

### Import discrepancy evidence
Import document-scoped discrepancies must include:
- `import_document_outcome_id`
- `source_path`
- `source_file_sha256`
- extracted raw/canonical values relevant to the failure

Candidate/duplicate discrepancies must additionally include the ordered workbook row evidence used for the decision. Storage discrepancies must include source and destination paths plus both hashes when both files exist.

### UD/IP/EXP historical legacy discrepancy details
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

This code is retained only for backward compatibility with older run artifacts created before the conservative phase-1 IP/EXP path was documented and implemented. New phase-1 IP/EXP staging should emit the current mail-shape, required-field, family-row, duplicate-only, or target-row conflict discrepancy codes instead.

### UD/IP/EXP selection object
When `ud_selection` is present for `ud_ip_exp`, it must include:
- `required_quantity`
- `quantity_unit`
- `candidate_count`
- `reported_candidate_count`
- `candidates_truncated`
- `omitted_candidate_count`
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
When `candidate_count` is large, the persisted `candidates[]` array may be a bounded deterministic subset instead of the full exact candidate universe.
In that case:
- `candidate_count` remains the full exact total
- `reported_candidate_count` equals `len(candidates[])`
- `candidates_truncated = true`
- `omitted_candidate_count = candidate_count - reported_candidate_count`

For dense structured UD matches, the selected candidate must still be present in `candidates[]` even when truncation is active.

## 5) Print-annotation checklist schema
- `schema_id`: `print_annotation_checklist`
- `schema_version`: `1.0.0`
- `report_schema_version`: `1.0.0`

### Required fields
- `run_id`
- `workflow_id`
- `generated_at_utc`
- `print_group_order`
- `checklist_row_count`
- `rows`

Each `rows[]` item must include these shared fields:
- `print_sequence`
- `print_group_id`
- `mail_id`
- `workflow_id`
- `sl_no_values`
- `mail_subject`
- `document_filename`
- `row_indexes`

`ud_ip_exp` checklist rows must also include:
- `ud_or_amendment_no`
- `lc_sc`
- `bangladesh_bank_ref`
- `ud_amendment_lc_value`
- `saved_document_id`
- `document_path_hash`

`export_lc_sc` checklist rows must also include:
- `lc_sc`
- `bangladesh_bank_ref`
- `document_path_hashes`
- `workbook_values`

`export_lc_sc` may additionally include:
- `saved_document_ids`
- `document_filenames`
- `mail_group_row_count`
- `mail_group_first_row`

The checklist JSON must be derived from the same persisted print plan that drives physical print order.
For `ud_ip_exp`, checklist rows are required only for printed UD/Amendment documents that resolve to workbook row-selection evidence; other newly saved PDFs may still be printed without checklist rows.
For `ud_ip_exp`, any mismatch between the current print plan's checklist-required document subset and the persisted checklist JSON is a hard-block before print execution.
For `export_lc_sc`, checklist generation is also a mandatory pre-print gate. Its rows are workbook-row-oriented rather than UD-document-oriented, and workbook-driven export values may be carried in a workflow-specific `workbook_columns` definition plus per-row `workbook_values`.
For `export_lc_sc`, when one mail spans multiple workbook rows, the persisted row payload may include mail-group metadata such as row-span count and one newline-delimited `document_filename` string representing all printed filenames for that mail.

### Related persisted print-plan evidence
When `print_plan.json` includes `annotation_documents` for a print group, each item should preserve direct checklist-source evidence in document print order:
- `saved_document_id`
- `document_path`
- `document_path_hash`
- `document_filename`
- `document_number`
- `ud_amendment_lc_value` for `ud_ip_exp` UD/Amendment checklist rows
- `row_indexes`
- `checklist_required`

For `ud_ip_exp`, checklist generation should prefer these persisted `annotation_documents` records and use older mail-level reconstruction fallbacks only for backward compatibility with older run artifacts.

## 6) Recovery/idempotency artifact schema
- `schema_id`: `recovery_artifact`
- `schema_version`: `1.0.0`
- `report_schema_version`: `1.0.0`

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
- `print_completion_markers` (array of marker ids; empty when no print phase applies)
- `mail_move_completion_markers` (array of marker ids; empty when no mail-move phase applies)

## 7) Minimal examples

### Run report (minimal)
```json
{
  "schema_id": "run_report",
  "schema_version": "1.0.0",
  "report_schema_version": "1.0.0",
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

### Mail outcome record (full shape sketch)
```json
{
  "schema_id": "mail_outcome_record",
  "schema_version": "1.1.0",
  "report_schema_version": "1.1.0",
  "run_id": "run-2026-03-24T09-30-00Z",
  "mail_id": "00000000A1B2C3D4",
  "workflow_id": "import_btb_lc",
  "snapshot_index": 0,
  "processing_status": "validated",
  "rule_pack_id": "import_btb_lc.default",
  "rule_pack_version": "1.2.0",
  "applied_rule_ids": ["core.x", "import.y"],
  "final_decision": "warning",
  "decision_reasons": [
    "attachment filename did not match extracted BTB LC number",
    "BTB LC was already recorded in the workbook with matching export LC and amount"
  ],
  "eligible_for_write": false,
  "eligible_for_print": false,
  "eligible_for_mail_move": true,
  "source_entry_id": "00000000A1B2C3D4",
  "subject_raw": "Fabric BTB LC",
  "sender_address": "sender@example.com",
  "file_numbers_extracted": [],
  "btb_lc_numbers_extracted": ["0742123456789"],
  "pi_numbers_extracted": ["BTL/26/0042"],
  "related_export_lc_numbers_extracted": ["LC-1234-2026"],
  "saved_documents": [{"path": "C:/.../scan-001.pdf"}],
  "staged_write_operations": [],
  "discrepancies": [
    {"code": "import_filename_number_mismatch"},
    {"code": "import_duplicate_document_in_workbook"}
  ],
  "import_keyword_revision": "2026-03-24.1",
  "launcher_path": "current_full",
  "processing_disposition": "candidate",
  "import_relevance": {
    "normalized_subject": "fabric btb lc",
    "include_keyword_hits": ["fabric"],
    "exclude_keyword_hits": [],
    "eligible": true,
    "import_keyword_revision": "2026-03-24.1"
  },
  "import_document_outcomes": [
    {
      "import_document_outcome_id": "impdoc-a1b2",
      "mail_id": "00000000A1B2C3D4",
      "saved_document_id": "saveddoc-a1b2",
      "source_path": "C:/.../scan-001.pdf",
      "source_file_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "source_filename": "scan-001.pdf",
      "attachment_index": 0,
      "document_classification": "import_btb_lc",
      "document_processing_disposition": "candidate",
      "storage_decision": "reused_existing_same_hash",
      "canonical_storage_path": "C:/customs-automation/import-documents/2026/scan-001.pdf",
      "extraction_page_limit": 3,
      "btb_lc_number_raw": "0742123456789",
      "btb_lc_number": "0742123456789",
      "btb_lc_date_raw": "24/03/2026",
      "btb_lc_date": "2026-03-24",
      "btb_lc_value_raw": "USD 50,000.00",
      "btb_lc_value": "50000.00",
      "currency_raw": "USD",
      "currency": "USD",
      "pi_numbers_raw": ["BTL/26/0042"],
      "pi_numbers": ["BTL/26/0042"],
      "related_export_lc_raw": "LC-1234-2026",
      "related_export_lc": "LC-1234-2026",
      "field_provenance": {},
      "filename_match": false,
      "duplicate_classification": "workbook_exact",
      "duplicate_evidence": {
        "row_index": 12,
        "related_export_lc": "LC-1234-2026",
        "import_amount": "50000.00"
      },
      "candidate_rows": [],
      "selected_sl_no": null,
      "selected_row_index": null,
      "allocation_attempts": [],
      "document_decision": "warning",
      "warning_codes": [
        "import_filename_number_mismatch",
        "import_duplicate_document_in_workbook"
      ],
      "discrepancy_codes": [
        "import_filename_number_mismatch",
        "import_duplicate_document_in_workbook"
      ],
      "staged_write_operation_ids": []
    }
  ]
}
```
