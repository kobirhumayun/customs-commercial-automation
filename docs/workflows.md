# Workflow Specifications

## Shared workflow contract
Every CLI workflow should follow the same control shape:
1. capture operator execution context
2. capture a run-level snapshot of all source emails/documents from the relevant manual intake location
3. determine deterministic mail iteration order for the snapshot by:
   - primary key: `ReceivedTime` converted to the workflow state timezone configured for operations (current deployment basis: Bangladesh Standard Time, UTC+06:00)
   - tie-breaker: ascending Outlook `EntryID`
4. save only new attachments/documents while iterating the snapshotted mails in that order
5. extract and normalize entities per mail
6. run workflow rule packs and stage per-mail write/print/move outcomes
7. apply a controlled batch workbook-write phase for successful mails only
8. derive deterministic print-group order from the successful mail groups using the earliest master-workbook row sequence assigned to each group
9. emit JSON reports for both the run and each mail outcome, including persisted `mail_iteration_order` and final `print_group_order`
10. batch print only after the workbook-write phase completes successfully for the eligible mails
11. perform post-run mail moves for successful mails only

For production export attachment storage, the configured `document_root` must be a stable base path, not a per-run timestamped directory. The canonical family hierarchy beneath that base path is what determines where later amendments and related documents are stored:
`Year / Buyer Name / LC-or-SC Number / All Attachments`.

Policy precedence note (phase 1): if a case is unspecified, ambiguous, or not fully satisfied by explicit rule conditions, the outcome must be `hard_block` with comprehensive reporting (no human-review routing in phase 1).

### Operator setup helper: Outlook folder EntryIDs
Live Outlook workflows require real folder `EntryID` values in local config for keys such as:
- `source_working_folder_entry_id`
- `destination_success_entry_id`

Operators may discover these values with the read-only inspection command:

```powershell
uv run python -m project inspect-outlook-folders --outlook-profile "outlook"
```

Optional narrowing flags:
- `--contains <text>` filters by folder name, full folder path, or EntryID
- `--max-depth <n>` limits recursion depth for large mailboxes

The command returns JSON records containing `display_name`, `folder_path`, `entry_id`, `depth`, `store_name`, and `parent_entry_id`. These records are intended for manual config setup only; the command does not create run artifacts or mutate Outlook state.

### Operator diagnostic helper: stopped runs
If a run stops with `hard_blocked_no_write`, `uncertain_not_committed`, `hard_blocked`, or another attention-required phase status, operators should first run the read-only explanation command:

```powershell
uv run python -m project explain-run-failure export_lc_sc --config "D:\customs-automation\export_lc_sc.toml" --run-id "<RUN_ID>"
```

The command summarizes primary causes separately from secondary effects by reading persisted run artifacts such as `discrepancies.jsonl`, `mail_outcomes.jsonl`, `target_probes.jsonl`, and `staged_write_plan.json`. It must not mutate Outlook, ERP, workbook, print, mail-move, or run artifacts. It is the preferred operator-facing first step before deciding whether the correct action is to fix input mails, clean a partial workbook row, use recovery, or simply rerun after correcting the environment.

### Operator setup helper: ERP download debugging
When the live ERP report page requires form input and export/download interaction rather than exposing a stable HTML table, operators may use the read-only debug command below to capture selectors and output artifacts:

```powershell
uv run python -m project inspect-erp-download export_lc_sc --config "D:\customs-automation\export_lc_sc.toml" --headed
```

The command accepts repeated `--fill SELECTOR=VALUE` inputs plus optional selectors for submit, post-submit wait state, download menu, and download format click.

For stable local reuse, the same fill values may be stored in config under:
- `erp_report_fill_values = ["SELECTOR=VALUE", ...]`

Typical example:

```powershell
uv run python -m project inspect-erp-download export_lc_sc --config "D:\customs-automation\export_lc_sc.toml" --headed `
  --fill "#fromDate=2026-03-01" `
  --fill "#toDate=2026-03-31" `
  --submit-selector "#btnShow" `
  --post-submit-wait-selector "#downloadDropdown" `
  --download-menu-selector "#downloadDropdown" `
  --download-format-selector "text=CSV"
```

The debug run writes page HTML, a full-page screenshot, and any downloaded export file under `report_root/erp_debug/...`. This command is intended for selector discovery and evidence capture only; it does not create run artifacts or stage workflow validation.

### Shared decision and phase-state enums (normative)

#### Decision enum
- Allowed values: `pass`, `warning`, `hard_block`.
- `hard_block` is terminal for the affected mail in the active run (no staged write, no print eligibility, no mail move eligibility).

#### Warning-to-action decision table
| Mail discrepancy profile | Staged write allowed | Print allowed | Mail move allowed |
|---|---:|---:|---:|
| no discrepancies | yes | yes (if workflow has print phase) | yes |
| warning-only discrepancies | yes | yes (if workflow has print phase) | yes |
| any hard-block discrepancy | no | no | no |

`warning` never overrides any explicit hard-block rule. If both warning and hard-block discrepancies are present, final decision is `hard_block`.

#### `write_phase_status` enum
- Allowed values: `not_started`, `prevalidating_targets`, `prevalidated`, `applying`, `hard_blocked_no_write`, `uncertain_not_committed`, `committed`.
- Transition rules are defined in the **Numbered transition flow for `write_phase_status`** section below.

#### `print_phase_status` enum
- Allowed values: `not_started`, `planned`, `printing`, `completed`, `hard_blocked`, `uncertain_incomplete`.
- Allowed transitions:
  1. `not_started` → `planned`
  2. `planned` → `printing`
  3. `printing` → `completed`
  4. `planned` or `printing` → `uncertain_incomplete` (runtime interruption)
  5. `uncertain_incomplete` → `printing` (resume from persisted partial print progress)
  6. `not_started` or `planned` → `hard_blocked` (cross-phase gate or eligibility failure)

#### `mail_move_phase_status` enum
- Allowed values: `not_started`, `moving`, `completed`, `hard_blocked`, `uncertain_incomplete`.
- Allowed transitions:
  1. `not_started` → `moving`
  2. `moving` → `completed`
  3. `moving` → `uncertain_incomplete` (runtime interruption)
  4. `not_started` → `hard_blocked` (cross-phase gate not satisfied or eligibility failure)

