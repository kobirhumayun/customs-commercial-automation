# Discrepancy Code Catalog (Normative)

All emitted discrepancy codes must come from this catalog.
If an implementation needs a new code, update this document first.

## 1) Namespace and format
- Code format: `snake_case` lowercase ASCII.
- Codes must be stable and never silently renamed.
- If semantics change materially, create a new code and deprecate the old one.

## 2) Ownership and lifecycle
- Each code should map to a workflow scope (`shared`, `export_lc_sc`, `ud_ip_exp`, `import_btb_lc`, `bb_dashboard_verification`).
- Deprecated codes remain reserved and cannot be reused.
- Reports may continue to contain deprecated codes for historical runs.

## 3) Minimum payload requirements per discrepancy
Every discrepancy payload should include:
- `code`
- `severity`
- `message`
- `workflow_id`
- `rule_id` (nullable when not rule-derived)
- `details` object with machine-parseable evidence

## 4) Canonical code table

### Shared / recovery / write-state
| Code | Severity | Scope | Description |
|---|---|---|---|
| `missing_recovery_artifact` | `hard_block` | shared | One or more required recovery artifacts missing/unreadable. |
| `backup_hash_mismatch` | `hard_block` | shared | Backup hash does not match persisted `run_start_backup_hash`. |
| `staged_plan_hash_mismatch` | `hard_block` | shared | Canonical staged write plan hash mismatch. |
| `workbook_probe_unknown_state` | `hard_block` | shared | Probe produced `mismatch_unknown` for at least one target. |
| `metadata_probe_contradiction` | `hard_block` | shared | Persisted phase metadata contradicts probe evidence. |
| `mixed_target_probe_state` | `hard_block` | shared | Mixed `matches_pre_write`/`matches_post_write` targets. |
| `workbook_lock_conflict` | `hard_block` | shared | Workbook lock/contention detected before write. |
| `workbook_open_readonly` | `hard_block` | shared | Workbook opened read-only when write required. |
| `excel_adapter_unavailable` | `hard_block` | shared | Excel adapter unavailable after retry policy. |
| `workbook_save_conflict` | `hard_block` | shared | Save conflict during/after write application. |
| `invalid_phase_state_transition` | `hard_block` | shared | Attempted workflow phase-state transition outside allowed state machine. |
| `workbook_header_mapping_invalid` | `hard_block` | shared | Required workbook header missing/duplicated/ambiguous for canonical mapping. |
| `workbook_target_prevalidation_failed` | `hard_block` | shared | A staged workbook target failed live prevalidation before any write attempt. |
| `workbook_post_write_probe_mismatch` | `hard_block` | shared | A staged workbook target did not match its expected post-write value before commit marker creation. |
| `workbook_apply_runtime_error` | `hard_block` | shared | A runtime error occurred after write application began and before a commit marker could be created. |
| `print_adapter_unavailable` | `hard_block` | shared | Print adapter unavailable or not configured for the requested print execution path. |
| `print_marker_mismatch` | `hard_block` | shared | A persisted print completion marker conflicted with the planned print group identity. |
| `print_source_document_missing` | `hard_block` | shared | A planned print document path was missing at print execution time. |
| `print_group_runtime_error` | `hard_block` | shared | A runtime error interrupted print execution for a planned print group. |
| `mail_move_gate_unsatisfied` | `hard_block` | shared | Mail moves were attempted before required upstream phases reached terminal success. |
| `mail_move_marker_mismatch` | `hard_block` | shared | A persisted mail-move completion marker conflicted with the planned move identity. |
| `mail_source_location_mismatch` | `hard_block` | shared | A planned mail was no longer in the expected source folder and had no valid completion marker. |
| `mail_move_runtime_error` | `hard_block` | shared | A runtime error interrupted mail-move execution for a planned move operation. |
| `document_storage_path_unresolved` | `hard_block` | shared | Attachment storage path could not be resolved deterministically for the active mail. |
| `document_save_runtime_error` | `hard_block` | shared | A runtime error interrupted attachment saving before validation completed. |
| `mail_subject_missing` | `hard_block` | shared | A snapshotted mail is missing a usable subject. |
| `mail_sender_missing` | `hard_block` | shared | A snapshotted mail is missing a canonical sender address. |

