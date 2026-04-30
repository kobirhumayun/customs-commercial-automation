# Architecture Overview

## 1. Executive summary
The target solution is a **Windows-first, monolithic but internally modular Python application** that exposes a set of **manually triggered CLI tools** for customs/commercial document workflows. This shape fits the business because the automations depend on local desktop integrations (Outlook COM, Excel, Adobe Acrobat, Playwright-driven ERP access, local file storage) and because the phase 1 objective is safe, deterministic automation with strong auditability rather than a distributed platform.

The architecture must optimize for:
- deterministic rule-based decisions before any write
- strict idempotency across intake, storage, workbook updates, and printing
- exact preservation of master workbook fidelity
- structured JSON reporting instead of dashboards in phase 1
- independent rollout of workflow-specific CLI tools within one codebase
- staged run execution so extraction/validation completes before workbook writes, printing, and mail moves

## 2. System context and module boundaries

### External dependencies
- Microsoft Outlook desktop via COM / `pywin32`
- Microsoft Excel desktop via `xlwings`
- Adobe Acrobat SDK for printing
- ERP portal via Playwright
- Local filesystem for attachments, reports, and state
- PDF/OCR stack via PyMuPDF, pdfplumber, Pillow, Tesseract, OCRmyPDF, and img2table
- Optional future PostgreSQL persistence

### Internal modules
1. **CLI command layer**
   - command dispatcher
   - workflow-specific entrypoints
   - operator execution context capture
2. **Workflow orchestrator**
   - coordinates shared services
   - manages job lifecycle, checkpoints, and no-write guarantees
3. **Outlook adapter**
   - inbox subfolder access
   - all-message intake from `working`
   - mail metadata and attachment extraction
4. **Document storage manager**
   - destination folder resolution
   - duplicate detection by filename
   - save-only-new-file guarantees
5. **ERP downloader**
   - Playwright login/navigation
   - `RptCommercialExport/DateWiseLCRegisterForDocuments` retrieval
   - normalization and row selection across all extracted file numbers, including family-consistency checks
6. **Parsing and normalization layer**
   - subject parsing
   - email body file-number extraction
   - buyer normalization
   - entity canonicalization for LC/SC, amendments, PI, UD, IP, EXP, BTB LC
7. **Document extraction pipeline**
   - text PDF extraction first
   - hybrid/scanned classification
   - OCR/table fallback
   - clause extraction and provenance capture
8. **Rule and validation engine**
   - hard-block rules
   - soft-warning rules
   - future human-review checkpoints
   - versioned rule packs by workflow
9. **Matching and reconciliation engine**
   - ERP-to-email matching
   - workbook candidate-row filtering
   - value/quantity combination logic
   - first-match or append/skip strategies depending on workflow
10. **Excel adapter**
    - workbook open/lock assumptions
    - header mapping
    - row reference tracking
    - surgical write operations with preservation controls
11. **Reporting and audit engine**
    - JSON report generation
    - discrepancy reports
    - write decision records
    - print sequencing metadata
12. **Printing engine**
    - batch creation by originating mail
    - row-sequence ordering
    - retries and review handling
13. **Configuration and secrets layer**
    - local config files for operator/environment settings
    - in-code versioned constants for phase 1 deterministic rule/keyword lists
    - credential storage strategy
    - environment-specific paths
14. **Future AI extension seam**
    - optional classifier/extractor interfaces that can be introduced without changing deterministic phase 1 flows

## 3. Staged execution model
Each manually triggered CLI run should follow one explicit execution contract:
1. **Run-level snapshot + workbook backup** â€” capture the complete set of messages currently in `working` for that workflow, bind them to the run id, and create a backup copy of the target yearly master workbook before any write-capable phase can proceed.
2. **Deterministic mail ordering** â€” sort the snapshotted messages by `ReceivedTime` converted to the configured workflow state timezone (current deployment basis: Bangladesh Standard Time, UTC+06:00), then tie-break by ascending Outlook `EntryID`; persist this ordered list in run metadata before mail processing starts.
3. **Mail-level validation** â€” iterate the ordered snapshot, save only new PDFs, extract entities, validate against ERP and workbook context, and build proposed write/print/move outcomes per mail.
4. **Batch workbook write** â€” open the yearly master workbook in one controlled write session and apply only the approved write operations for mails that passed validation.
5. **Batch print planning + print** â€” derive mail-group print order from master-workbook row sequence (group key = originating mail, group rank = earliest row sequence written for that mail), persist final print-group order in run metadata, then print only newly saved PDFs for each group without extra intra-group sorting and insert exactly one blank page between consecutive groups.
6. **Post-run mail moves** â€” move only successful mails to their destination Outlook folders after workbook writes and printing complete; blocked mails remain in `working`.
7. **Rerun recovery gate** â€” if the prior run is marked uncertain/incomplete, perform recovery checks against the backup artifact and recorded staged write plan before any new write attempt is allowed, using the shared **Recovery Decision Matrix** contract defined in `docs/workflows.md` (required artifacts, exact outcomes, idempotency checks, and pseudocode flow).