Any attempted state transition not listed above is a hard-block with discrepancy code `invalid_phase_state_transition`.


### Rule-pack discovery and lineage contract (shared, normative)
- The active workflow rule-pack module must publish a canonical version constant named `RULE_PACK_VERSION`.
- Startup is a hard failure if `RULE_PACK_VERSION` is missing, empty, non-string, or not a valid semantic version.
- Every run-level and mail-level report must include:
  - `workflow_id`
  - `rule_pack_id`
  - `rule_pack_version`
  - `applied_rule_ids` (ordered list of rule IDs applied from shared-core + workflow-specific packs)

Example mail/run report fragment:

```json
{
  "run_id": "run-2026-03-24T09-30-00Z",
  "mail_id": "00000000A1B2C3D4",
  "workflow_id": "export_lc_sc",
  "rule_pack_id": "export_lc_sc.default",
  "rule_pack_version": "1.4.0",
  "applied_rule_ids": [
    "core.subject.buyer_lc_match.v1",
    "core.extraction.required_fields.v2",
    "export_lc_sc.exception.filename_cosmetic_variation.v1"
  ],
  "final_decision": "warning"
}
```

### Rule ID governance (shared, normative)
To keep lineage stable across releases, all rule IDs must follow one governance policy.

#### Rule ID pattern and uniqueness
- Required format: `<scope>.<domain>.<name>.v<major>`
- Example: `core.subject.buyer_lc_match.v1`
- `scope` is one of: `core`, `export_lc_sc`, `ud_ip_exp`, `import_btb_lc`, `bb_dashboard_verification`
- Rule IDs are globally unique across the repository (not just within one workflow).

#### Stability and lifecycle
- Once released, a rule ID must never be reused for different semantics.
- Material logic changes require a new ID/version suffix (for example `.v1` → `.v2`).
- Deprecated rules remain reserved and may be marked inactive, but not reassigned.

#### Registry and CI validation
- Canonical registry path: `rules/registry/*.yaml` (or equivalent machine-readable location adopted by implementation).
- Startup/run validation should hard-fail if a rule emits an ID not present in the registry.
- CI should verify:
  - uniqueness of IDs
  - pattern conformance
  - no deleted/reused IDs without explicit deprecation metadata

#### Required change-log discipline
Any PR that adds/removes/changes rule logic must include:
1. affected rule IDs
2. change type (`add`, `deprecate`, `supersede`)
3. rationale and impact on report lineage
4. migration note if decision behavior changes

### Batch write contract (normative)
Batch atomicity applies only to mails with approved staged write operations, not to all mails in the run snapshot.
If one mail in the run snapshot is blocked while others are approved, the blocked mail contributes no workbook writes and each approved mail still participates in the same atomic commit of the approved staged write set.
Example run (3 mails): Mail A = blocked, Mail B = approved (2 staged writes), Mail C = approved (1 staged write) ⇒ batch write outcome: commit Mail B + Mail C writes together (3 total) in one atomic transaction; commit none if that transaction fails; Mail A writes remain zero.

### Write transaction protocol and `write_phase_status` transitions (shared, normative)

#### Staged write application protocol
All write-capable workflows must execute this protocol exactly:
1. Build one deterministic staged write plan ordered by `(mail_iteration_order, operation_index_within_mail)`.
2. Persist `write_phase_status=prevalidating_targets`.
3. Pre-validate **all** staged targets before any write:
   - target address resolvable (sheet/row/column)
   - row-eligibility predicates satisfied
   - expected pre-write values satisfy staged constraints
4. If any target fails pre-validation, persist `write_phase_status=hard_blocked_no_write`, emit discrepancy report, and perform zero writes.
5. If all targets pass, persist `write_phase_status=prevalidated`.
6. Apply writes in the same deterministic staged order; while writing, persist `write_phase_status=applying`.
7. Run post-write probes at target-cell granularity and save workbook.
8. If probes and save succeed for all targets, create commit marker and persist `write_phase_status=committed`.
9. Any runtime interruption after `prevalidated` but before `committed` must persist `write_phase_status=uncertain_not_committed`.

#### Commit marker creation point and required metadata
Create the commit marker **only after** all staged writes are applied, post-write probes confirm expected values for all targets, and workbook save succeeds.
Commit marker payload must include at least:
- `run_id`, `workflow_id`, `tool_version`, `rule_pack_version`
- `committed_at_utc` (ISO-8601 UTC timestamp)
- `operation_count`
- `staged_write_plan_hash` (SHA-256 over canonical plan serialization)
- `run_start_backup_hash`
- `post_write_probe_summary` with counts for `matches_post_write`, `matches_pre_write`, `mismatch_unknown`

#### Failure window behavior (normative)
- If interruption occurs **before** commit marker creation (`prevalidating_targets`, `prevalidated`, `applying`, or `uncertain_not_committed`), recovery must treat the write as not yet committed and decide via per-target probes whether safe reapply is allowed.
- If interruption occurs **after** commit marker creation (`committed`), recovery must treat workbook writes as committed intent and only evaluate safe resume of print/mail-move via idempotency markers.
- Metadata/probe contradictions are always `hard_block`.

#### Minimum probe granularity (normative)
Recovery and commit validation must probe every staged target cell individually. Minimum probe record fields:
- `sheet_name`
- `row_index`
- `column_key` (or canonical column index)
- `expected_pre_write_value`
- `expected_post_write_value`
- `observed_value`
- `classification` (`matches_pre_write`, `matches_post_write`, `mismatch_unknown`)

Workbook-level or row-level aggregate probes are not sufficient to classify partial writes safely.

