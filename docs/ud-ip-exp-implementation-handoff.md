# UD/IP/EXP Implementation Handoff

This document is retained as historical implementation handoff and status context for `ud_ip_exp`.
Current normative behavior lives in `docs/architecture.md`, `docs/workflows.md`, `docs/domain-rules.md`, and `docs/discrepancy-codes.md`.

Before implementation, read these files in order:
1. `AGENTS.md`
2. `docs/architecture.md`
3. `docs/workflows.md`
4. `docs/domain-rules.md`
5. `docs/discrepancy-codes.md`
6. `PLANS.md`
7. `README.md`

## Current Status

- Workflow id: `ud_ip_exp`
- Registry entry: present in `project/workflows/registry.py`
- Rule pack: implemented at `project/rules/workflows/ud_ip_exp/__init__.py`
- Rule pack id: `ud_ip_exp.default`
- Rule pack version: `1.2.0`
- Implementation status:
  - payload models, parsing helpers, workbook header mapping, deterministic UD allocation, staging, reporting, and rule-pack wiring are implemented
  - CLI validation accepts deterministic fixture payloads through `validate-run ud_ip_exp --ud-payload-json <path>`
  - live UD document preparation is implemented for `validate-run` when `--document-root` is used, including PDF save, saved-document analysis, workflow-local document classification, workbook-family storage-path resolution, and UD payload derivation from saved documents
  - live `ud_ip_exp` family resolution now follows the released `export_lc_sc` model: extract canonical file numbers from the email body, resolve them through ERP rows, and require one LC/SC family before storage, validation, printing, or mail movement can proceed
  - email subject extraction is not authoritative for `ud_ip_exp`; it must not drive family resolution, storage, validation, printing, or mail movement
  - live `ud_ip_exp` document reading is filename-gated: only PDFs beginning `UD-`, beginning `IP-`, or whose filename stem is exactly one or more digits followed by `-EXP` are processed; other PDFs are skipped before OCR/document analysis
  - EXP descriptor variants such as `123-EXP-INVOICE.pdf` are skipped because the strict `123-EXP.pdf` form identifies the machine-generated text-layer file preferred for extraction accuracy
  - explicit `UD-LC-<suffix>` or `UD-SC-<suffix>` filename evidence is implemented as a sanity guard against the ERP LC/SC selected from email-body file numbers; mismatches hard-block with discrepancy code `ud_filename_lc_suffix_mismatch`, and filename suffixes are never used for lookup or row selection
  - live UD document preparation now hard-blocks with attachment-level evidence when multiple live-derived documents disagree on resolved LC/SC family, or when multiple live-derived UD documents disagree on required UD evidence such as document date or quantity
  - low-confidence OCR-derived UD/IP/EXP document numbers are not promoted into deterministic UD/IP/EXP document classification; document-number confidence thresholds remain the documented UD/IP/EXP OCR gates
  - when multiple same-family UD payloads exist, the workflow validates and allocates them independently in deterministic document-date/document-number/attachment-order sequence while excluding rows already claimed earlier in the same mail
  - structured UD Amendment extraction implements the confirmed zero-`Increased/Decreased` rule: when the matched amendment row's increased/decreased value is numeric zero, the workflow uses that row's `Value` column because the LC is treated as newly included in the amendment
  - structured UD quantity validation derives each selected workbook row unit from the quantity cell number format, and in-memory workbook snapshot advancement preserves row number formats between staged mails so `MTR`/`YDS` evidence does not drift during a multi-mail batch
  - transport for `ud_ip_exp` is enabled using the same staged model as `export_lc_sc`: newly saved PDFs from successful mails are print-eligible after workbook write commit, and successful mails move only after required upstream gates complete
  - transport integration is covered by orchestration and CLI-artifact tests proving live UD validation saves into the ERP-derived export-family folder, print planning includes newly saved UD PDFs after the committed write gate, mail moves hard-block before print completion, and mail moves complete after print completion
  - live UD-only end-to-end proof is complete for a newly saved structured UD Amendment PDF: run `run-20260423T080206Z-ud_ip_exp-610790f2` committed row 372, submitted one print group, and completed the post-print mail move with zero discrepancies
  - final clean two-mail live proof after the structured amendment and batch-number-format fixes is complete: run `run-20260423T094320Z-ud_ip_exp-9474d4c6` produced `pass = 2`, `hard_block = 0`, six committed workbook writes, two moved mails, and zero discrepancies
  - a one-click Windows launcher is available at `scripts/run_ud_ip_exp_live_cycle.cmd`; it reuses the shared `scripts/run_live_cycle.ps1` staged sequence
  - conservative phase-1 IP/EXP processing is implemented for valid `EXP-only` and `EXP+IP` mails, with family-wide blank-only staging and duplicate-only/no-write handling for exact already-recorded values
