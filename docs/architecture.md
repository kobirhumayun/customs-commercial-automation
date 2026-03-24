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
   - `rptDateWiseLCRegister` retrieval
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
1. **Run-level snapshot + workbook backup** — capture the complete set of messages currently in `working` for that workflow, bind them to the run id, and create a backup copy of the target yearly master workbook before any write-capable phase can proceed.
2. **Deterministic mail ordering** — sort the snapshotted messages by `ReceivedTime` converted to the configured workflow state timezone (current deployment basis: Bangladesh Standard Time, UTC+06:00), then tie-break by ascending Outlook `EntryID`; persist this ordered list in run metadata before mail processing starts.
3. **Mail-level validation** — iterate the ordered snapshot, save only new PDFs, extract entities, validate against ERP and workbook context, and build proposed write/print/move outcomes per mail.
4. **Batch workbook write** — open the yearly master workbook in one controlled write session and apply only the approved write operations for mails that passed validation.
5. **Batch print planning + print** — derive mail-group print order from master-workbook row sequence (group key = originating mail, group rank = earliest row sequence written for that mail), persist final print-group order in run metadata, then print only newly saved PDFs for each group without extra intra-group sorting and insert exactly one blank page between consecutive groups.
6. **Post-run mail moves** — move only successful mails to their destination Outlook folders after workbook writes and printing complete; blocked mails remain in `working`.
7. **Rerun recovery gate** — if the prior run is marked uncertain/incomplete, perform recovery checks against the backup artifact and recorded staged write plan before any new write attempt is allowed, using the shared **Recovery Decision Matrix** contract defined in `docs/workflows.md` (required artifacts, exact outcomes, idempotency checks, and pseudocode flow).

This model is intentionally **run-level staged, but mail-level selective**: one blocked mail must not force unrelated validated mails in the same run to be discarded, yet no single mail may print or move ahead of the controlled workbook-write phase.

### Batch write contract (normative)
Batch atomicity applies only to mails with approved staged write operations, not to all mails in the run snapshot.
If one mail in the run snapshot is blocked while others are approved, the blocked mail contributes no workbook writes and each approved mail still participates in the same atomic commit of the approved staged write set.
Example run (3 mails): Mail A = blocked, Mail B = approved (2 staged writes), Mail C = approved (1 staged write) ⇒ batch write outcome: commit Mail B + Mail C writes together (3 total) in one atomic transaction; commit none if that transaction fails; Mail A writes remain zero.

## 4. Workflow architecture

### Export LC/SC intake CLI
- Operator moves eligible emails from `temp-export` to `working`.
- CLI snapshots all messages currently present in `working` and binds them to the active run before any side effects occur.
- Body parser extracts all file numbers matching `P/<yy>/<nnnn>`.
- Every extracted file number is used for ERP lookup, subject validation, and folder-path verification to confirm they all belong to the same LC/SC family.
- ERP downloader retrieves `rptDateWiseLCRegister`, normalizes row-2 headers, and validates family consistency using LC/SC number, normalized buyer, and LC/SC date. Duplicate ERP rows may use any one row when they are true duplicates. Any partial family match is a hard block.
- Subject validation compares normalized buyer name and LC/SC number against the verified family; any mismatch is a hard block.
- Attachment classifier identifies LC/SC and PI PDFs using naming conventions, clauses, amendment context, and ERP PI references.
- Storage manager saves only new PDFs into export folder hierarchy:
  `Year / Buyer Name / LC-or-SC Number / All Attachments`.
- Excel adapter appends or skips based on file number existence and amendment matching rules.
- Reporting engine emits structured results for each mail and the overall run.
- Validated export write operations are staged and applied during the batch workbook-write phase.
- Successfully processed export-team emails move from `working` to the Outlook folder `UD and LC` only during the post-run mail-move phase; blocked emails remain in `working`.

### UD / IP / EXP CLI
- Shares intake, storage, and parsing services with export workflow.
- Processes only the LC/SC family confirmed by validating all extracted email-body file numbers against ERP data.
- Saves only new PDFs and records all saved paths.
- Extraction pipeline captures document numbers, dates, LC/SC references, quantities, and units.
- Matching engine locates candidate workbook rows and applies UD combination logic or IP/EXP total matching rules.
- Shared workbook column `UD No. & IP No.` stores UD values directly and EXP/IP values with ordered prefixes.
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
- Prints only newly saved PDFs from successful mails in the active run snapshot.
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
- Rule-pack version resolution must be explicit and recorded in run metadata.
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
- subject validation mismatch against ERP buyer name and LC/SC number
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
- Never write unless all validations pass.
- Execute the batch workbook-write phase as all-or-nothing for the run’s approved write set.
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

### Ownership and update workflow
- **Owner**: automation engineering maintains list definitions in code.
- **Approver**: at least one business-domain reviewer (customs/commercial process owner or delegate) must approve list-change pull requests before merge.
- **Review minimum**: one technical reviewer + one business reviewer.
- **Release boundary**: list changes become effective only after merge and tagged deployment/package release; no ad hoc runtime edits on operator machines.

### Version stamping requirements
- Each deterministic list/rule set must expose a stable revision identifier (for example semantic version or date+sequence token).
- The orchestrator must capture the active revision identifier in run metadata and discrepancy/report outputs.
- Report consumers must be able to reconstruct which exact list revision produced each relevance or validation decision.

### Missing/malformed configuration behavior
- If required deterministic list constants cannot be loaded, are empty when marked mandatory, or fail schema/shape validation at startup, the CLI must terminate with a **startup hard failure** before snapshot side effects.
- Phase 1 must not silently fall back to permissive defaults for missing/malformed decision-driving lists.

## 10. Windows deployment and operations
- Package and manage the environment with `uv`.
- Keep Outlook, Excel, Acrobat, Playwright, OCR tools, and Python runtime as documented desktop prerequisites.
- Use local secrets storage appropriate for Windows operator machines.
- Standardize report/log locations so operators can retrieve discrepancy reports without a dashboard.
- Publish workflow-specific runbooks for command usage, recovery, and reruns.

## 11. Risks and mitigation themes
- **Excel corruption risk** → constrain writes to surgical adapter operations and require pre-write validation.
- **Unreliable document extraction** → layered extraction, provenance, and human-review thresholds.
- **Duplicate processing on rerun** → run snapshots, job ids, filename dedupe, workbook existence checks, staged side effects, and print-state tracking.
- **Rule ambiguity** → explicit open questions and review checkpoints instead of silent inference.
- **Desktop dependency fragility** → adapter abstraction and environment readiness checklist.

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