This model is intentionally **run-level staged, but mail-level selective**: one blocked mail must not force unrelated validated mails in the same run to be discarded, yet no single mail may print or move ahead of the controlled workbook-write phase.

### Workbook contention protocol (normative)
Before any write-capable phase transitions to target pre-validation, the orchestrator must run workbook contention preflight checks:
1. verify workbook path exists and is writable by the current operator context
2. verify workbook is not opened in a conflicting write session (read-only fallback is treated as contention)
3. verify Excel adapter session is healthy and save-capable
4. persist preflight evidence in run metadata

If contention is detected, outcome is `hard_block` (no write attempt), with deterministic discrepancy coding and zero downstream print/mail-move execution.

#### Contention retry envelope
- Retries are allowed only for transient adapter/session initialization failures.
- Default retry policy for transient checks:
  - max attempts: 3
  - backoff: fixed 5 seconds
- Lock/read-only/share violations are not retried in the same run; they hard-block immediately.

#### Required discrepancy codes for contention events
- `workbook_lock_conflict`
- `workbook_open_readonly`
- `excel_adapter_unavailable`
- `workbook_save_conflict`

#### Decision table (normative)
| Scenario | Immediate action | Run outcome |
|---|---|---|
| workbook locked by another process/user | stop before pre-validation | `hard_block` |
| workbook opens read-only | stop before pre-validation | `hard_block` |
| Excel adapter unavailable after retry envelope | stop before pre-validation | `hard_block` |
| save conflict detected during write phase | set uncertain state | `uncertain_not_committed` + recovery gate |

### Batch write contract (normative)
Batch atomicity applies only to mails with approved staged write operations, not to all mails in the run snapshot.
If one mail in the run snapshot is blocked while others are approved, the blocked mail contributes no workbook writes and each approved mail still participates in the same atomic commit of the approved staged write set.
Example run (3 mails): Mail A = blocked, Mail B = approved (2 staged writes), Mail C = approved (1 staged write) â‡’ batch write outcome: commit Mail B + Mail C writes together (3 total) in one atomic transaction; commit none if that transaction fails; Mail A writes remain zero.

### Explicit write transaction strategy (normative)
Write-capable workflows must execute workbook mutations using one staged protocol so recovery and audit logic can unambiguously classify run state.

#### 1) Staged write application protocol
Before any cell mutation, the orchestrator must:
1. assemble the full staged write set for all approved mails in deterministic operation order `(mail_iteration_order, operation_index_within_mail)`
2. pre-validate every target cell against staged preconditions (sheet exists, row/column address exists, expected pre-write value constraints, and row-eligibility constraints)
3. persist pre-write phase metadata and target manifest hashes

If any target pre-validation fails, no cell writes are allowed and the batch is hard-blocked.

When pre-validation passes, the Excel adapter must:
1. apply writes strictly in the same deterministic order used for staging
2. persist phase markers as the run advances (`write_phase_status`)
3. persist deterministic target probes sufficient to classify each target as pre-write/post-write/unknown during recovery

#### 2) Commit marker creation point and required metadata
The commit marker must be written only after:
- all staged operations were applied without adapter/runtime error
- post-write probes for all staged targets confirm expected post-write values
- workbook save/sync for the active write session returns success

At that point, set `write_phase_status=committed` and persist a commit marker record with at least:
- `run_id`, `workflow_id`, `tool_version`, `rule_pack_version`
- `committed_at_utc` (ISO-8601 UTC timestamp)
- `operation_count`
- `mail_iteration_order` digest/reference
- `staged_write_plan_hash` (SHA-256, canonical serialization contract)
- `run_start_backup_hash`
- `post_write_probe_summary` (counts for `matches_post_write`, `matches_pre_write`, `mismatch_unknown`)

#### 3) Failure window behavior and recovery interpretation
- **Failure before commit marker**: treat as uncommitted (`write_phase_status` in `not_started`, `prevalidated`, `applying`, or `uncertain_not_committed`). Recovery must require target probes and may allow only `safe reapply staged writes` when all targets still match pre-write expectations.
- **Failure after commit marker**: treat as committed write intent. Recovery must require all staged targets to probe as post-write and then only resume downstream phases (print/mail-move) via idempotency gates.
- **Any contradiction** between marker/phase metadata and probe evidence is a hard block requiring manual recovery.

#### 4) Minimum required probe granularity
Probe granularity must be **per staged target cell** (not per row, per sheet, or aggregate-only).
Each staged target probe record must include:
- `sheet_name`
- `row_index`
- `column_key` (or canonical column index)
- `expected_pre_write_value`
- `expected_post_write_value`
- `observed_value`
- derived classification (`matches_pre_write`, `matches_post_write`, `mismatch_unknown`)