- Phase: `PLANS.md` Phase 3.

## Objective

Implement and maintain the UD/IP/EXP workflow for updating the yearly master workbook after export LC/SC rows already exist.

The workflow must:
- Snapshot all mails from the configured Outlook working folder.
- Save and classify relevant PDF attachments for UD, IP, and EXP.
- Extract required values from PDFs.
- Match extracted LC/SC family and quantities to existing workbook rows.
- Write values into shared workbook columns safely.
- Preserve workbook fidelity and use the same staged write, prevalidation, live write, print, mail-move, recovery, and reporting contracts used by the released workflow.

Current implementation note:
- The repository already satisfies the initial pure-function foundation goals plus a first live-UD intake slice.
- Remaining work is no longer "start from zero"; it is to extend the current implementation without breaking export behavior.

## Non-Negotiable Shared Contracts

Reuse the existing shared architecture:
- `initialize_workflow_run` for run bootstrap and workbook backup.
- `validate_run_snapshot` as the orchestration entry point, extended cleanly for `ud_ip_exp`.
- Rule-pack loading through the existing registry and `RULE_DEFINITIONS`.
- `prepare_live_write_batch` and `execute_live_write_batch` for workbook prevalidation/write execution.
- Print planning/execution only after eligible writes are committed.
- Mail moves only after required upstream phases are terminal-success for that mail.
- `explain-run-failure` must work for stopped `ud_ip_exp` runs by relying on the same persisted artifact shapes.

Do not special-case around the staged write safety model. If a target cell is not in the expected pre-write state, hard-block before writing.

## Confirmed Business Rules

### Shared Column

Workbook column `UD No. & IP No.` stores UD, EXP, and IP values together:
- UD values have no prefix.
- EXP values use prefix `EXP: `.
- IP values use prefix `IP: `.
- If both EXP and IP exist, EXP appears before IP.
- Multiple values are separated by line breaks.

Mail composition contract:
- a mail may contain only UD documents, only EXP documents, or EXP together with IP
- if IP exists in a mail, EXP must also be present
- a mail mixing any UD document with any IP/EXP document is invalid

### UD Allocation

For UD documents:
- Extract UD number, UD date, LC/SC number, quantity data, and any additional fields required by the workbook mapping.
- Write-capable UD payloads now require value-first evidence: match ERP family, validate LC date, identify exact workbook `Amount` groups that match the extracted LC value within tolerance, and only then compare workbook quantities.
- Before staging a structured UD write, check for an already-recorded workbook group carrying the same UD value plus matching `UD & IP Date`; if found and quantity/value checks also pass, return `already_recorded` and stage no write.
- Write UD values only to selected matched rows, and only when every target cell required for the write is blank.
- Ignore excess quantity only when excess is at least `50` yards/meters.
- If excess quantity is less than `50`, hard-block.
- If required extraction fields are missing or below threshold, hard-block.

### UD Candidate Tie-Breaking

When multiple exact workbook value groups satisfy UD row identification, apply this exact deterministic order:

1. Row-index key:
   Compare sorted row-index sequences lexicographically; smallest sequence wins.
2. Amendment recency key:
   Compare normalized `L/C Amnd Date` ascending, blank oldest, then `L/C Amnd No.` ascending, blank as `0`.
3. Blank-field priority key:
   Prefer the higher count of selected rows where UD target cells are blank before write. If still tied, prefer fewer non-target populated optional cells.
4. Stable candidate-id key:
   Candidate id is sorted row indexes joined with `-`; lexicographically smallest wins.

If candidates remain tied after all keys, hard-block with discrepancy code `ud_candidate_tie_after_full_tiebreak`.

### Required UD Selection Reporting

