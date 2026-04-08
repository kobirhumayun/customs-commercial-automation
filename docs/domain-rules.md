# Domain Rules and Invariants

## Global invariants
- Never modify the master workbook unless all required validation rules pass.
- If rules are incomplete, contradictory, or not satisfied, do not write; produce a discrepancy report instead.
- All workflows must be idempotent and safe to rerun.
- Each run must begin from a fixed snapshot of the messages currently present in `working` for that workflow.
- At the start of any write-capable tool run, create a backup of the target yearly master workbook.
- New documents must never overwrite existing local files.
- Focus on new PDFs only.
- All extracted file numbers from an email remain in the report for traceability and must be validated as belonging to the same LC/SC family before processing can continue. Family consistency is determined by LC/SC number, normalized buyer, and LC/SC date.
- Workbook write execution is all-or-nothing per run-level batch.
- Writes are allowed only into target cells that were validated as blank during pre-write validation (including append targets that are blank by construction).
- If a crash/interruption occurs during the write phase, the run must be marked uncertain/incomplete.
- In uncertain/incomplete write state, printing and email moves are hard-blocked.
- Any rerun after uncertain/incomplete write state must begin with a recovery check against the workbook backup and the recorded staged write plan before any new write attempt.

## File storage rules

### Export path
`Year (LC/SC opening year) / Buyer Name / LC Number or Sales Contract Number / All Attachments`

### Import path
Designated import root organized by year.

### Duplicate rule
A duplicate PDF is defined only by identical filename.

## Subject and naming rules

## Canonicalization profiles (normative)
All matching-critical identifiers must be canonicalized before any equality or family-consistency checks.
If canonicalization fails for any required identifier, the outcome is `hard_block`.