Row-level or workbook-level checksum-only probes are insufficient for recovery safety.

## 4. Workflow architecture

### Export LC/SC intake CLI
- Operator moves eligible emails from `temp-export` to `working`.
- CLI snapshots all messages currently present in `working` and binds them to the active run before any side effects occur.
- Body parser extracts all file numbers matching `P/<yy>/<nnnn>`.
- Every extracted file number is used for ERP lookup and folder-path verification to confirm they all belong to the same ERP family.
- ERP downloader retrieves `RptCommercialExport/DateWiseLCRegisterForDocuments`, normalizes row-2 headers, and validates family consistency using ERP `LC No.`, normalized buyer, and canonicalized `LC DT.`. Duplicate ERP rows may use any one row when they are true duplicates. Any partial family match is a hard block.
- Mail subject parsing is optional and advisory only. ERP rows selected by extracted body file numbers are the final source for family data, workbook values, and storage path construction.
- Attachment saving persists all new PDF attachments into the export folder hierarchy; LC/SC and PI extraction/classification signals are informational only and do not gate run success.
- Storage manager saves only new PDFs into export folder hierarchy:
  `Year / Buyer Name / LC-or-SC Number / All Attachments`.
- Excel adapter appends or skips based on file number existence and amendment matching rules.
- Reporting engine emits structured results for each mail and the overall run.
- Validated export write operations are staged and applied during the batch workbook-write phase.
- Successfully processed export-team emails move from `working` to the Outlook folder `UD and LC` only during the post-run mail-move phase; blocked emails remain in `working`.

### UD / IP / EXP CLI
- Shares intake, storage, and parsing services with export workflow.
- Mail composition follows a fixed contract: one mail may contain only UD documents (single or multiple), only EXP documents, or EXP documents together with IP documents. A mail containing IP must also contain EXP, and a mail mixing any UD document with any IP/EXP document is invalid.
- Processes only the LC/SC family confirmed by validating all extracted email-body file numbers against ERP data.
- Saves only new PDFs and records all saved paths.
- Email subject text is not authoritative for family resolution; the body file number selects the canonical ERP row, and the ERP row supplies LC/SC, buyer, LC/SC date, and `Ship. Remarks`.
- The UD/IP/EXP document reader is filename-gated: only PDFs beginning `UD-`, beginning `IP-`, or whose filename stem is exactly one or more digits followed by `-EXP` are processed as UD/IP/EXP documents. For EXP, `123-EXP.pdf` is accepted while `123-EXP-INVOICE.pdf` is skipped so the workflow prioritizes the machine-generated text-layer file over scanned descriptor variants.
- Explicit `UD-LC-<suffix>` or `UD-SC-<suffix>` filename evidence is a guardrail only: it must agree with the ERP-derived LC/SC suffix, and it must never replace email-body file-number plus ERP family resolution.
- Structured Base UD PDFs are identified by `UD Authenticating Authority`; structured UD Amendment PDFs are identified by `Amendment Authenticating Authority`.
- Structured UD extraction must locate the page-1 UD/AM identifier row strictly by the office-use-only label: Base UD uses `UD No (For office use only)` and UD Amendment uses `Amendment no. (For office use only)`. The `For office use only` text is mandatory, no alternate row-label fallback is allowed, and the UD/AM number plus document date must both come from that same matched row. If the extracted row value does not align with the BGMEA UD/AM pattern, the mail hard-blocks.
- Structured UD extraction uses ERP `Ship. Remarks` first, then ERP `LC No.`, to locate the UD/AM LC table row. `Ship. Remarks` matching remains exact. ERP `LC No.` matching is exact first; if exact matching fails, only leading zeros on the left side may be stripped from the ERP/table LC strings for comparison. Leading and trailing spaces around compared values may be trimmed, but internal spaces must remain unchanged. No punctuation, separators, internal zeros, or other characters may be modified. The matched row supplies LC/SC date and value, while ERP LC/SC remains the family value used for storage and workbook matching.
- For structured UD Amendments only, the LC table value normally comes from the `Increased/Decreased` column. If that extracted amendment value is numeric zero, the LC is treated as newly included in the amendment and the workflow uses the row's `Value` column instead. Base UD value extraction continues to use the documented base-UD value column.
- Before staging a structured UD write, the workflow first checks for an already-recorded workbook group carrying the same UD/AM value in `UD No. & IP No.` plus the same `UD & IP Date`; if those rows also satisfy the same value and quantity checks, the mail becomes a duplicate-only no-op with no workbook write or print obligation.
- Structured UD validation requires the extracted LC/SC date to match ERP `LC DT.`, then walks blank workbook rows for the ERP family in ascending row order and accumulates workbook `Amount` column 6 until the extracted UD/AM value matches within tolerance. The current structured path does not run arbitrary row-combination search once value evidence is available.
- Structured UD quantity validation aggregates Pioneer Denim supplier rows by unit and compares only against the value-selected workbook row group.
- Workbook quantity units for structured UD validation come from the workbook quantity cell number format: `#,###.00 "Mtr"` means `MTR`; other formats default to `YDS`.
- Successful structured UD writes stage `UD No. & IP No.`, `UD & IP Date`, and `UD Recv. Date`; dates are written as `DD/MM/YYYY`.
- Structured UD writes stage only when every target cell for those three columns is blank; unexpected non-blank target cells hard-block instead of being appended or overwritten.
- Legacy UD payloads without structured value evidence may still use the older deterministic quantity-combination allocation path.
- IP/EXP processing remains blocked until business rules are finalized in durable docs.
- The current code formats shared-column values as plain UD numbers plus ordered `EXP: ` / `IP: ` prefixes for discrepancy evidence, but does not stage workbook writes for IP/EXP documents yet.
- Write is blocked if matching rules are incomplete, contradictory, or leave unresolved discrepancies under the defined thresholds.

