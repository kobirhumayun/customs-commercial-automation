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

Policy precedence note (phase 1): if a case is unspecified, ambiguous, or not fully satisfied by explicit rule conditions, the outcome must be `hard_block` with comprehensive reporting (no human-review routing in phase 1).

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

### Batch write contract (normative)
Batch atomicity applies only to mails with approved staged write operations, not to all mails in the run snapshot.
If one mail in the run snapshot is blocked while others are approved, the blocked mail contributes no workbook writes and each approved mail still participates in the same atomic commit of the approved staged write set.
Example run (3 mails): Mail A = blocked, Mail B = approved (2 staged writes), Mail C = approved (1 staged write) ⇒ batch write outcome: commit Mail B + Mail C writes together (3 total) in one atomic transaction; commit none if that transaction fails; Mail A writes remain zero.

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
   - resume must skip any group whose completion marker exists and is hash-consistent
   - resume may print only groups without completion markers
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

## Export LC/SC intake

### Inputs
- Outlook folder: `working` after operator triage from `temp-export`; snapshot all messages in the folder when the CLI is triggered
- ERP report: `rptDateWiseLCRegister`
- Attachments: LC/SC PDFs and PI PDFs

### Deterministic checks
- parse subject into document type, LC/SC end sequence, buyer, and optional suffix
- extract all body file numbers matching `P/<yy>/<nnnn>`
- validate every extracted file number through ERP lookup and pathing rules while retaining all file numbers for audit
- define LC/SC family consistency using LC/SC number, normalized buyer, and LC/SC date
- canonical row selection follows ERP row order
- the first occurrence row is the canonical row for that file number/family context
- canonical row fields drive folder path construction, workbook mapping, and reporting metadata
- duplicate true-equivalent ERP rows do not alter canonical selection once the first occurrence is chosen
- hard-block if the extracted file numbers do not resolve to the same LC/SC family; any partial family match is a hard block
- normalize ERP buyer name by splitting on `\`, trimming whitespace, and trimming trailing periods
- hard-block if normalized subject buyer and LC/SC number do not exactly match ERP-derived values
- identify base/amendment context from ERP `Amd No`, clause text, and attachment naming patterns

Example (canonical selection): if two ERP rows are true-equivalent for `P/26/0042` and appear as row 118 then row 241, row 118 remains canonical and its fields are used for folder pathing, workbook mapping, and reporting metadata.

### Workbook mapping
Use ERP fields to populate:
- `Name of Buyers` ← `Buyer Name`
- `L/C Issuing Bank` ← `Notify Bank`
- `L/C & S/C No.` ← `LC No.`
- `LC Issue Date` ← `LC DT.`
- ` Amount` ← `Current LC Value`
- `Shipment Date` ← `Ship. DT.`
- `Expiry Date` ← `Expiry DT.`
- `Quantity of Fabrics (Yds/Mtr)` ← `LC Qty`
- `L/C Amnd No.` ← `Amd No`
- `L/C Amnd Date` ← `Amd DT`
- `Lien Bank` ← `Nego Bank`
- `Master L/C No.` ← `Master LC No.`
- `Master L/C Issue Dt.` ← `M.L/C Date`
- `Commercial File No.` ← `File No.`

### No-write rules
- subject mismatch
- any extracted file number is missing its required ERP row
- any partial family match across LC/SC number, normalized buyer, and LC/SC date
- duplicate file number already present when workflow expects skip
- ambiguous document identity not resolved by rules
- any incomplete validation needed for append/skip decision

### Batch execution behavior
- blocked emails remain in `working`
- successfully processed export-team emails move to `UD and LC` only after the batch workbook-write and batch print phases finish
- print batches are built from successful mails in the active run snapshot, using only newly saved PDFs

## UD / IP / EXP processing

During the initial live-deployment phase, any mismatch, unknown exception, or incomplete rule condition should hard-block with a comprehensive report rather than route to human review.

### Inputs
- Outlook folder: `working`; snapshot all messages in the folder when the CLI is triggered
- PDF attachments for UD, EXP, and/or IP
- Existing master workbook rows for the same LC/SC family

### Shared-column behavior
- Column `UD No. & IP No.` stores UD/EXP/IP values together.
- UD entries have no prefix.
- EXP entries use `EXP: ` prefix.
- IP entries use `IP: ` prefix.
- When both EXP and IP exist, EXP must be listed before IP.
- Multiple entries are line-break separated.

### UD allocation logic
- extract UD number, date, LC/SC number, quantity, quantity unit, and relevant values
- find candidate rows for the LC/SC family
- select the first occurrence of each required row value, even when combinations are non-sequential
- maintain a multiset/bag of remaining values so duplicates are handled correctly
- write UD number to matched rows only if quantity rules are satisfied
- ignore excess quantity only when excess is at least 50 yards/meters; otherwise hard-block

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
- any print failure must be reported with retry/review metadata