#### Numbered transition flow for `write_phase_status` (normative)
1. `not_started` → `prevalidating_targets`
2. `prevalidating_targets` → `hard_blocked_no_write` (any target pre-validation failure)
3. `prevalidating_targets` → `prevalidated` (all targets pass)
4. `prevalidated` → `applying` (first cell write attempt begins)
5. `applying` → `uncertain_not_committed` (interruption/error before commit marker creation)
6. `applying` → `committed` (all writes applied, post-write probes pass, workbook save succeeds, commit marker persisted)
7. `committed` is terminal for workbook-write phase; only print/mail-move phases may continue/resume.

### Recovery Decision Matrix (shared, normative)
This section defines the mandatory recovery contract when a prior run is uncertain/incomplete and a new write-capable run attempts to start.

#### Required artifacts
Recovery checks require all of the following persisted artifacts from the prior run:
1. **Backup hash**
   - cryptographic hash of the run-start workbook backup artifact (`run_start_backup_hash`)
   - cryptographic hash of the active workbook at recovery time (`current_workbook_hash`)
   - algorithm: **SHA-256**
   - encoding: lowercase hexadecimal (64 chars)
2. **Staged write plan**
   - ordered staged write operations, including sheet, row, column, expected pre-write value, and intended value
   - canonical serialization is required before hashing:
     - UTF-8 bytes
     - LF (`\n`) line endings only
     - stable object key order (lexicographic ascending)
     - deterministic operation ordering by `(mail_iteration_order, operation_index_within_mail)`
   - persisted plan hash (`staged_write_plan_hash`) uses SHA-256 over canonical serialized bytes
   - encoding: lowercase hexadecimal (64 chars)
3. **Run metadata**
   - run id, workflow id, tool version/rule-pack version
   - persisted `mail_iteration_order`
   - persisted `print_group_order` (if computed before interruption)
   - phase checkpoints (`write_phase_status`, `print_phase_status`, `mail_move_phase_status`)
   - required hash metadata fields:
     - `hash_algorithm` = `sha256`
     - `run_start_backup_hash`
     - `current_workbook_hash`
     - `staged_write_plan_hash`
4. **Workbook probe results**
   - deterministic probe of all staged target cells against expected post-write values and expected pre-write values from the staged write plan
   - derived probe classification per target: `matches_post_write`, `matches_pre_write`, or `mismatch_unknown`

If any required artifact is missing, unreadable, or hash-invalid, recovery must hard-block.

#### Outcomes and exact conditions
Recovery must produce exactly one outcome:

1. **safe resume**
   - `run_start_backup_hash` equals persisted backup file hash
   - `staged_write_plan_hash` is valid and staged plan is readable
   - workbook probe shows all staged targets as `matches_post_write`
   - `write_phase_status` is `committed`
   - `print_phase_status` and/or `mail_move_phase_status` are incomplete or unknown

2. **safe reapply staged writes**
   - `run_start_backup_hash` equals persisted backup file hash
   - `staged_write_plan_hash` is valid and staged plan is readable
   - workbook probe shows all staged targets as `matches_pre_write`
   - `write_phase_status` is `not_started` or `uncertain_not_committed`
   - no target classified as `matches_post_write` or `mismatch_unknown`

3. **hard-block requiring operator/manual recovery**
   - any missing/unreadable/invalid required artifact
   - backup hash mismatch
   - mixed probe state across staged targets (`matches_pre_write` + `matches_post_write`)
   - any target classified as `mismatch_unknown`
   - phase metadata contradictions (for example: `write_phase_status=committed` while all targets are `matches_pre_write`)
   - print/mail-move evidence inconsistent with run metadata or absent when required for deterministic continuation

#### Idempotency checks for partial print/mail-move stages
When recovery outcome is `safe resume`, perform these idempotency gates before resuming:
1. **Print idempotency**
   - each print group has a stable id derived from `(run_id, mail_id, print_group_index, document_path_hash)`
   - each print group marker may be either `completed` or `partial_incomplete`
   - a `partial_incomplete` marker must persist `printed_document_path_hashes` as a deterministic prefix of the planned `document_path_hashes`
   - if Acrobat timed out after physical paper output, operators may advance that deterministic prefix manually with `acknowledge-partial-print`
   - resume must skip any group whose completion marker exists and is hash-consistent
   - resume may continue a `partial_incomplete` print group only from the remaining suffix of `document_path_hashes`
   - if operators confirm that all planned PDFs physically printed, `acknowledge-partial-print --printed-count <total>` may finalize the marker as `completed`; one final `execute-print` pass must then be used to close the run metadata without sending more print commands
   - if marker exists but hash/metadata differs from persisted plan, hard-block
2. **Mail-move idempotency**
   - each mail move has a stable operation id derived from `(run_id, entry_id, destination_folder)`
   - resume must skip mails already marked moved with matching destination and timestamp evidence
   - resume may move only mails with no completion marker
   - if mail is no longer in expected source folder and no valid completion marker exists, hard-block
3. **Cross-phase gate**
   - mail move resumption is allowed only after print resumption reaches terminal success for all eligible groups (or workflow has no print phase)
   - any attempt to move mail before this gate is a hard-block

#### Recovery pseudocode (normative)
```text
function recover_or_block(prior_run_id):
    artifacts = load_required_artifacts(prior_run_id)
    if artifacts.missing_or_invalid:
        return HARD_BLOCK("missing/invalid artifact set")

    if sha256_hex(artifacts.backup_file_bytes) != artifacts.run_start_backup_hash:
        return HARD_BLOCK("backup hash mismatch")

    if compute_staged_plan_hash(artifacts.staged_write_plan) != artifacts.staged_write_plan_hash:
        return HARD_BLOCK("staged write plan hash mismatch")

    probe = probe_workbook_targets(
        workbook=current_master_workbook,
        staged_plan=artifacts.staged_write_plan
    )

    if probe.any == mismatch_unknown:
        return HARD_BLOCK("unknown workbook state")

    if probe.all == matches_pre_write:
        if artifacts.write_phase_status in {not_started, uncertain_not_committed}:
            return SAFE_REAPPLY_STAGED_WRITES
        return HARD_BLOCK("metadata/probe contradiction: expected uncommitted write status")

    if probe.all == matches_post_write:
        if artifacts.write_phase_status != committed:
            return HARD_BLOCK("metadata/probe contradiction: expected committed write status")

        print_check = evaluate_print_idempotency(artifacts)
        if print_check == inconsistent:
            return HARD_BLOCK("print evidence mismatch")

        move_check = evaluate_mail_move_idempotency(artifacts)
        if move_check == inconsistent:
            return HARD_BLOCK("mail-move evidence mismatch")

        return SAFE_RESUME

    return HARD_BLOCK("mixed target states require manual recovery")
```