### Import / BTB LC CLI
- Operator moves fabric-relevant emails from `temp-import` to `working`.
- Relevance is determined by case-insensitive substring matching against the fabric subject keyword list stored in code.
- New PDFs are saved into the designated import folder organized by year.
- Extraction returns BTB LC number/date/value, PI yarn quantity, and related export LC number from clauses.
- Candidate workbook rows are filtered by matching export LC with blank `UP No.` and blank BTB LC field.
- Strict validation selects the first row where BTB LC value falls between 40% and 80% of export LC value.
- One import LC populates exactly one workbook row.
- Successfully processed import-team emails move from `working` to the Outlook folder `Import` only during the post-run mail-move phase; blocked emails remain in `working`.

### Bangladesh Bank dashboard verification CLI
- Reads candidate rows where `UP No.` is blank, UD exists, and dashboard status is blank or not already compliant.
- Aggregates ERP amendments for LC value, quantity, and net weight.
- Uses Playwright login to inspect dashboard values.
- Writes verification-only results: `OK`, `OK (Kgs)`, or a combined discrepancy string.
- Does not populate any additional fields.

### Printing CLI/service
- Triggered automatically after the batch workbook-write phase succeeds for at least one mail in a write-capable workflow.
- Prints all newly saved PDFs from successful mails in the active run snapshot.
- Batches are grouped by originating mail and ordered by master-workbook row sequence captured from the staged write outcomes.
- Within each mail group, all newly saved PDFs are printed in saved/staged order with no extra intra-group sorting.
- Inserts exactly one blank page between consecutive mail groups.
- Persists both mail-iteration order and final print-group order in run JSON metadata for audit replay.
- Records retries, failures, and operator review requirements in JSON reports.

## 5. Canonical data model
Key entities that should exist in architecture and later in code contracts:
- `ProcessingJob`
- `EmailMessage`
- `Attachment`
- `SavedDocument`
- `FileNumber`
- `Buyer`
- `ExportLC`
- `SalesContract`
- `Amendment`
- `PI`
- `UD`
- `IP`
- `EXP`
- `ImportLC` / `BTBLC`
- `ERPRegisterRow`
- `MasterWorkbookRowReference`
- `ExtractionResult`
- `ValidationResult`
- `Discrepancy`
- `WriteOperation`
- `PrintBatch`

### Persistence split
- **Phase 1 local JSON**: jobs, run snapshots, extraction results, validation outputs, saved file paths, staged row targets, write decisions, print metadata, mail-move decisions, and operator context.
- **Future PostgreSQL**: searchable historical jobs, reconciliation indexes, rule/version lineage, cross-workflow analytics.

## 6. Rule engine and validation design
Rules should be represented as explicit, versioned rule packs keyed by workflow.

### Default workflow rule-pack contract
To keep behavior deterministic and auditable across all workflows, phase 1 should adopt one default contract for how rule packs are organized, executed, and reported.

#### Module layout
- One **workflow-specific rule-pack module** per CLI workflow (`export_lc_sc`, `ud_ip_exp`, `import_btb_lc`, `bb_dashboard_verification`).
- One **shared core validation module** that contains reusable baseline checks used by all workflows.

#### Execution order
1. Run shared core validations first.
2. Run workflow-specific exception logic second.

This ordering is mandatory so every workflow inherits the same baseline safety gates before any workflow-specific allowances are applied.

#### Standard rule-pack function contract
Each workflow rule-pack module should expose a single primary evaluator function with this conceptual contract:
- **Input context**: normalized workflow payload, extracted entities, ERP/workbook candidate context, run metadata, and rule-pack configuration/version reference.
- **Output decision list**: ordered list of decision records produced by rule evaluation.
- **Output metadata**: rule-pack identifier, semantic version, evaluation timestamp, and optional execution diagnostics.

#### Standard decision schema
Every decision record emitted by the shared core module and workflow-specific module should use a consistent schema with at least:
- `decision_type`: one of `hard_block`, `warning`, `applied_exception`
- `rule_id`: stable rule identifier
- `rationale`: human-readable explanation of why the decision was produced