### Shared canonicalization primitives
Apply these primitives only when the identifier profile explicitly references them:
1. trim leading/trailing whitespace
2. collapse internal runs of whitespace to a single space
3. uppercase ASCII letters
4. replace Unicode dashes (`–`, `—`, `‑`) with `-`
5. remove zero-width characters and non-printable control characters
6. normalize slash variants (`\` → `/`) for file-number style tokens
7. reject if result is empty

### File number profile (`P/<yy>/<nnnn>`)
Accepted raw forms (examples): `P/26/0042`, `p-26-42`, ` P\26\0042 `.

Normalization steps (exact order):
1. apply shared primitives 1, 3, 4, 5, 6
2. replace `-` with `/`
3. extract exactly three segments
4. segment 1 must equal `P`
5. segment 2 must be 2 digits (`yy`)
6. segment 3 must be 1-4 digits; left-pad with zeros to 4 digits
7. output canonical as `P/<yy>/<nnnn>`

Invalid patterns:
- missing/extra segments
- non-numeric year/sequence
- sequence longer than 4 digits

Match rule: exact canonical string equality.

### LC/SC number profile
Accepted raw forms must begin with `LC` or `SC` prefix and include a non-empty body.

Normalization steps (exact order):
1. apply shared primitives 1, 2, 3, 4, 5
2. split prefix token (`LC` or `SC`) from the rest
3. strip separators around prefix/body boundaries
4. preserve alphanumeric body characters and internal `-`
5. collapse repeated `-` to single `-`
6. trim trailing separators
7. output canonical as `<PREFIX>-<BODY>`

Invalid patterns:
- prefix not `LC` or `SC`
- empty body after normalization

Match rule: exact canonical equality.

### PI number profile
Accepted raw forms are PI references such as `PDL-YY-NNNN` with optional revision token `R<digits>`.

Normalization steps (exact order):
1. apply shared primitives 1, 3, 4, 5
2. parse base tokens `PDL`, `YY`, `NNNN`
3. left-pad numeric serial to 4 digits
4. if revision exists, normalize to `R<digits>` (no leading `+`, no spaces)
5. output canonical as `PDL-<YY>-<NNNN>` optionally followed by `-R<digits>`

Invalid patterns:
- base token not `PDL`
- missing `YY` or serial
- non-numeric `YY`/serial/revision digits

Match rule: exact canonical equality.

### UD / IP / EXP document number profile
Accepted raw forms may include mixed separators and spacing around prefixes (`UD`, `IP`, `EXP`).

Normalization steps (exact order):
1. apply shared primitives 1, 2, 3, 4, 5
2. normalize prefix token to one of `UD`, `IP`, `EXP`
3. normalize separators to single `-` between core tokens
4. trim trailing punctuation (`.`, `,`, `;`, `:`)
5. output canonical `<PREFIX>-<NORMALIZED_BODY>`

Invalid patterns:
- unknown prefix
- empty normalized body

Match rule: exact canonical equality for same prefix class.

### Buyer name profile
Accepted raw forms may include trailing punctuation and optional address segment in ERP field.

Normalization steps (exact order):
1. apply shared primitives 1, 2, 3, 4, 5
2. if ERP source contains `\`, keep only segment before first `\`
3. remove trailing periods from resulting buyer text
4. collapse repeated punctuation separators to single space
5. output canonical buyer string

Invalid patterns:
- empty buyer after normalization

Match rule: exact canonical equality.

### Worked examples (raw → canonical)
1. `p/26/42` → `P/26/0042`
2. ` P-26-0042 ` → `P/26/0042`
3. `P\26\7` → `P/26/0007`
4. `LC  -0038` → `LC-0038`
5. `sc-010-pdl-8` → `SC-010-PDL-8`
6. `pdl-26-42` → `PDL-26-0042`
7. `PDL-26-0042-r4` → `PDL-26-0042-R4`
8. `ip lc 0043 vintage denim studio ltd.` → `IP-LC-0043-VINTAGE DENIM STUDIO LTD`
9. `exp-  9981 ;` → `EXP-9981`
10. `Designer Fashion Ltd.\Dhaka.` → `DESIGNER FASHION LTD`

### Export subject parsing targets
- prefix: `LC` or `SC`
- LC/SC number end sequence
- buyer name
- optional suffixes such as `_ACK` and amendment markers

### Supported examples
- `LC-0515-L-DESIGNER FASHION LTD_ACK`
- `LC-0038-ANANTA GARMENTS LTD_AMD_05`
- `SC-010-PDL-8-ZYTA APPARELS LTD`
- `LC-05NU384-DESIGNER FASHION LTD`
- `LC-092387-COMTRADING APPAREL`

### Naming conventions assumed reliable in phase 1
- PI: `PDL-YY-NNNN` with optional revision like `R4`
- UD: `UD-LC/SC-LC/SC_ENDSEQ-BUYERNAME_EXTENSION`
- LC standard/amendment/ack patterns as documented
- IP example: `IP-LC-0043-VINTAGE DENIM STUDIO LTD`
- EXP example: `[Invoice]-EXP [Extension]`

## OCR confidence thresholds and fallback policy (normative)
Required-field OCR thresholds are explicit and workflow-bound:

| workflow/document class | required field | min confidence |
|---|---|---:|
| export LC/SC docs | LC/SC number | 0.98 |
| export LC/SC docs | PI reference | 0.95 |
| UD docs | UD number | 0.97 |
| IP docs | IP number | 0.97 |
| EXP docs | EXP number | 0.97 |
| import BTB LC docs | BTB LC number | 0.98 |
| import BTB LC docs | BTB LC value | 0.96 |

Extraction order is mandatory:
1. text-layer PDF parse
2. OCR pass for scanned/hybrid pages
3. table extraction fallback for required tabular fields

Decision rules:
- OCR- or parser-derived LC/SC and PI fields are informational only in phase 1 and never hard-block export processing.
- Non-required low-confidence fields may emit `ocr_non_required_field_low_confidence` warning if all required fields pass.

## ERP normalization rules
- ERP report is `RptCommercialExport/DateWiseLCRegisterForDocuments`.
- Headers are read from row 2 of sheet 1.
- Canonical row selection follows ERP row order.
- The first occurrence row is the canonical row for that file number/family context.
- Canonical row fields drive folder path construction, workbook mapping, and reporting metadata.
- ERP `LC No.` is preserved as the canonical family number exactly as exported, after shared string normalization. It is not constrained to mail-subject-style `LC-...` or `SC-...` patterns.
- ERP `LC DT.` is canonicalized to ISO `YYYY-MM-DD` for internal storage/pathing.
- Duplicate true-equivalent ERP rows do not alter canonical selection once the first occurrence is chosen.
- When multiple file numbers are extracted from one email, each must be validated against ERP and all resolved rows must be consistent with the same LC/SC family.
- Any partial family match is a hard block.
- `Buyer Name` may contain an address separated by `\`; normalize by taking the buyer segment, trimming whitespace, and removing trailing periods.
- Mail-subject and PDF comparisons against ERP are advisory only until separately codified; ERP rows selected by extracted file numbers are the final phase-1 source for workbook values and folder path construction.

Example (canonical selection): if two true-equivalent ERP rows for `P/26/0042` are found at row 118 and row 241, row 118 is canonical and row 241 cannot replace it for pathing, workbook mapping, or reporting metadata.

## Master workbook rules
- One master workbook per year.
- Headers are in row 2 of sheet 1.
- Export workflow generally appends new rows.
- Before appending, check whether the same file number already exists.
- If the same file number exists, skip that file.
- The uniqueness contract for export writes is the canonical workbook file number in `Commercial File No.`.
- Phase-1 operating assumption: once an automation cycle starts, no manual or external workbook edits occur until that cycle finishes.
- Under that operating assumption, duplicate prevention is enforced by canonical file number across:
  - repeated mentions of the same file in one mail body
  - different mails within the same run
  - files already present in the workbook before the run starts
- A duplicate file must never produce a second staged workbook row in the same run.
- A file already present in `Commercial File No.` must never be written again as a new workbook row.
- If required, first attempt to locate an existing row for the same file/amendment to avoid duplicate insertion.
- Operational ordering is row sequence and drives staged write ordering, reporting, and print ordering.
- If a mail resolves only to file numbers already present in `Commercial File No.`, the workflow outcome is `duplicate_only_noop`.
- `duplicate_only_noop` means:
  - no workbook writes
  - no print requirement
  - mail remains eligible for deterministic post-run movement when validation otherwise passes
- This duplicate-only terminal path is valid only because workbook uniqueness by canonical file number is the final contract for export processing.

### Workbook mapping contract (normative)
Implementations must resolve workbook targets from exact row-2 header text on sheet 1 and then stage writes using canonical `column_key` names. Header aliases are allowed only where explicitly listed.
Duplicate header text is disallowed by default unless explicitly declared in this contract with fixed column indexes.

#### Mapping matrix — shared/core fields
| column_key | Required header text (exact) | Allowed aliases | Source | Write mode | Pre-write constraint |
|---|---|---|---|---|---|
| `file_no` | `Commercial File No.` | `File No.`, `FILE NO`, `File Number` | Email body canonical file number | `append_only` (export) / `update_if_blank` (others) | target blank unless explicitly update-only workflow rule allows replacement |
| `lc_sc_no` | `L/C & S/C No.` | `L/C No.`, `LC/SC No.`, `LC No.` | ERP canonical family field | `append_only` / `update_if_blank` | target blank |
| `buyer_name` | `Name of Buyers` | `Buyer Name`, `Buyer` | ERP canonical buyer | `append_only` / `update_if_blank` | target blank |
| `ud_ip_shared` | `UD No. & IP No.` | none | UD/IP/EXP extraction and ordering rules | `update_if_blank_or_append_multiline` | existing value may be preserved and line-extended only by deterministic rule pack |
| `up_no` | `UP No.` | `UP` | workflow filters only in phase 1 | `never_write` (except future approved workflow) | n/a |

#### Mapping matrix — workflow-specific minimum fields
| workflow_id | column_key | Required header text (exact) | Write mode | Required preconditions |
|---|---|---|---|---|
| `export_lc_sc` | `quantity_fabrics` | `Quantity of Fabrics (Yds/Mtr)` | `append_only` | ERP unit/value available; target blank |
| `export_lc_sc` | `export_amount` | `Amount` (column 6) | `append_only` | ERP current LC value available; target blank |
| `ud_ip_exp` | `ud_ip_shared` | `UD No. & IP No.` | `update_if_blank_or_append_multiline` | candidate rows selected by deterministic tie-break contract |
| `import_btb_lc` | `btb_lc_no` | `BTB L/C No.` | `update_if_blank` | row matches export LC + BTB value 40%-80% rule |
| `import_btb_lc` | `import_lc_amount` | `Amount` (column 22) | `update_if_blank` | row passed import LC candidate matching and BTB value validation |
| `bb_dashboard_verification` | `dashboard_status` | `B. Bangladesh Bank Status` | `update_if_blank_or_replace_non_compliant` | row eligible by workflow filters |

If a required header is missing, duplicated ambiguously, or maps to multiple candidate columns outside an explicitly declared duplicate-header exception, outcome is `hard_block` with discrepancy code `workbook_header_mapping_invalid`.

## Workbook preservation rules
These must remain exactly as-is after writes:
- formulas
- styles
- merged cells
- conditional formatting
- filters
- comments
- validations
- workbook protection

## Quantity formatting rule
For `Quantity of Fabrics (Yds/Mtr)`:
- if ERP `LC Unit` is `YDS`, preserve existing number format
- if ERP `LC Unit` is `MTR`, apply `#,###.00 "Mtr"`

## Export amendment rules
- A new file is typically created for each new export LC/SC or for some amendments.
- A file may contain the base LC and one or more amendments if received together.
- A single file must never contain two different LCs.
- Some amendments create a new file only when they independently increase value/quantity with new PI coverage.
- Dependent amendments may be added to an existing file instead of receiving a new file.

## UD/IP/EXP shared column rule
Column `UD No. & IP No.` stores:
- UD numbers for local buyers with no prefix
- `EXP: ` prefixed values for EXP
- `IP: ` prefixed values for IP
- EXP listed before IP when both exist
- multiple values separated by line breaks

## UD candidate combination determinism rule
When multiple valid UD row combinations satisfy the same extracted quantity, selection must be deterministic and fully reportable.

### Tie-break key order (normative)
Apply keys in this exact order:
1. **Row-index key (ascending):**
   - Compare each candidate by lexicographically sorted row indexes; smallest wins.
2. **Amendment recency key (older first):**
   - Compare normalized amendment tuples (`L/C Amnd Date` asc with blank oldest, then `L/C Amnd No.` asc with blank = `0`).
3. **Blank-field priority key:**
   - Prefer higher count of pre-write blank UD target cells; if tied, prefer fewer non-target populated optional cells.
4. **Stable candidate-id key:**
   - Candidate id is `"-"`-joined sorted row indexes; lexicographically smallest id wins.

### Equal-score outcome rule
- If all tie-break keys are equal between two or more candidates, the outcome is `hard_block` (no write).
- Required discrepancy code: `ud_candidate_tie_after_full_tiebreak`.
- The discrepancy report must include compared candidates and key values for operator traceability.

### Required report fields for candidate selection
Mail-level report payload must include:
- required quantity + unit
- total candidate count
- per-candidate row list, matched quantities, and each tie-break key value
- selected/non-selected status per candidate and rejection reasons
- final decision + final decision reason

### Worked example (duplicated quantities, non-sequential matches)
For extracted UD quantity `3000 YDS`, candidate rows:
- row 11 (`1000`, amnd date `2026-01-02`, amnd no `1`)
- row 14 (`1000`, amnd date `2026-01-02`, amnd no `1`)
- row 19 (`2000`, amnd date `2026-02-10`, amnd no `2`)
- row 27 (`2000`, amnd date `2026-02-10`, amnd no `2`)

Valid non-sequential combinations are `[11,19]`, `[14,19]`, `[11,27]`, `[14,27]`.
Applying keys in order selects `[11,19]` because:
1. row-index key eliminates `[14,*]`,
2. amendment key ties between `[11,19]` and `[11,27]`,
3. blank-field priority assumed tie,
4. stable candidate id `11-19` sorts before `11-27`.

## Bangladesh Bank rules
Verification uses:
- workbook LC/SC number and master LC number
- ERP totals across amendments for value, quantity, and net weight
- shipment and expiry dates from final amendment rows

The dashboard column is verification-only and should not be used to drive other writes in phase 1.

## Confirmed phase 1 exclusions
- Buyer-type inference for UD/IP/EXP is excluded entirely from phase 1.
- Human-review routing is excluded from the initial live-deployment path; unspecified failures default to hard block with comprehensive reporting until recurring issue categories are formally classified.

## Initial exception-handling rule
- Any naming mismatch, unsupported rule exception, or partially specified case must hard-block and produce a comprehensive discrepancy report during early deployment.
- Business-approved exceptions should be implemented only inside workflow-specific rule-pack modules and should run after standard validation rules.
- `warning` decisions are permitted in phase 1 only for explicitly defined, non-blocking exceptions where all required validation parameters still pass.
- Warning-only outcomes may continue through downstream stages (workbook write when applicable, print, and post-run mail move) and must be captured in discrepancy/audit reporting with rule IDs and rationale.
- If both `warning` and `hard_block` are present for the same mail, hard-block takes precedence and no write/print/mail-move is allowed for that mail.

### Warning-only examples (phase 1)
- Subject/attachment token formatting differs cosmetically (such as extra separators) but required identifiers and normalized entities match ERP/workbook context.
- Buyer text includes harmless punctuation/case variation while canonical normalization confirms an exact business-entity match.
- Email includes an extra non-required PDF that is ignored, while required documents pass extraction and all mandatory write gates.

## Import relevance rule
- Fabric-related import emails are identified by case-insensitive substring matching against a versioned rules data file: `rules/import_btb_lc/keywords.yaml`.
- Required keys in `keywords.yaml`:
  - `revision` (string)
  - `include_keywords` (non-empty array of non-empty strings)
  - `exclude_keywords` (array, optional)
- Subject matching sequence:
  1. normalize subject with trim + whitespace collapse + lowercase
  2. require at least one include-keyword hit
  3. reject if any exclude-keyword hit is present
- Startup must hard-fail before run snapshot/side effects if file is missing, malformed, or include list is empty.
- The active `revision` value must be stamped as `import_keyword_revision` in run and mail reports.
- Keyword-list changes must follow code-review and release boundaries; no ad hoc runtime edits.

## Staged run execution rule
- A run snapshots all candidate emails before validation and side effects begin.
- Validation outcomes are decided per mail, but workbook writes, printing, and email moves execute in controlled post-validation phases.
- One blocked mail does not invalidate unrelated successful mails in the same run.
- Batch atomicity applies only to mails with approved staged write operations, not to all mails in the run snapshot.
- If one mail in the run snapshot is blocked while others are approved, the blocked mail contributes no workbook writes and each approved mail still participates in the same atomic commit of the approved staged write set.
- Example run (3 mails): Mail A = blocked, Mail B = approved (2 staged writes), Mail C = approved (1 staged write) ⇒ batch write outcome: commit Mail B + Mail C writes together (3 total) in one atomic transaction; commit none if that transaction fails; Mail A writes remain zero.
- Duplicate suppression is file-number-driven, not subject/body identity-driven.
- If two mails in the same run resolve to the same canonical file number, only the earliest eligible mail in deterministic `mail_iteration_order` may stage that file; later mails must skip it.
- The run initialization stage must capture both the email snapshot and a master-workbook backup before write-capable phases continue.
- The workbook write stage must commit as an all-or-nothing batch from the approved staged write plan.
- If write state is uncertain/incomplete, downstream print and mail-move stages must not run.
- Rerun entry must perform recovery validation using the backup artifact plus recorded staged write plan before allowing new writes.
- Print execution must persist deterministic per-group progress markers.
- If a print group is interrupted after some PDFs are printed, recovery/resume may continue only from the remaining document suffix recorded by the partial print marker.
- If physical paper output occurred during an Acrobat timeout, operators may advance the recorded print prefix with `acknowledge-partial-print` before resuming.
- If operators confirm that all PDFs in the planned print group physically printed, `acknowledge-partial-print` may finalize the marker as `completed`; one final `execute-print` pass is still required to close run metadata without sending more print commands.
- If physical print output occurred but no partial/completed print marker was persisted, the run remains a manual recovery boundary and mail moves must stay blocked.

## Rerun/recovery hash invariants
- Recovery hash algorithm is fixed to **SHA-256** for:
  - run-start backup (`run_start_backup_hash`)
  - current workbook (`current_workbook_hash`)
  - canonical staged write plan (`staged_write_plan_hash`)
- Hash encoding is lowercase hexadecimal (64 chars), not base64.
- Persisted metadata must include:
  - `hash_algorithm` = `sha256`
  - `run_start_backup_hash`
  - `current_workbook_hash`
  - `staged_write_plan_hash`
- Staged write plan hashing must use canonical serialization before SHA-256:
  - deterministic operation ordering by `(mail_iteration_order, operation_index_within_mail)`
  - stable object key order (lexicographic ascending)
  - UTF-8 byte encoding
  - LF (`\n`) normalized line endings
- Any missing hash field, unknown algorithm value, non-hex digest, or canonicalization failure is a hard block for rerun/recovery.

## Outlook post-processing rule
- Blocked emails remain in `working`.
- Successfully processed export-team emails move to `UD and LC` only after batch workbook writes and printing complete.
- Successfully processed import-team emails move to `Import` only after batch workbook writes and printing complete.

## Open questions that remain intentionally unresolved
- Any future business-approved exceptions to the documented value/quantity matching constraints or naming conventions that have not yet been encoded in workflow-specific rule-pack modules.

## Discrepancy code registry reference
- All discrepancy `code` values must come from `docs/discrepancy-codes.md`.
- New codes must be added to the registry before implementation changes that emit them.

## Import keyword lifecycle and release gate (normative)
To keep import relevance deterministic and auditable, keyword changes must follow a documented lifecycle.

### Change control checklist
Every pull request that modifies `project/workflows/import_btb_lc/keywords.py` must include:
1. updated `IMPORT_KEYWORD_REVISION` matching `YYYY-MM-DD.N`
2. rationale for each added/removed keyword
3. business-domain approval note
4. test evidence for positive and negative subject matches
5. impact statement for potential over-broad keyword collisions

### Minimum validation test matrix
At minimum, tests must cover:
- expected-match subjects for every newly added keyword
- expected-non-match subjects that are lexically similar but should not match
- case-insensitive matching behavior
- duplicate keyword normalization behavior
- startup hard-fail paths for missing/malformed `IMPORT_SUBJECT_KEYWORDS`
- startup hard-fail path for malformed `IMPORT_KEYWORD_REVISION`

Suggested fixture location for implementation: `tests/workflows/import_btb_lc/fixtures/`.

### Overlap and breadth policy
- Substring overlaps are allowed only when both keywords are intentionally retained and tested.
- Any newly added generic token (for example, one-word high-frequency terms) requires an explicit false-positive risk note in PR description.
- If risk cannot be mitigated by deterministic keyword constraints, change must be deferred and treated as unresolved requirement.

### Release gate requirements
- Keyword changes become active only after merge + deployment release boundary.
- Runtime/manual editing on operator machines is prohibited.
- Run-level and mail-level outputs must stamp `import_keyword_revision` for every relevance decision, including blocked mails.