Helper reference:
```text
function compute_staged_plan_hash(plan):
    canonical_bytes = canonical_serialize_staged_plan(
        plan,
        key_order="lexicographic_asc",
        line_endings="lf",
        encoding="utf-8",
        operation_order=("mail_iteration_order", "operation_index_within_mail")
    )
    return sha256_hex(canonical_bytes)
```

### Deterministic rule aggregation contract (shared, normative)
Rule outcomes must be aggregated in a deterministic way across shared core rules and workflow-specific rules.

#### Execution order
1. Run shared core rules in ascending `rule_id` lexical order.
2. Run workflow-specific standard rules in ascending `rule_id` lexical order.
3. Run workflow-specific exception rules in ascending `rule_id` lexical order.
4. Persist `applied_rule_ids` exactly in execution order.

#### Allowed per-rule outcomes
- `pass`
- `warning`
- `hard_block`

Any unknown outcome value is a startup hard failure for that run.

#### Aggregation precedence
Final decision precedence is strict:
1. Any `hard_block` present => final decision `hard_block`.
2. Else any `warning` present => final decision `warning`.
3. Else => final decision `pass`.

#### Discrepancy merge semantics
- Deduplication key: `(discrepancy_code, subject_scope, target_ref)`.
- If duplicates exist, keep the first by execution order and append later emitting `rule_id`s to `source_rule_ids`.
- Final discrepancy list must be sorted by:
  1) severity (`hard_block` before `warning`), then
  2) first-emitting rule execution order, then
  3) discrepancy code lexical order.

#### Aggregation pseudocode
```text
function aggregate_rule_results(rule_results):
    applied_rule_ids = []
    discrepancies = OrderedMap()  # key=(code, scope, target_ref)
    seen_warning = false
    seen_hard_block = false

    for result in rule_results_in_execution_order:
        applied_rule_ids.append(result.rule_id)
        if result.outcome == warning:
            seen_warning = true
        if result.outcome == hard_block:
            seen_hard_block = true

        for d in result.discrepancies:
            key = (d.code, d.subject_scope, d.target_ref)
            if key not in discrepancies:
                discrepancies[key] = d.with_source_rule_ids([result.rule_id])
            else:
                discrepancies[key].source_rule_ids.append(result.rule_id)

    final_decision = hard_block if seen_hard_block else warning if seen_warning else pass
    return aggregated_payload(applied_rule_ids, sort_discrepancies(discrepancies.values()), final_decision)
```

#### Worked examples
1. Core warning + workflow pass => final `warning`.
2. Core warning + workflow hard_block => final `hard_block` and no write/print/mail-move.

### Deterministic workflow selection gaps (shared, normative closure)
The following selection/disambiguation rules are mandatory to avoid implementation drift.

#### 1) Export append/skip candidate resolution
When export workflow finds multiple plausible workbook targets for update/append decisions, apply tie-break keys in order:
1. exact file-number canonical match count (higher first)
2. exact amendment canonical match count (higher first)
3. earliest row index (ascending)
4. stable candidate id (lexicographically smallest)

If still tied after all keys: `hard_block` with discrepancy code `export_candidate_tie_after_full_tiebreak`.

#### 2) Import candidate-row tie scenarios
After 40%-80% validation, if more than one candidate row remains:
1. prefer row with earliest row index
2. if tied on row index (logical duplicates), prefer candidate with greatest blank-field compatibility for required target columns
3. if still tied, choose stable candidate id (lexicographically smallest)

If fully tied: `hard_block` with discrepancy code `import_candidate_tie_after_full_tiebreak`.

#### 3) Attachment-to-document-type disambiguation
If multiple attachments map to the same required class (for example two PI candidates):
1. prefer strongest deterministic filename pattern score
2. if tie, prefer higher clause-extraction confidence
3. if tie, prefer earliest attachment order in message metadata
4. if tie, stable filename lexical order

If still tied and choosing one would change downstream writes: `hard_block` with discrepancy code `attachment_classification_ambiguous`.

#### 4) OCR fallback acceptance thresholds
For scanned/hybrid extraction fallback:
- If required fields are extracted with confidence meeting workflow thresholds, continue.
- If any required field is missing or below threshold, outcome is `hard_block`.
- Warning-only continuation is allowed only when all required fields pass and only non-required fields are low confidence.

Required discrepancy codes:
- `ocr_required_field_below_threshold`
- `ocr_required_field_missing`
- `ocr_non_required_field_low_confidence`

#### Required report fields for all deterministic selections
Mail-level reports must include:
- evaluated candidate count
- tie-break keys and values per candidate
- selected candidate id (or null for hard-block)
- rejection reasons per non-selected candidate
- final selection decision reason/code

### Workbook write failure strategy (shared, normative)
Because Excel desktop adapters may fail mid-apply, write-capable workflows must use the following deterministic failure playbook.

### Workbook lock/contention handling protocol (shared, normative)
All write-capable workflows must execute this preflight before `write_phase_status=prevalidating_targets`:
1. workbook existence + write permission check
2. conflicting-open session detection
3. adapter health check (open/save capability)
4. persisted preflight evidence in run metadata

If any check fails, emit discrepancy and stop with no workbook mutation.

#### Contention decision table
| Condition | Retry? | Discrepancy code | Write phase status |
|---|---|---|---|
| workbook locked by another actor | no | `workbook_lock_conflict` | `hard_blocked_no_write` |
| workbook opened read-only | no | `workbook_open_readonly` | `hard_blocked_no_write` |
| adapter unavailable at startup | yes (max 3 attempts) | `excel_adapter_unavailable` | `hard_blocked_no_write` |
| save conflict during apply | no | `workbook_save_conflict` | `uncertain_not_committed` |