#### Reporting requirements
Run-level and mail-level reports must capture rule-evaluation lineage fields:
- rule-pack name/id used for the workflow
- rule-pack version used at runtime
- ordered list of applied rule IDs (including core and workflow-specific sources)

These fields are required so discrepancy reports can be replayed and audited against exact rule logic.

#### Rule-pack discovery and loading at runtime
The orchestrator should resolve the active workflow name from the invoked CLI command, then load the mapped workflow rule-pack module via a deterministic registry (preferred) or explicit config mapping.
- Unknown workflow or missing mapping is a startup hard failure (no processing).
- Rule-pack version must come from a canonical module constant named `RULE_PACK_VERSION` in the resolved workflow rule-pack module.
- Startup must hard-fail if `RULE_PACK_VERSION` is missing, empty, non-string, or not a valid semantic version string (no processing).
- Required lineage metadata fields for both run-level and mail-level reports:
  - `workflow_id`
  - `rule_pack_id`
  - `rule_pack_version`
  - `applied_rule_ids` (ordered list, including shared-core and workflow-specific rule IDs)
- Dynamic loading should be constrained to known module paths in-repo; no ad hoc external module discovery.

### Rule outcome classes
- **Hard block**: no write; discrepancy report required.
- **Soft warning**: permitted in phase 1 as a non-blocking decision class for fully specified, low-risk anomalies; processing may continue and reports must retain warning lineage.
- **Human review**: deferred capability for a later phase after common issue categories are understood and explicitly documented.
- **Phase-1 precedence**: unspecified, ambiguous, or incompletely satisfied rule conditions must resolve to `hard_block` (not human review) with comprehensive discrepancy reporting.

### Warning behavior policy (phase 1)
- Warnings are allowed only when all mandatory validation parameters are satisfied and no hard-block condition is present.
- A warning-only mail may still proceed through staged downstream actions: workbook write (if write-capable workflow), print, and post-run mail move.
- Warning decisions must be preserved in mail-level and run-level reports with rule IDs and rationale.
- If both `warning` and `hard_block` decisions are emitted for the same mail, **`hard_block` takes precedence** and write/print/mail-move are disallowed for that mail.

### Examples of warning-only cases (phase 1)
- Attachment filename contains a non-critical cosmetic variation (for example extra separator characters) but document classification and required extracted fields validate successfully.
- Parsed buyer display text differs only by case/punctuation from ERP-normalized buyer while normalized canonical values match.
- A duplicate informational attachment (not selected for extraction/write) is present in the email, while at least one required document is valid and all write-gating checks pass.

### Examples of hard blocks
- any extracted file number missing its ERP row
- ERP family inconsistency across extracted file numbers
- missing required extraction fields
- contradictory matching results
- workbook row eligibility not satisfied
- duplicate-save or duplicate-write invariants violated

### Initial live-deployment decision policy
During early live deployment, the system should treat any failure to satisfy specified parameters as a hard block with a comprehensive discrepancy report. Warning decisions are still permitted only for explicitly encoded non-blocking rule outcomes that do not violate required parameters. Human-review checkpoints remain a future extension once recurring issue categories have been observed and codified.

## 7. Excel integration design
- Use one master workbook per year.
- At the beginning of any write-capable tool run, create a master-workbook backup artifact before progressing past run initialization.
- Assume exclusive access during writes.
- Read headers from row 2 of sheet 1.
- Treat duplicate header labels as a supported workbook shape only when the duplicate is explicitly declared in the mapping contract with fixed column indexes and workflow ownership.
- In the current master workbook schema, header text `Amount` appears twice and must be disambiguated by column index:
  - column 6 `Amount` is the **Export LC/SC amount** target
  - column 22 `Amount` is the **Import LC (Back-to-Back) amount** target
- Any attempt to resolve `Amount` by header text alone is invalid; writes must bind to canonical column keys first, then enforce the declared column index.
- Never write unless all validations pass.
- Execute the batch workbook-write phase as all-or-nothing for the runâ€™s approved write set.
- Restrict writes to previously validated blank target cells (or validated append targets that are blank by construction).
- For export LC/SC, append new rows only after skip-if-file-number-exists and same-file/amendment checks.
- Preserve formulas, styles, merged cells, conditional formatting, filters, comments, validations, and protection exactly.
- Apply selective number-format override only for `Quantity of Fabrics (Yds/Mtr)` when the ERP unit is `MTR`, using `#,###.00 "Mtr"`.
- Capture before/after row references and batched write operations in reports as compensating controls.
- If a crash/interruption or partial failure occurs after validation but before or during the batch workbook-write phase, mark run state as uncertain/incomplete and persist that state in run metadata.
- While run state is uncertain/incomplete, block batch printing and block post-run mail moves.
- Reruns must start with a recovery check that compares workbook state to the backup and the recorded staged write plan; only after explicit recovery resolution may a new write attempt begin.