Mail-level reports for UD allocation must include:
- `ud_selection.required_quantity`
- `ud_selection.quantity_unit`
- `ud_selection.candidate_count`
- `ud_selection.reported_candidate_count`
- `ud_selection.candidates_truncated`
- `ud_selection.omitted_candidate_count`
- `ud_selection.candidates[]`
- candidate row indexes
- matched quantities
- score keys
- selected status
- rejection reason for non-selected candidates
- final decision and reason

This evidence is mandatory because value-based selection may still produce multiple exact workbook groups and the final choice must remain auditable.
When dense structured value matches produce very large exact candidate sets, the persisted `ud_selection.candidates[]` payload may be a bounded deterministic subset instead of the full exact universe, but it must still include the selected candidate and the report must preserve the true total through `candidate_count`.

### IP / EXP Rules

Current docs confirm:
- IP and EXP do not use the UD amendment model.
- EXP/IP values share `UD No. & IP No.` with UD values.
- EXP must be ordered before IP when both are present.
- phase-1 IP/EXP staging is family-wide rather than quantity/value-subset-based
- phase 1 allows at most one deterministic EXP payload and at most one deterministic IP payload in a mail
- EXP-only and EXP+IP mails write the same formatted shared-column value to every workbook row in the verified ERP LC/SC family
- all IP/EXP payloads in the same mail must normalize to one document date because the workbook exposes one shared `UD & IP Date` value per target row
- quantity and value fields from IP/EXP payloads remain report evidence only in phase 1 and do not drive target-row selection
- exact already-recorded family-wide matches no-op; any different non-blank shared/date target value hard-blocks because phase 1 does not append, merge, or replace existing values

## Confirmed Exclusions

- Buyer-type inference for UD/IP/EXP is excluded from phase 1.
- Human-review routing is excluded from the initial live deployment path.
- Any unspecified, ambiguous, or partially specified case must hard-block with comprehensive reporting.
- Do not introduce AI-dependent production behavior.

## Expected Implementation Slices

### 1. Payload and Extraction Models

Implemented modules:
- `payloads.py`
- `parsing.py`
- `matching.py`
- `staging.py`
- `reporting.py`
- `providers.py`
- `document_classification.py`
- `live_documents.py`

Current payload coverage:
- parsed/extracted document type: UD, IP, EXP
- extracted LC/SC number
- extracted quantities and units
- extracted UD/IP/EXP identifiers and dates
- selected workbook candidate rows
- extraction provenance/confidence, where available
- saved document metadata

Current live-extraction boundary:
- saved-document analysis now carries UD/IP/EXP-oriented fields including document number, document date, quantity, quantity unit, and provenance
- live extraction remains heuristic and deterministic; unsupported or incomplete extraction still resolves to hard-block through the rule/staging path
- email body file numbers plus ERP rows are the primary LC/SC-family source for `ud_ip_exp`; PDF-derived LC/SC evidence is supporting validation evidence and must not override the ERP family
- explicit UD-LC/UD-SC attachment filename suffixes are supporting validation evidence only and must agree with the ERP LC/SC suffix when present
- ERP LC/SC family context and ERP `Ship. Remarks` are the primary linkage inputs for structured Base UD and UD Amendment PDF property extraction
- structured Base UD PDFs are identified by `UD Authenticating Authority`; structured UD Amendment PDFs are identified by `Amendment Authenticating Authority`
- structured UD/AM extraction now requires page-1 office-use-only-row UD/AM number/date extraction: Base UD must match `UD No (For office use only)` and Amendment must match `Amendment no. (For office use only)`; `For office use only` is mandatory, no alternate row-label fallback is allowed, and invalid BGMEA row values hard-block
- structured UD/AM extraction also requires exact ERP `Ship. Remarks` or ERP `LC No.` row matching in the UD LC table, ERP LC date validation, exact workbook `Amount`-group matching by LC value, and supplier quantity validation for Pioneer Denim rows
- ERP `LC No.` row matching is exact first, then may compare only after removing leading zeros from the left side of the ERP/table LC strings; leading/trailing spaces around compared values may be trimmed, internal spaces and all other characters remain unchanged, and `Ship. Remarks` remains exact-only
- for UD Amendments only, if the matched row's `Increased/Decreased` value is numeric zero, extraction uses that row's `Value` column instead because the LC is treated as newly included in the amendment
- structured UD/AM quantity validation uses the workbook `Quantity of Fabrics (Yds/Mtr)` cell number format as the unit source: `#,###.00 "Mtr"` means `MTR`; any other number format defaults to `YDS`
- batch validation must preserve row number formats when advancing the in-memory workbook snapshot after staged writes, because later mails in the same run still need the original quantity-unit evidence
- selected structured UD/AM rows write three fields when blank: `UD No. & IP No.`, `UD & IP Date`, and `UD Recv. Date`; date writes use `DD/MM/YYYY`
- document-date extraction prefers UD/IP/EXP-specific date labels over LC/SC issue-date labels so export-family dates are not accepted as UD/IP/EXP evidence
- text-layer UD/IP/EXP document-number extraction uses label-aware boundaries so buyer labels and neighboring LC/SC, date, and quantity labels are not captured into the document number
- storage-path resolution now reports attachment-level evidence whenever live-derived documents fail one-family resolution
- same-mail live-derived UD documents now hard-block with discrepancy code `ud_live_document_conflict` when required UD evidence disagrees across attachments
- mixed-quality same-family UD documents still hard-block when any UD payload is missing required fields; later valid UD payloads may still appear in deterministic selection reporting, but the mail remains blocked and stages no writes