#### Failure decision table
| Failure point | Required write state | Same-run action | Next-run recovery eligibility |
|---|---|---|---|
| target pre-validation fails | `hard_blocked_no_write` | stop; zero writes | new run allowed without recovery resume |
| first write throws before save | `uncertain_not_committed` | stop; zero downstream phases | recovery matrix required |
| post-write probe mismatch | `uncertain_not_committed` | stop; zero downstream phases | recovery matrix required |
| save failure after successful apply | `uncertain_not_committed` | stop; zero downstream phases | recovery matrix required |

#### Rollback policy
- No in-memory rollback is trusted as proof of safety.
- No automatic same-run backup restore is allowed.
- The only permitted post-failure path is persisted uncertain state + recovery gate evaluation in a subsequent run.
- Manual/operator backup restoration remains an out-of-band recovery operation and must be recorded before rerun.

### Canonical report field set for downstream consumers (shared, normative)
Run-level reports and mail-level reports must include these required fields:

Run-level:
- `report_schema_version`
- `run_id`, `workflow_id`
- `rule_pack_id`, `rule_pack_version`
- `mail_iteration_order`, `print_group_order`
- `write_phase_status`, `print_phase_status`, `mail_move_phase_status`
- `hash_algorithm`, `run_start_backup_hash`, `staged_write_plan_hash`

Mail-level:
- `report_schema_version`
- `run_id`, `mail_id`, `workflow_id`
- `rule_pack_id`, `rule_pack_version`
- `applied_rule_ids`, `final_decision`
- `discrepancies`
- `saved_documents`
- `staged_write_operations`
- `print_group_id` (if eligible)
- `mail_move_operation_id` (if eligible)

## Export LC/SC intake

### Inputs
- Outlook folder: `working` after operator triage from `temp-export`; snapshot all messages in the folder when the CLI is triggered
- ERP report: `RptCommercialExport/DateWiseLCRegisterForDocuments`
- Attachments: all PDF attachments are saved; LC/SC and PI extraction/classification is informational only