## 8. Document extraction strategy
Use a layered extraction pipeline:
1. detect whether PDF is text, scanned, or hybrid
2. extract embedded text first with PyMuPDF/pdfplumber
3. extract tables where required with img2table or equivalent
4. use OCR fallback for scanned or low-yield pages
5. isolate clauses needed for LC/amendment and related-LC detection
6. keep provenance per field: source document, page, extraction method, confidence

This allows deterministic review of why a value was accepted or blocked.

## 9. Storage, audit, and reporting
- Local filesystem remains the primary store in phase 1.
- Duplicate PDF detection is by filename only.
- Export files follow the hierarchy `Year / Buyer / LC-or-SC / All Attachments`.
- Import files live under the designated import root organized by year.
- JSON reports must include run id, per-mail job identifiers, workflow name, source-email snapshot, parsing outputs, extracted file numbers, saved paths, normalized entities, validation results, staged row targets, final write/blocked status, destination Outlook folder decisions, print metadata, timestamps, and operator context.
- Run-level JSON metadata must persist deterministic `mail_iteration_order` (timezone-normalized `ReceivedTime` + `EntryID`) and final `print_group_order` (mail-group ids ranked by workbook row sequence).
- Run-level and mail-level reports must include revision stamps for every active deterministic list/rule set used during evaluation (for example `import_subject_keyword_list_version`).

## Configuration layer policy (phase 1)

### Deterministic list location
- Workflow keyword/rule lists that directly influence write/no-write decisions (including import relevance keywords) must live in **in-repo Python constants**, not operator-editable external config files, in phase 1.
- Rationale: keeps behavior deterministic, code-reviewed, and tied to explicit release artifacts.
- Import relevance keyword constants must use the canonical module path `project/workflows/import_btb_lc/keywords.py` (import path `project.workflows.import_btb_lc.keywords`) so startup validation and lineage stamping are deterministic across environments.
- The module must export both required constants:
  - `IMPORT_SUBJECT_KEYWORDS`
  - `IMPORT_KEYWORD_REVISION`

### Ownership and update workflow
- **Owner**: automation engineering maintains list definitions in code.
- **Approver**: at least one business-domain reviewer (customs/commercial process owner or delegate) must approve list-change pull requests before merge.
- **Review minimum**: one technical reviewer + one business reviewer.
- **Release boundary**: list changes become effective only after merge and tagged deployment/package release; no ad hoc runtime edits on operator machines.

### Version stamping requirements
- Each deterministic list/rule set must expose a stable revision identifier (for example semantic version or date+sequence token).
- For import relevance keywords, revision format is required: `YYYY-MM-DD.N` (`N` is a positive integer sequence for that date).
- The orchestrator must capture the active revision identifier in run metadata and discrepancy/report outputs.
- For import workflows, the orchestrator must stamp `IMPORT_KEYWORD_REVISION` into both:
  - run-level report/metadata payloads
  - mail-level report/discrepancy payloads for every processed mail
- Field name must be stable as `import_keyword_revision` so report consumers can join run/mail lineage without per-tool remapping.
- Report consumers must be able to reconstruct which exact list revision produced each relevance or validation decision.

### Missing/malformed configuration behavior
- If required deterministic list constants cannot be loaded, are empty when marked mandatory, or fail schema/shape validation at startup, the CLI must terminate with a **startup hard failure** before snapshot side effects.
- Import keyword startup validation must explicitly fail fast when `IMPORT_SUBJECT_KEYWORDS` or `IMPORT_KEYWORD_REVISION` is missing, malformed, mandatory-empty, or if `IMPORT_KEYWORD_REVISION` does not match `YYYY-MM-DD.N`.
- Phase 1 must not silently fall back to permissive defaults for missing/malformed decision-driving lists.

## 10. Windows deployment and operations
- Package and manage the environment with `uv`.
- Keep Outlook, Excel, Acrobat, Playwright, OCR tools, and Python runtime as documented desktop prerequisites.
- Use local secrets storage appropriate for Windows operator machines.
- Standardize report/log locations so operators can retrieve discrepancy reports without a dashboard.
- Publish workflow-specific runbooks for command usage, recovery, and reruns.

## 11. Risks and mitigation themes
- **Excel corruption risk** â†’ constrain writes to surgical adapter operations and require pre-write validation.
- **Unreliable document extraction** â†’ layered extraction, provenance, and human-review thresholds.
- **Duplicate processing on rerun** â†’ run snapshots, job ids, filename dedupe, workbook existence checks, staged side effects, and print-state tracking.
- **Rule ambiguity** â†’ explicit open questions and review checkpoints instead of silent inference.
- **Desktop dependency fragility** â†’ adapter abstraction and environment readiness checklist.