### 2. Workbook Header Mapping

Implemented:
- `UD No. & IP No.`
- `L/C & S/C No.`
- `Quantity of Fabrics (Yds/Mtr)`
- `L/C Amnd No.`
- `L/C Amnd Date`

Additional live-document storage-path mapping is also implemented to resolve:
- `Name of Buyers`
- `LC Issue Date`

Do not assume additional workbook columns without verifying `docs/workflows.md`, `docs/domain-rules.md`, the real workbook headers, and user confirmation.

### 3. Candidate Matching

Implemented:
- Match rows by LC/SC family.
- For UD, identify candidate row groups only from exact workbook `Amount` matches to the extracted LC value, then validate workbook quantity totals by unit.
- Persist/report all candidate evidence.

### 4. Write Staging

Implemented behavior:
- No direct Excel writes during validation.
- All target cells must be prevalidated before live write.
- UD shared-column writes stage explicit `WriteOperation` records using expected post-write values.
- Exact already-recorded UD matches no-op instead of staging appends or replacements.
- Conflicting non-blank target values currently hard-block with `ud_shared_column_nonblank_policy_unresolved`.

Still deferred:
- append/update behavior for non-blank shared-column cells beyond the current hard-block path
- any phase-2+ append/merge policy for non-blank family-wide IP/EXP rows

### 5. Rule Pack

Implemented rule categories:
- file number present for ERP family resolution
- ERP rows present
- ERP family consistent
- required processable document present
- required UD extraction fields present
- deterministic UD allocation selected
- valid phase-1 IP/EXP mail shape
- required IP/EXP fields present and valid

Still expected before workflow completion:
- required document classification present
- richer live IP/EXP extraction fixtures and proofs

Any new discrepancy code must first be added to `docs/discrepancy-codes.md`.

### 6. Validation Orchestration

Implemented:
- build its workflow payload
- save/classify live documents for UD/IP/EXP intake when `--document-root` is used
- choose the most complete deterministic UD payload for allocation/reporting context when multiple UD payloads are present
- run rules
- stage workbook operations
- emit mail outcomes and reports

Keep export behavior unchanged. Add tests proving `export_lc_sc` still passes.

### 7. Print and Mail Move

Current implementation:
- `ud_ip_exp` print and mail-move eligibility follows `export_lc_sc`
- all newly saved PDFs for successful `ud_ip_exp` mails are print-eligible after the workbook write commit
- successful `ud_ip_exp` mails follow the same staged post-write/post-print movement gate as `export_lc_sc`
- IP/EXP document processing can still hard-block before transport when mail shape, required fields, family row resolution, or non-blank target conflicts violate the conservative phase-1 contract

## Deterministic Fixture Manifest

The initial implementation includes a read-only fixture path for deterministic UD validation via `validate-run ud_ip_exp --ud-payload-json <path>`.

This manifest is only for tests, fixture-backed dry runs, and development while live UD/IP/EXP PDF identification and extraction rules remain open. It must not be treated as a replacement for the unresolved live extraction rules.