### Deterministic checks
- parse subject into document type, LC/SC end sequence, buyer, and optional suffix
- extract all body file numbers matching `P/<yy>/<nnnn>`
- validate every extracted file number through ERP lookup and pathing rules while retaining all file numbers for audit
- deduplicate repeated mentions of the same canonical file number within one mail body before ERP lookup and workbook staging
- define ERP family consistency using ERP `LC No.`, normalized buyer, and canonicalized ERP `LC DT.`
- canonical row selection follows ERP row order
- the first occurrence row is the canonical row for that file number/family context
- canonical row fields drive folder path construction, workbook mapping, and reporting metadata
- duplicate true-equivalent ERP rows do not alter canonical selection once the first occurrence is chosen
- hard-block if the extracted file numbers do not resolve to the same LC/SC family; any partial family match is a hard block
- normalize ERP buyer name by splitting on `\`, trimming whitespace, and trimming trailing periods
- subject parsing and subject-to-ERP comparison remain optional/advisory only; ERP rows selected from body file numbers are final
- attachment naming and OCR-derived signals may be recorded for reporting, but they do not block processing

Example (canonical selection): if two ERP rows are true-equivalent for `P/26/0042` and appear as row 118 then row 241, row 118 remains canonical and its fields are used for folder pathing, workbook mapping, and reporting metadata.

### Workbook mapping
Use ERP fields to populate:
- `Name of Buyers` ← `Buyer Name`
- `L/C Issuing Bank` ← `Notify Bank`
- `L/C & S/C No.` ← `LC No.`
- `LC Issue Date` ← `LC DT.`
- `Amount` (column 6, Export LC/SC field) ← `Current LC Value`
- `Shipment Date` ← `Ship. DT.`
- `Expiry Date` ← `Expiry DT.`
- `Quantity of Fabrics (Yds/Mtr)` ← `LC Qty`
- `L/C Amnd No.` ← `Amd No`
- `L/C Amnd Date` ← `Amd DT`
- `Lien Bank` ← `Nego Bank`
- `Master L/C No.` ← `Master LC No.`
- `Master L/C Issue Dt.` ← `M.L/C Date`
- `Bangladesh Bank Ref.` ← `Ship. Remarks`
- `Commercial File No.` ← `File No.`

Note: the master workbook intentionally contains duplicate `Amount` headers. The export workflow must write only to column 6. Column 22 `Amount` is reserved for Import LC (Back-to-Back) workflow writes.
The `Bangladesh Bank Ref.` workbook header and ERP `Ship. Remarks` report column are mandatory for export mapping. The ERP `Ship. Remarks` row value may be blank; in that case the workflow writes a blank workbook value and does not hard-block the mail for that field alone.

### No-write rules
- any extracted file number is missing its required ERP row
- any partial family match across LC/SC number, normalized buyer, and LC/SC date
- duplicate file number already present when workflow expects skip
- duplicate file number already staged earlier in the same run when workflow expects skip
- any incomplete validation needed for append/skip decision

### Duplicate-only terminal behavior
- If every canonical file number in a mail is already present in the workbook, the mail outcome is `duplicate_only_noop`.
- A `duplicate_only_noop` mail stages no workbook writes and requires no print planning or print execution.
- Subject parsing and subject-to-ERP comparison remain advisory only in this path as well; body file numbers plus ERP rows remain final.
- A `duplicate_only_noop` mail may still complete the workflow through post-run mail movement once validation succeeds.
- Duplicate-only handling must be visible in run reports, workflow summaries, dashboards, and mail-move receipts.

### Batch execution behavior
- blocked emails remain in `working`
- successfully processed export-team emails with new writes move to `UD and LC` only after the batch workbook-write and batch print phases finish
- duplicate-only successful emails may move to `UD and LC` without workbook-write or print completion because no new write or print obligation exists
- print batches are built from successful mails in the active run snapshot, using all newly saved PDFs
- duplicate prevention is enforced by canonical file number, not by identical mail subject/body detection
- if multiple mails in one run contain the same canonical file number, deterministic `mail_iteration_order` decides which mail is evaluated first and later mails must not create an additional workbook row for that file

## UD / IP / EXP processing

During the initial live-deployment phase, any mismatch, unknown exception, or incomplete rule condition should hard-block with a comprehensive report rather than route to human review.

### Inputs
- Outlook folder: `working`; snapshot all messages in the folder when the CLI is triggered
- Email body file numbers resolved through the ERP register report, using the same canonical file-number extraction and one-family consistency checks as `export_lc_sc`
- PDF attachments for UD, EXP, and/or IP
- Existing master workbook rows for the same LC/SC family

### Initial live-document validation boundary
- the email subject is not a required or authoritative input for `ud_ip_exp`; subject parsing must not drive family resolution, storage, validation, printing, or mail movement
- LC/SC family resolution for live `ud_ip_exp` processing must come from email body file numbers plus ERP lookup, matching the `export_lc_sc` family rules for LC/SC number, normalized buyer, and LC/SC date
- live saved-document analysis may derive UD/IP/EXP document number, date, LC/SC number, quantity, and unit from saved PDFs before rule evaluation, but PDF-derived LC/SC evidence is validation evidence only and must not replace the ERP-derived family
- live UD attachment saving/classification must hard-block if PDF-derived LC/SC evidence contradicts the ERP-derived LC/SC family for the mail
- structured Base UD PDFs are identified by `UD Authenticating Authority` on page 1; structured UD Amendment PDFs are identified by `Amendment Authenticating Authority` on page 1
- for structured Base UD and UD Amendment PDFs, the UD/AM number and UD/AM date come from page 1 table index 1, row index 1, columns 1 and 3 respectively
- structured UD LC/SC table extraction must match rows by exact ERP `Ship. Remarks` when present/found, otherwise exact ERP `LC No.`; ERP values are sourced from the email-body file number lookup
- the extracted UD LC/SC table date must match the ERP LC/SC date before workbook writes are allowed
- the extracted UD LC/SC table value is mandatory and drives target workbook row selection before quantity validation
- structured UD quantity extraction must aggregate rows for supplier `PIONEER DENIM LIMITED` or `PIONEER DENIM LTD`, applying supplier `DO` fill-down in the supplier column before filtering
- live UD attachment saving/classification must also hard-block if multiple live-derived UD attachments in the same mail disagree on required UD evidence such as `document_date` or `quantity`
- when multiple same-family UD payloads are available, deterministic allocation/reporting may use the most complete UD payload as the selected UD evidence source, using completeness of required extraction fields rather than attachment order
- this selected-payload preference does not relax validation: any UD payload missing required fields must still hard-block with attachment/document-level evidence before workbook writes
- these hard blocks must include attachment-level evidence in the discrepancy/details payload so the operator can see which documents disagreed
- live `ud_ip_exp` attachment storage must use the same canonical export attachment hierarchy as `export_lc_sc`, rooted at the ERP-derived LC/SC family: `Year / Buyer Name / LC-or-SC Number / All Attachments`
- ERP `LC No.`/`L/C & S/C No.` family context and ERP `Ship. Remarks` are the primary linkage inputs for structured Base UD and UD Amendment PDF property extraction
- until IP/EXP business rules are finalized, IP/EXP documents remain allowed only as explicit unresolved-policy hard-block evidence rather than a completed processing path

### Batch execution behavior
- blocked emails remain in `working`
- successfully processed `ud_ip_exp` emails with new writes move using the same staged post-write/post-print movement model as `export_lc_sc`
- print batches are built from successful `ud_ip_exp` mails in the active run snapshot, using all newly saved PDFs after the workbook write commit
- duplicate-only/no-write movement behavior for `ud_ip_exp` follows the shared staged mail-move gates once validation succeeds and no print obligation exists

### Shared-column behavior
- Column `UD No. & IP No.` stores UD/EXP/IP values together.
- UD entries have no prefix.
- EXP entries use `EXP: ` prefix.
- IP entries use `IP: ` prefix.
- When both EXP and IP exist, EXP must be listed before IP.
- Multiple entries are line-break separated.

### UD allocation logic
- extract UD/AM number, UD/AM date, LC/SC date, LC/SC value, and supplier quantities by unit
- filter workbook rows to the ERP LC/SC family and exclude rows where `UD No. & IP No.` is already populated
- sort candidate workbook rows by row index ascending
- starting from the first blank-UD row, sum workbook `Amount` column 6 until it matches the extracted UD LC/SC value numerically within tolerance
- the matched contiguous row range is the only target row group for the mail
- sum workbook quantities for only that target row group by unit
- compare each workbook unit total against the corresponding UD supplier quantity total
- pass quantity validation only when UD quantity equals workbook quantity or the UD excess is at least 50 yards/meters; hard-block when UD quantity is less than workbook quantity or excess is greater than zero but below 50
- write the UD/AM number, UD/AM date, and current workflow receive date to matched rows only if value and quantity rules are satisfied
- `UD & IP Date` is written from the UD/AM document date as `DD/MM/YYYY`; `UD Recv. Date` is written as the current workflow date in the same format

#### UD row-combination candidate scoring and tie-break order (normative)
When more than one valid row combination can satisfy UD quantity allocation, the workflow must score each combination, then apply this deterministic tie-break sequence:

1. **Primary key — workbook row index sequence (ascending)**
   - Compare combinations lexicographically by sorted workbook row indexes.
   - Prefer the combination whose first differing row index is smaller.
2. **Secondary key — amendment recency (older first)**
   - For each combination, derive an amendment recency tuple from matched rows:
     - normalized `L/C Amnd Date` ascending (blank treated as oldest)
     - then numeric `L/C Amnd No.` ascending (blank treated as `0`)
   - Prefer the combination with the lexicographically smaller recency tuple.
3. **Tertiary key — blank-field priority (maximize write safety)**
   - Prefer the combination with the higher count of rows where all UD target cells for this write are blank at pre-write validation.
   - If still tied, prefer the combination with fewer non-target populated optional cells (minimize risk of semantic conflict).
4. **Quaternary key — stable candidate id**
   - Build `candidate_id` as joined sorted row indexes (example: `17-22-25`).
   - Select the lexicographically smallest `candidate_id`.

#### Equal-score candidate behavior (normative)
- If two or more candidate combinations remain exactly tied after all keys above, do **not** select arbitrarily.
- Mark the mail outcome as `hard_block`.
- Emit discrepancy reason `ud_candidate_tie_after_full_tiebreak`.
- Include full candidate comparison details in the mail report so the operator can resolve data ambiguity offline.

#### Required UD selection-report fields (normative)
For every mail that reaches UD allocation, the mail-level JSON report must include:
- `ud_selection.required_quantity`
- `ud_selection.quantity_unit`
- `ud_selection.candidate_count`
- `ud_selection.candidates[]` with:
  - `candidate_id`
  - `row_indexes` (ascending)
  - `matched_quantities`
  - `score_keys` object containing:
    - `row_index_key`
    - `amendment_recency_key`
    - `blank_field_priority_key`
    - `stable_candidate_id_key`
  - `prewrite_blank_targets_count`
  - `prewrite_nonblank_optional_count`
  - `selected` (boolean)
  - `rejection_reason` (if not selected)
- `ud_selection.final_decision` (`selected` or `hard_block_tie`)
- `ud_selection.final_decision_reason`

#### Worked example (duplicated quantities + non-sequential matches)
UD extracted quantity = `3000 YDS`.
Eligible rows for same LC/SC family (row → available quantity, amendment metadata):
- row 11 → `1000`, `Amd No=1`, `Amd Date=2026-01-02`
- row 14 → `1000`, `Amd No=1`, `Amd Date=2026-01-02`
- row 19 → `2000`, `Amd No=2`, `Amd Date=2026-02-10`
- row 27 → `2000`, `Amd No=2`, `Amd Date=2026-02-10`

Valid quantity combinations:
- Candidate A: rows `[11, 19]` = `1000 + 2000`
- Candidate B: rows `[14, 19]` = `1000 + 2000`
- Candidate C: rows `[11, 27]` = `1000 + 2000`
- Candidate D: rows `[14, 27]` = `1000 + 2000`

Selection:
1. Row-index key prefers candidates starting with row `11` over row `14` → keep A/C.
2. Amendment recency ties between A and C (same amendment metadata pattern).
3. Blank-field priority evaluated; if equal, continue.
4. Stable `candidate_id` tie-break: `11-19` < `11-27` → select Candidate A.

Result: UD is written to rows 11 and 19 only; report records all four candidates and why Candidate A won.

### IP / EXP rules
- no amendment model
- each document is newly issued against a specific LC/SC or amendment
- when multiple IP/EXP docs appear in one mail, their total value and quantity should match LC total unless a future documented exception applies
- dates must be written line-by-line aligned with their corresponding numbers

## Import / BTB LC processing

### Inputs
- Outlook folder: `working` after operator triage from `temp-import`; snapshot all messages in the folder when the CLI is triggered
- Fabric-related import/back-to-back LC emails identified by case-insensitive substring matching on fabric keywords in the subject, with the keyword list stored in code

### Extraction targets
- BTB LC number
- BTB LC date
- BTB LC value
- yarn quantity from PI
- related export LC number from clause text

### Candidate row rules
- export LC matches related export LC
- `UP No.` blank
- BTB LC target field blank
- choose the first row where BTB LC value is between 40% and 80% of export LC value
- one import LC maps to one row only

### Batch execution behavior
- blocked emails remain in `working`
- successfully processed import-team emails move to `Import` only after the batch workbook-write and batch print phases finish

## Bangladesh Bank dashboard verification

### Candidate filters
Rows where:
- `UP No.` is blank
- UD value exists
- dashboard field is blank, not `OK`, or not `OK (Kgs)`

### Verification result values
- `OK` when quantity matches LC Qty and remaining fields are consistent
- `OK (Kgs)` when quantity mismatches LC Qty but matches Net Weight and remaining fields are consistent
- otherwise write a combined descriptive discrepancy string into `Bangladesh Bank Dashboard`

## Printing
- only newly saved PDFs are printed
- print groups are organized by originating mail from the active run snapshot
- group order follows master-workbook row sequence across mail groups
- within each mail group, print every newly saved PDF exactly in saved/staged order, with no additional intra-group sorting
- insert exactly one blank page between consecutive mail groups
- persist final print group order in run JSON metadata
- live submission uses hidden Acrobat OLE automation plus the `JSObject` bridge for silent printing
- when the COM `JSObject` bridge cannot provide print parameters, the adapter must fall back to hidden `AVDoc.PrintPagesSilent` submission
- if `print_printer_name` is configured, that fallback must temporarily switch the Windows default printer to the configured printer, submit the silent job, and then restore the original default printer in `finally`
- print success in phase 1 means deterministic job submission order only; the workflow does not wait for physical printer completion
- any print submission failure must be reported with retry/review metadata

### Operator recovery for partial Acrobat timeouts
- If `execute-print` returns `uncertain_incomplete`, operators must first confirm whether any planned PDFs physically printed after silent submission.
- If no paper output occurred, rerunning `execute-print` is allowed because no print progress was acknowledged.
- If one or more leading PDFs physically printed, operators must record that progress before retrying:

```powershell
uv run python -m project acknowledge-partial-print <workflow_id> --config "<config.toml>" --run-id "<RUN_ID>" --printed-count <N>
```

- After acknowledgment, rerun `execute-print`; the workflow must resume only from the remaining suffix of the planned print group.
- If all planned PDFs physically printed across one or more timed-out attempts, operators may acknowledge the full planned count. The marker becomes `completed`, and one final `execute-print` invocation closes the print phase without sending additional Acrobat submission commands.
- Post-run email moves remain blocked until print phase reaches terminal `completed`.

### Release readiness checklist
- `report-live-readiness` must return `overall_status = "ready"` before first live use on a workstation/profile
- Outlook folder `EntryID` values must be copied from `inspect-outlook-folders` into the active TOML
- ERP download selectors/settings must be validated against the live report form
- the workbook year, sheet, and header mapping must be confirmed for the active filing cycle
- if `print_printer_name` is configured, the operator must validate one real silent print cycle on that named printer
- operators must know that named-printer fallback may temporarily switch the Windows default printer and then restore it automatically
- before release signoff, at least one full live cycle must reach:
  `write = committed`, `print = completed`, `mail move = completed`

### Phase 1 released operator note
- The standard released sequence is:
  `report-live-readiness` -> `validate-run` -> `plan-print` -> `execute-print` -> `execute-mail-moves`
- `acknowledge-partial-print` is an exception-path recovery command, not part of the normal happy-path operator flow.
- Print completion in phase 1 means deterministic silent submission order has completed and the workflow state reached `completed`; it does not mean the system waited for physical paper completion.
- A run may end in terminal `completed` state while still retaining discrepancy records from earlier failed attempts in the same audit trail. Operators should treat terminal phase statuses as the authoritative state and use discrepancies as historical evidence.
- In the released export workflow path, daily `validate-run` with `--document-root` saves attachments for printing/storage but does not perform OCR-based saved-document analysis by default.

## Rule-pack loading contract (shared, normative)
To prevent workflow divergence, rule packs must be discovered and loaded through one canonical structure.

### Required package layout
- Shared core rules: `project/rules/core/`
- Workflow rules: `project/rules/workflows/<workflow_id>/`
- Optional exceptions: `project/rules/workflows/<workflow_id>/exceptions/`

### Required module exports
Every loadable rule-pack module must export:
- `RULE_PACK_ID` (non-empty string)
- `RULE_PACK_VERSION` (semantic version string)
- `RULE_DEFINITIONS` (ordered sequence of rule descriptors)

Each rule descriptor must provide:
- `rule_id` (stable string)
- `stage` (`core`, `workflow_standard`, or `workflow_exception`)
- deterministic callable/entrypoint

### Loader behavior
1. Resolve active `workflow_id`.
2. Load shared core rules from `project.rules.core`.
3. Load workflow rules from `project.rules.workflows.<workflow_id>`.
4. Load exceptions from `project.rules.workflows.<workflow_id>.exceptions` when present.
5. Validate that all `rule_id` values are unique across the resolved set.
6. Sort execution order according to deterministic aggregation contract.
7. Persist `rule_pack_id`, `rule_pack_version`, and ordered `applied_rule_ids`.

### Startup hard-fail cases
Startup must hard-fail if any of these occur:
- missing required exports
- empty or invalid semantic version
- duplicate `RULE_PACK_ID` or duplicate `rule_id`
- unknown rule `stage`
- import/discovery error for required core/workflow modules

## Excel transaction procedure (write-capable workflows, normative)
Write-capable workflows must apply the following desktop Excel procedure.

1. **Session preflight**
   - Confirm workbook path exists and is writable.
   - Acquire exclusive write intent lock (process-level + run metadata lock).
   - Record `excel_session_id`, host, pid, and timestamp in run metadata.

2. **Open + verify baseline**
   - Open workbook in one controlled session.
   - Validate sheet/header expectations and staged target resolvability.
   - If baseline validation fails, set `write_phase_status=hard_blocked_no_write` and close without saving.

3. **Apply staged writes**
   - Transition status to `applying`.
   - Apply staged operations strictly in deterministic order.
   - Capture per-target operation receipts in memory for probe correlation.

4. **Probe + save gate**
   - Run per-target post-write probes.
   - If any probe mismatches expected post-write values, set `write_phase_status=uncertain_not_committed`, do not create commit marker, close session, and hard-block recovery-required state.
   - If probes pass, save workbook in the active session.

5. **Commit marker creation**
   - Only after save success + probe success, persist commit marker and set `write_phase_status=committed`.

6. **Close + release lock**
   - Close workbook session and release lock.
   - Persist close outcome and lock-release evidence.

### Save failure behavior
- If save fails after any write attempt, state is `uncertain_not_committed`.
- No print or mail move is allowed.
- Next write-capable run must execute recovery decision matrix before any new writes.

### Prohibited behavior
- Multiple concurrent write sessions to the same yearly workbook.
- Commit marker creation before successful post-write probes + save.
- Silent retry loops that mutate workbook after uncertain state without recovery gate.

## Outlook folder identity and resolution contract (shared, normative)
Folder routing must use stable identifiers, not display names alone.

### Folder mapping requirements
Each workflow configuration must declare:
- `source_working_folder_entry_id`
- destination folder entry id(s) per success path (for example `destination_success_entry_id`)
- optional display-name hints for diagnostics only

### Resolution behavior
1. Resolve folders by EntryID first.
2. If EntryID resolution fails and policy allows fallback, resolve by exact configured display name and record fallback mode.
3. If multiple folders share the same display name, hard-fail startup.
4. If any required folder is missing/inaccessible, hard-fail startup.

### Audit fields
Run metadata must include resolved folder identity fields:
- `resolved_source_folder_entry_id`
- `resolved_destination_folder_entry_id`
- `folder_resolution_mode` (`entry_id` or `display_name_fallback`)

Mail-level move records must include source/destination EntryIDs and move operation id.

## Report schema reference
Versioned JSON schema definitions for run-level, mail-level, discrepancy, and recovery/idempotency artifacts are defined in `docs/report-schemas.md` and are normative for all workflow outputs.

### Import workflow keyword-governance contract (normative)
For `import_btb_lc`, the fabric-subject keyword list must be managed as versioned rule data rather than ad hoc constants.

- Canonical source path: `rules/import_btb_lc/keywords.yaml`.
- The file must contain:
  - `revision` (string, required)
  - `include_keywords` (array of case-insensitive substrings, required)
  - `exclude_keywords` (array, optional; evaluated after include match)
- Matching policy:
  1. subject is normalized by trim + whitespace collapse + ASCII case-folding to lowercase
  2. include pass requires at least one include keyword hit
  3. any exclude hit after include pass makes the mail ineligible
- `mail_report.import_keyword_revision` must equal `revision` from the loaded keyword file for every processed import mail.
- Loader failures (missing file, invalid schema, empty include list) are startup hard failures for `import_btb_lc`.