## 12. Open questions needing business clarification
- Which future business-approved exceptions need to be added to workflow-specific rule-pack modules once they are identified in production?

## 13. Confirmed phase 1 decisions
- Buyer-type inference for UD/IP/EXP is intentionally out of scope for phase 1 and must not be used as a dependency in deterministic workflow logic.
- Initial live deployment should default to hard block plus comprehensive reporting for any case that does not satisfy all specified parameters.
- Outlook-driven workflows snapshot all messages currently in the `working` folder when the operator triggers the CLI.
- Export-family verification should validate every extracted file number against ERP data rather than selecting a single primary file number. Family consistency is defined by LC/SC number, buyer, and LC/SC date; duplicate ERP rows may use any one duplicate row, and any partial family match is a hard block.
- Import relevance uses case-insensitive substring matching on fabric subject keywords stored in code.
- Successfully processed export-team emails move to `UD and LC`; successfully processed import-team emails move to `Import`; blocked emails remain in `working`, and mail moves occur only after batched writes and printing finish.
- Print grouping is based on the active run snapshot and staged successful write outcomes, but only newly saved PDF documents are included in each batch.

## 14. Recommended documentation set
The architecture should continue to be split across:
- `docs/architecture.md`
- `docs/workflows.md`
- `docs/domain-rules.md`
- `PLANS.md`
- later implementation specs/runbooks under `docs/` as the codebase grows

## 15. Canonical entity schemas and stable identifiers (normative)
This section defines minimum schema requirements for phase 1 records so independently implemented modules remain interoperable.

### `ProcessingJob` (run-level)
Required fields:
- `run_id` (string): stable run identifier, format `run-<UTC ISO basic timestamp>-<workflow_id>-<8hex>`.
- `workflow_id` (string): one of `export_lc_sc`, `ud_ip_exp`, `import_btb_lc`, `bb_dashboard_verification`.
- `started_at_utc` (string): ISO-8601 UTC timestamp.
- `operator_id` (string): immutable operator identity captured at run start.
- `mail_iteration_order` (array): ordered list of `mail_id`.
- `hash_algorithm` (string): must be `sha256`.
- `run_start_backup_hash` (string): lowercase hex SHA-256 digest.
- `staged_write_plan_hash` (string|null): lowercase hex SHA-256 digest when write-capable workflow stages writes.
- `write_phase_status` (string): workflow write phase checkpoint.
- `print_phase_status` (string): workflow print checkpoint.
- `mail_move_phase_status` (string): workflow mail-move checkpoint.

### `EmailMessage` (mail-level)
Required fields:
- `mail_id` (string): stable mail identifier derived from Outlook `EntryID`.
- `entry_id` (string): raw Outlook `EntryID`.
- `received_time_utc` (string): normalized ISO-8601 UTC timestamp.
- `received_time_workflow_tz` (string): timestamp in configured workflow timezone.
- `subject_raw` (string): original subject.
- `sender_address` (string): canonical sender SMTP address when available.
- `snapshot_index` (integer): position from deterministic run snapshot ordering.

### `SavedDocument`
Required fields:
- `saved_document_id` (string): stable id `sha256(mail_id + "|" + normalized_filename + "|" + destination_path)`.
- `mail_id` (string): parent mail id.
- `attachment_name` (string): original attachment filename.
- `normalized_filename` (string): normalized filename for dedupe comparisons.
- `destination_path` (string): full output path.
- `file_sha256` (string): lowercase hex SHA-256 of saved bytes.
- `save_decision` (string): `saved_new` or `skipped_duplicate_filename`.

### `WriteOperation`
Required fields:
- `write_operation_id` (string): stable id `sha256(run_id + "|" + mail_id + "|" + operation_index_within_mail + "|" + sheet_name + "|" + row_index + "|" + column_key)`.
- `run_id`, `mail_id` (string): lineage keys.
- `operation_index_within_mail` (integer): 0-based deterministic operation index.
- `sheet_name` (string), `row_index` (integer), `column_key` (string): cell target.
- `expected_pre_write_value` (string|number|null): precondition.
- `expected_post_write_value` (string|number|null): intended write value.
- `row_eligibility_checks` (array): explicit predicates used during prevalidation.

### `PrintBatch`
Required fields:
- `print_group_id` (string): stable id `sha256(run_id + "|" + mail_id + "|" + print_group_index)`.
- `run_id`, `mail_id` (string): lineage keys.
- `print_group_index` (integer): deterministic rank in `print_group_order`.
- `document_path_hashes` (array): SHA-256 hashes for print payload documents in group order.
- `completion_marker_id` (string): `sha256(run_id + "|" + mail_id + "|" + print_group_index + "|" + joined_document_hashes)`.

### `MailMoveOperation`
Required fields:
- `mail_move_operation_id` (string): `sha256(run_id + "|" + entry_id + "|" + destination_folder)`.
- `run_id`, `mail_id`, `entry_id` (string): lineage keys.
- `source_folder`, `destination_folder` (string): expected move path.
- `moved_at_utc` (string|null): completion evidence timestamp.
- `move_status` (string): `pending`, `moved`, or `inconsistent`.