### Export / import candidate ambiguity
| Code | Severity | Scope | Description |
|---|---|---|---|
| `export_candidate_tie_after_full_tiebreak` | `hard_block` | export_lc_sc | Export candidate resolution remained tied after all keys. |
| `export_subject_unparseable` | `hard_block` | export_lc_sc | Export mail subject could not be parsed into prefix, LC/SC number, buyer, and suffix context. |
| `export_file_number_missing` | `hard_block` | export_lc_sc | Export mail body did not yield any canonical file numbers. |
| `export_erp_row_missing` | `hard_block` | export_lc_sc | One or more extracted export file numbers did not resolve to a canonical ERP row. |
| `export_family_inconsistent` | `hard_block` | export_lc_sc | Resolved ERP rows for extracted file numbers did not belong to one LC/SC family. |
| `export_required_erp_field_missing` | `hard_block` | export_lc_sc | A canonical ERP row is missing one or more fields required for export workbook staging. |
| `export_subject_family_mismatch` | `hard_block` | export_lc_sc | Parsed export subject fields did not match the verified ERP family. |
| `import_candidate_tie_after_full_tiebreak` | `hard_block` | import_btb_lc | Import candidate resolution remained tied after all keys. |
| `attachment_classification_ambiguous` | `hard_block` | shared | Required attachment class could not be uniquely selected. |

### OCR / extraction quality
| Code | Severity | Scope | Description |
|---|---|---|---|
| `ocr_required_field_below_threshold` | `hard_block` | shared | Required OCR field confidence below workflow threshold. |
| `ocr_required_field_missing` | `hard_block` | shared | Required OCR field could not be extracted. |
| `ocr_non_required_field_low_confidence` | `warning` | shared | Non-required field low confidence with required fields valid. |

### UD/IP/EXP selection
| Code | Severity | Scope | Description |
|---|---|---|---|
| `ud_candidate_tie_after_full_tiebreak` | `hard_block` | ud_ip_exp | UD row-combination selection tied after all deterministic keys. |
| `ud_live_document_conflict` | `hard_block` | ud_ip_exp | Multiple live-derived UD attachments in one mail disagree on required UD evidence such as date or quantity, so deterministic processing is blocked. |
| `ud_file_number_missing` | `hard_block` | ud_ip_exp | UD/IP/EXP mail body did not yield any canonical file numbers for ERP family resolution. |
| `ud_erp_row_missing` | `hard_block` | ud_ip_exp | One or more extracted UD/IP/EXP file numbers did not resolve to a canonical ERP row. |
| `ud_family_inconsistent` | `hard_block` | ud_ip_exp | Resolved ERP rows for extracted UD/IP/EXP file numbers did not belong to one LC/SC family. |
| `ud_filename_lc_suffix_mismatch` | `hard_block` | ud_ip_exp | A UD/IP/EXP attachment filename explicitly carrying a `UD-LC-...` or `UD-SC-...` suffix does not agree with the ERP-derived LC/SC family suffix. |
| `ud_required_document_missing` | `hard_block` | ud_ip_exp | No UD document payload was available for deterministic UD processing. |
| `ud_required_field_missing` | `hard_block` | ud_ip_exp | A UD document payload is missing one or more mandatory extracted fields. |
| `ud_allocation_unresolved` | `hard_block` | ud_ip_exp | UD quantity allocation did not produce a selected candidate row combination. |
| `ud_lc_date_mismatch` | `hard_block` | ud_ip_exp | Structured UD/AM LC table date did not match the ERP LC/SC date. |
| `ud_lc_value_match_unresolved` | `hard_block` | ud_ip_exp | Structured UD/AM LC value did not identify a contiguous blank-UD workbook row group. |
| `ud_quantity_below_workbook` | `hard_block` | ud_ip_exp | Structured UD/AM supplier quantity was less than the selected workbook quantity for a unit. |
| `ud_quantity_excess_below_threshold` | `hard_block` | ud_ip_exp | Structured UD/AM supplier quantity exceeded workbook quantity by less than the required 50-unit threshold. |
| `ud_shared_column_nonblank_policy_unresolved` | `hard_block` | ud_ip_exp | Selected UD target row already has a non-blank shared-column value and append/duplicate behavior is not yet business-confirmed. |
| `ip_exp_policy_unresolved` | `hard_block` | ud_ip_exp | IP/EXP matching, date-column, total-check, or shared-column update policy remains unresolved, so staging is blocked with document evidence. |

## 5) Change-control checklist for new codes
A PR introducing new discrepancy code(s) must include:
1. code + severity + scope
2. rationale and triggering condition
3. required `details` fields
4. backward-compatibility/deprecation note
5. tests demonstrating emission path
