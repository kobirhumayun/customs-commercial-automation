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
| `print_annotation_generation_failed` | `hard_block` | shared | The mandatory pre-print annotation checklist could not be generated from the persisted run evidence. |
| `print_annotation_sl_no_unresolved` | `hard_block` | shared | One or more selected checklist target rows did not yield a readable workbook `SL.No.` value. |
| `print_annotation_checklist_missing_or_invalid` | `hard_block` | shared | The mandatory pre-print annotation checklist artifact was missing or did not match the active print plan. |
| `print_annotation_browser_open_failed` | `warning` | shared | The print-annotation checklist HTML was generated successfully, but the system could not open it automatically in the default browser. |
| `dashboard_report_browser_open_failed` | `warning` | shared | The dashboard verification HTML report was generated successfully, but the system could not open it automatically in the default browser. |
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
| `import_candidate_tie_after_full_tiebreak` | `hard_block` | import_btb_lc | Reserved for historical/defensive handling; normal import row allocation resolves a remaining value tie by lowest workbook row index. |
| `import_duplicate_document_same_mail` | `warning` | import_btb_lc | Repeated import BTB LC evidence within one mail matched exactly and was treated as duplicate-only with no additional write. |
| `import_duplicate_document_same_run` | `warning` | import_btb_lc | A later import BTB LC in the same run matched an earlier accepted import BTB LC exactly and was treated as duplicate-only with no additional write. |
| `import_duplicate_document_in_workbook` | `warning` | import_btb_lc | Exactly one workbook row already contained the same BTB LC number, related export LC, and import amount, so the document was handled as duplicate-only with no write. |
| `import_filename_number_mismatch` | `warning` | import_btb_lc | The import BTB LC attachment filename did not match the extracted BTB LC number, but all other required extraction and workbook-selection checks for that import BTB LC remained valid so processing continued. |
| `import_duplicate_document_conflict` | `hard_block` | import_btb_lc | Duplicate import BTB LC evidence conflicted on required extracted values or deterministic workbook implications. |
| `import_no_qualified_workbook_row` | `hard_block` | import_btb_lc | The extracted import BTB LC did not qualify any workbook row under the deterministic candidate rules. |
| `import_required_document_missing` | `hard_block` | import_btb_lc | No deterministic import BTB LC PDF could be extracted from the relevant import mail. |
| `import_btb_lc_number_invalid` | `hard_block` | import_btb_lc | Extracted BTB LC number was missing or did not match an approved bank-specific identifier shape. |
| `import_btb_lc_date_invalid` | `hard_block` | import_btb_lc | BTB LC date was missing, ambiguous, or not a valid calendar date. |
| `import_btb_lc_amount_invalid` | `hard_block` | import_btb_lc | BTB LC amount was missing, non-positive, ambiguous, or not a valid canonical decimal. |
| `import_currency_missing_or_mismatch` | `hard_block` | import_btb_lc | Extracted currency was missing or differed from configured `import_amount_currency`. |
| `import_pi_number_invalid` | `hard_block` | import_btb_lc | No seller PI was found, or at least one extracted seller PI-like value did not match an approved import PI pattern. |
| `import_pi_register_unavailable` | `hard_block` | import_btb_lc | The ERP import PI register could not be loaded, downloaded, or parsed for PI value/quantity validation. |
| `import_pi_register_row_missing` | `hard_block` | import_btb_lc | One or more extracted seller PI numbers were not present in the ERP import PI register. |
| `import_pi_register_amount_invalid` | `hard_block` | import_btb_lc | An ERP import PI register row contained an invalid PI amount or quantity value. |
| `import_pi_register_amount_mismatch` | `hard_block` | import_btb_lc | Aggregated ERP import PI value for the extracted seller PIs did not exactly match the extracted BTB LC value. |
| `import_related_export_lc_invalid` | `hard_block` | import_btb_lc | Related export LC was missing or failed canonical LC/SC normalization. |
| `import_workbook_duplicate_unverifiable` | `hard_block` | import_btb_lc | Workbook already contained the BTB LC number, but row count, related export LC, import amount, or populated BTB LC issue-date evidence could not prove one exact duplicate. |
| `import_workbook_candidate_invalid` | `hard_block` | import_btb_lc | A matching-family workbook row had an invalid required export amount or a partial BTB-number/import-amount target state. |
| `import_target_cell_already_populated` | `hard_block` | import_btb_lc | An import destination cell (`BTB L/C No.`, `BTB LC Issue Date`, import `Amount` column 22, or `Quantity (Kgs)`) was populated when a blank target was required for staging or live pre-write validation; the batch must stop before any workbook mutation. |
| `import_storage_filename_content_conflict` | `hard_block` | import_btb_lc | The destination filename already existed with different file content; the existing file was not overwritten. |
| `import_file_picker_source_invalid` | `hard_block` | import_btb_lc | A selected File Picker source was not a regular PDF beneath the configured import document root. |
| `import_report_browser_open_failed` | `warning` | import_btb_lc | The import BTB LC HTML report was generated successfully, but the system could not open it automatically in the default browser. |
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
| `ud_duplicate_document_same_mail` | `warning` | ud_ip_exp | Duplicate UD/AM evidence within one mail was deterministically ignored after matching by BGMEA number or duplicate filename evidence. |
| `ud_duplicate_document_same_run` | `warning` | ud_ip_exp | A later mail in the same run carried a UD/AM already staged by an earlier mail, so it was treated as duplicate-only with no new write. |
| `ud_live_document_conflict` | `hard_block` | ud_ip_exp | Multiple live-derived UD attachments in one mail disagree on required UD evidence such as date or quantity, so deterministic processing is blocked. |
| `ud_file_number_missing` | `hard_block` | ud_ip_exp | UD/IP/EXP mail body did not yield any canonical file numbers for ERP family resolution. |
| `ud_erp_row_missing` | `hard_block` | ud_ip_exp | One or more extracted UD/IP/EXP file numbers did not resolve to a canonical ERP row. |
| `ud_family_inconsistent` | `hard_block` | ud_ip_exp | Resolved ERP rows for extracted UD/IP/EXP file numbers did not belong to one LC/SC family. |
| `ud_ip_exp_mail_shape_invalid` | `hard_block` | ud_ip_exp | The mail's deterministic document composition is invalid for phase 1, such as UD mixed with IP/EXP, IP without EXP, or more than one EXP/IP payload of the same kind. |
| `ud_filename_lc_suffix_mismatch` | `hard_block` | ud_ip_exp | A UD/IP/EXP attachment filename explicitly carrying a `UD-LC-...` or `UD-SC-...` suffix does not agree with the ERP-derived LC/SC family suffix. |
| `ud_required_document_missing` | `hard_block` | ud_ip_exp | No deterministic UD/IP/EXP document payload was available for processing. |
| `ud_required_field_missing` | `hard_block` | ud_ip_exp | A UD document payload is missing one or more mandatory extracted fields. |
| `ud_required_field_invalid` | `hard_block` | ud_ip_exp | A UD document payload contains a mandatory field that is present but invalid, such as an unparseable date or non-BGMEA UD/AM number. |
| `ud_document_number_pattern_mismatch` | `hard_block` | ud_ip_exp | A UD attachment did not yield an extracted BGMEA UD/AM number matching the required workbook-write pattern; filename fallback is not allowed. |
| `ud_allocation_unresolved` | `hard_block` | ud_ip_exp | UD quantity allocation did not produce a selected candidate row combination. |
| `ud_lc_date_mismatch` | `hard_block` | ud_ip_exp | Structured UD/AM LC table date did not match the ERP LC/SC date. |
| `ud_lc_value_match_unresolved` | `hard_block` | ud_ip_exp | Structured UD/AM LC value did not identify any exact workbook value-matched row group in the verified LC/SC family. |
| `ud_quantity_below_workbook` | `hard_block` | ud_ip_exp | Structured UD/AM supplier quantity was less than the selected workbook quantity for a unit. |
| `ud_quantity_excess_below_threshold` | `hard_block` | ud_ip_exp | Structured UD/AM supplier quantity exceeded workbook quantity by less than the required 50-unit threshold. |
| `ud_target_row_conflict` | `hard_block` | ud_ip_exp | The candidate UD/AM row group is already assigned to a different UD/AM document, or the existing UD date conflicts with the candidate document. |
| `ud_shared_column_nonblank_policy_unresolved` | `hard_block` | ud_ip_exp | Selected UD target row has one or more non-blank UD target cells; phase 1 does not write to any workbook target cell that already contains a value. |
| `ip_exp_required_field_missing` | `hard_block` | ud_ip_exp | An EXP or IP document payload is missing one or more mandatory extracted fields required for deterministic phase-1 staging. |
| `ip_exp_required_field_invalid` | `hard_block` | ud_ip_exp | An EXP or IP document payload contains an invalid or contradictory required field, such as an unparseable date, conflicting family LC/SC number, or inconsistent same-mail document date. |
| `ip_exp_family_row_missing` | `hard_block` | ud_ip_exp | The verified ERP LC/SC family did not resolve to any existing workbook row for family-wide IP/EXP staging. |
| `ip_exp_target_row_conflict` | `hard_block` | ud_ip_exp | One or more family-wide IP/EXP target rows already contain a different non-blank shared/date value, so phase 1 staging cannot append, merge, or replace them. |
| `ip_exp_policy_unresolved` | `hard_block` | ud_ip_exp | Historical pre-policy code retained for backward compatibility with older run artifacts created before the deterministic phase-1 IP/EXP path was documented and implemented. |

### Bangladesh Bank dashboard verification
| Code | Severity | Scope | Description |
|---|---|---|---|
| `bb_dashboard_family_input_invalid` | `hard_block` | bb_dashboard_verification | Required workbook or ERP inputs for a candidate LC family were missing, unreadable, or not deterministically consistent. |
| `bb_dashboard_fetch_runtime_error` | `hard_block` | bb_dashboard_verification | The live/dashboard-provider fetch failed before a deterministic family result could be formed. |

## 5) Change-control checklist for new codes
A PR introducing new discrepancy code(s) must include:
1. code + severity + scope
2. rationale and triggering condition
3. required `details` fields
4. backward-compatibility/deprecation note
5. tests demonstrating emission path