## 16. Report schema/versioning contract (normative)
All JSON report payloads must include `report_schema_version` using semantic versioning:
- Patch: backward-compatible additive fields.
- Minor: backward-compatible structural additions.
- Major: breaking changes requiring consumer upgrade.

### Required top-level run report object
```json
{
  "report_schema_version": "1.0.0",
  "run_id": "run-20260324T093000Z-export_lc_sc-a1b2c3d4",
  "workflow_id": "export_lc_sc",
  "rule_pack_id": "export_lc_sc.default",
  "rule_pack_version": "1.4.0",
  "started_at_utc": "2026-03-24T09:30:00Z",
  "mail_iteration_order": ["mail-01", "mail-02"],
  "print_group_order": ["grp-mail-02", "grp-mail-01"],
  "write_phase_status": "committed",
  "print_phase_status": "completed",
  "mail_move_phase_status": "completed",
  "hash_algorithm": "sha256",
  "run_start_backup_hash": "9e1f...",
  "staged_write_plan_hash": "3ac8..."
}
```

### Required top-level mail report object
```json
{
  "report_schema_version": "1.0.0",
  "run_id": "run-20260324T093000Z-export_lc_sc-a1b2c3d4",
  "mail_id": "mail-01",
  "workflow_id": "export_lc_sc",
  "rule_pack_id": "export_lc_sc.default",
  "rule_pack_version": "1.4.0",
  "applied_rule_ids": ["core.subject.buyer_lc_match.v1"],
  "final_decision": "warning",
  "discrepancies": [],
  "saved_documents": [],
  "staged_write_operations": [],
  "print_group_id": "grp-mail-01",
  "mail_move_operation_id": "move-01"
}
```

Canonical serialization for any hash/signature-relevant report variant must use UTF-8, LF line endings, deterministic key order (lexicographic), and deterministic array ordering where identities are ordered.
Implementation should keep schema definitions in `project/reporting/schemas/` so report writers and validators share one source of truth.

## 8. Phase 1 configuration and secrets contract (normative)
To keep CLI behavior deterministic across operator machines, all workflows must use one explicit configuration and secrets contract.

### Configuration sources and precedence
Configuration values are resolved in this order (highest to lowest):
1. CLI arguments
2. Environment variables
3. Local configuration file
4. In-code defaults (allowed only for non-sensitive optional settings)

If a required value is missing after resolution, startup must hard-fail before run snapshot and before any side effects.

### Required shared configuration keys
At minimum, the configuration layer must expose these keys:
- `workflow_id`
- `state_timezone` (IANA name; phase-1 deployment basis is `Asia/Dhaka`)
- `report_root`
- `run_artifact_root`
- `backup_root`
- `outlook_profile`
- `master_workbook_root`
- `erp_base_url`
- `playwright_browser_channel` (if applicable)

Write-capable workflows must also provide:
- `master_workbook_path_template`
- `excel_lock_timeout_seconds`
- `print_enabled`

`master_workbook_path_template` controls the expected yearly workbook filename.
The normal production pattern is to store the exact real workbook filename in config and update it manually when the yearly workbook changes.

Optional placeholders may be used if a deployment intentionally wants generated naming:
- `{year}`
- `{workflow_id}`

### Workflow-specific required keys
Workflow modules must declare their own required key list (for example import keyword controls, destination folder mapping, or worksheet mapping), and startup validation must fail if any required key is absent or malformed.

### Secrets handling (Windows-first)
- Credentials must not be hard-coded in source files.
- Credentials must not be committed in local config files tracked by git.
- Preferred phase-1 secret source is environment variables or OS-protected credential storage configured by deployment scripts.
- Report payloads and logs must never include raw credential values.

### Startup validation contract
Before processing a run snapshot, startup must validate:
- presence/type/shape of all required configuration values
- path existence/permissions for configured roots
- timezone parseability and canonicalization
- destination Outlook folder mapping completeness for the active workflow

Any failure is a startup hard failure with structured diagnostics.

### Example local config file (illustrative)
```toml
workflow_id = "export_lc_sc"
state_timezone = "Asia/Dhaka"
report_root = "C:/customs-automation/reports"
run_artifact_root = "C:/customs-automation/state/runs"
backup_root = "C:/customs-automation/state/backups"
outlook_profile = "outlook"
master_workbook_root = "C:/customs-automation/workbooks"
erp_base_url = "https://erp.example.local"
playwright_browser_channel = "msedge"
print_enabled = true
excel_lock_timeout_seconds = 120
```

## 9. Artifact storage layout reference
Run/recovery artifact locations, file naming, atomic persistence rules, and retention behavior are defined in `docs/storage-layout.md`. Implementations must treat that document as normative for persisted run state and recovery marker management.