Manifest shape:
- Top-level JSON value is a list of records.
- Each record must include `mail_id` or `entry_id` matching the mail snapshot.
- Each record may include `document_kind` (`UD`, `IP`, or `EXP`); omitted kind defaults to `UD` for backward-compatible UD fixtures.
- Multiple records may reference the same mail when a fixture mail carries multiple UD documents, or EXP together with IP documents.
- A fixture mail must not mix any UD record with any IP/EXP record.
- A fixture mail containing any IP record must also include at least one EXP record.
- Each record must include `document_number`, `document_date`, and `lc_sc_number`.
- `quantity` and `quantity_unit` should be supplied for UD allocation.
- Optional confidence/provenance fields are supported for required extracted values.

See `docs/ud-ip-exp-payload-manifest-example.json` for a minimal example.

## Tests to Add

Implemented test groups:
- `tests/test_ud_ip_exp_payloads.py`
- `tests/test_ud_ip_exp_parsing.py`
- `tests/test_ud_ip_exp_matching.py`
- `tests/test_ud_ip_exp_staging.py`
- `tests/test_ud_ip_exp_rules.py`
- `tests/test_ud_ip_exp_validation.py`
- `tests/test_ud_ip_exp_reporting.py`
- `tests/test_ud_ip_exp_manifest_validation.py`
- `tests/test_ud_ip_exp_workbook.py`
- `tests/test_ud_ip_exp_live_documents.py`
- CLI integration tests in `tests/test_cli.py`
- regression tests proving `export_lc_sc` behavior unchanged

Implemented scenarios include:
- UD exact quantity maps to one row.
- UD exact quantity maps to multiple non-sequential rows.
- Duplicate row quantities use multiset matching correctly.
- Tie-break selects the lowest row-index sequence.
- Tie after all keys hard-blocks with `ud_candidate_tie_after_full_tiebreak`.
- Excess quantity of at least `50` is ignored.
- Excess quantity below `50` hard-blocks.
- Existing `UD No. & IP No.` value is appended or blocked according to explicit rule.
- EXP and IP values are ordered as EXP then IP.
- Missing required extraction field hard-blocks.
- Workbook prevalidation blocks non-safe target cells.
- Live-derived documents that resolve to mixed LC/SC families hard-block with attachment-level evidence.
- Live-derived UD documents for the same family hard-block when extracted date or quantity disagrees across attachments.

Still valuable to add later:
- more live document-analysis fixtures covering mixed-quality PDFs
- explicit tests for additional live-document ambiguity patterns beyond family/date/quantity disagreement
- broader live IP/EXP completion-path tests beyond the conservative phase-1 family-wide write contract

## Open Questions Before Production Completion

These must be answered or intentionally hard-blocked before production release:

1. What exact PDF patterns identify UD, IP, and EXP documents?
2. Which extracted fields are mandatory for UD?
3. Which extracted fields are mandatory for IP?
4. Which extracted fields are mandatory for EXP?
5. Does the conservative family-wide IP/EXP write policy remain acceptable once live evidence accumulates?
6. Are any future non-blank append/merge rules needed for repeated EXP/IP updates on the same family?
7. What source/destination Outlook folders should production use for this workflow?

Until answered, default to hard-block with comprehensive discrepancy reporting rather than guessing.

## Suggested Next Implementation Step

The next highest-value work is whichever of these the team wants to unblock first:
1. Update this handoff and adjacent durable docs whenever the implementation boundary changes.
2. Continue improving live UD extraction quality with deterministic saved-document analysis fixtures and hard-block reporting inside the confirmed office-use-only-row plus ERP LC/SC + `Ship. Remarks` linkage rules.
3. Add broader live-proof coverage for the documented conservative phase-1 IP/EXP path, then revisit whether later business-approved append/merge behavior is needed.

## Guardrails for New Sessions

When starting a new session, use this prompt:

```text
Read AGENTS.md, docs/architecture.md, docs/workflows.md, docs/domain-rules.md, docs/discrepancy-codes.md, PLANS.md, README.md, and docs/ud-ip-exp-implementation-handoff.md. We are implementing workflow ud_ip_exp. Preserve existing export_lc_sc behavior and tests. Follow the documented conservative phase-1 IP/EXP contract exactly; hard-block anything outside it rather than inventing append/merge behavior.
```
