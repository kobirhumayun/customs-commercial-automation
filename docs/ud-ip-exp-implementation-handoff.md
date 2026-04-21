# UD/IP/EXP Implementation Handoff

This handoff is the starting brief for implementing the `ud_ip_exp` workflow after the released `export_lc_sc` workflow. It is intended for a fresh Codex session or engineer who has no access to the prior conversation.

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
- Rule pack version: `1.0.0`
- Implementation status:
  - payload models, parsing helpers, workbook header mapping, deterministic UD allocation, staging, reporting, and rule-pack wiring are implemented
  - CLI validation accepts deterministic fixture payloads through `validate-run ud_ip_exp --ud-payload-json <path>`
  - live UD document preparation is implemented for `validate-run` when `--document-root` is used, including PDF save, saved-document analysis, workflow-local document classification, workbook-family storage-path resolution, and UD payload derivation from saved documents
  - live `ud_ip_exp` family resolution now follows the released `export_lc_sc` model: extract canonical file numbers from the email body, resolve them through ERP rows, and require one LC/SC family before storage, validation, printing, or mail movement can proceed
  - email subject extraction is not authoritative for `ud_ip_exp`; it must not drive family resolution, storage, validation, printing, or mail movement
  - live UD document preparation now hard-blocks with attachment-level evidence when multiple live-derived documents disagree on resolved LC/SC family, or when multiple live-derived UD documents disagree on required UD evidence such as document date or quantity
  - when multiple same-family UD payloads exist, deterministic reporting/allocation context selects the most complete UD payload based on required extraction-field completeness rather than attachment order, while the rule pack still hard-blocks if any UD payload is missing required fields
  - transport for `ud_ip_exp` is enabled using the same staged model as `export_lc_sc`: newly saved PDFs from successful mails are print-eligible after workbook write commit, and successful mails move only after required upstream gates complete
  - transport integration is covered by orchestration and CLI-artifact tests proving live UD validation saves into the ERP-derived export-family folder, print planning includes newly saved UD PDFs after the committed write gate, mail moves hard-block before print completion, and mail moves complete after print completion
  - IP/EXP processing remains intentionally hard-blocked where policy is still unresolved
- Phase: `PLANS.md` Phase 3.

## Objective

Implement the UD/IP/EXP workflow for updating the yearly master workbook after export LC/SC rows already exist.

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

### UD Allocation

For UD documents:
- Extract UD number, UD date, LC/SC number, quantity, quantity unit, and any additional fields required by the workbook mapping.
- Find candidate workbook rows for the same LC/SC family.
- Use a multiset/bag approach so duplicate row quantities are handled correctly.
- Candidate row combinations may be non-sequential.
- Write UD values only to selected matched rows.
- Ignore excess quantity only when excess is at least `50` yards/meters.
- If excess quantity is less than `50`, hard-block.
- If required extraction fields are missing or below threshold, hard-block.

### UD Candidate Tie-Breaking

When multiple valid row combinations satisfy the same extracted quantity, apply this exact deterministic order:

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
- `ud_selection.candidates[]`
- candidate row indexes
- matched quantities
- score keys
- selected status
- rejection reason for non-selected candidates
- final decision and reason

This evidence is mandatory because the workflow may select non-sequential rows and must remain auditable.

### IP / EXP Rules

Current docs confirm:
- IP and EXP do not use the UD amendment model.
- EXP/IP values share `UD No. & IP No.` with UD values.
- EXP must be ordered before IP when both are present.

The exact extraction fields, workbook date columns, matching keys, and update/append behavior for IP/EXP must be confirmed before coding if not already explicit in the source docs.

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
- ERP LC/SC family context and ERP `Ship. Remarks` are reserved as the primary future linkage inputs for UD PDF property extraction, while the detailed extraction rule remains deferred until the business rule is documented
- document-date extraction prefers UD/IP/EXP-specific date labels over LC/SC issue-date labels so export-family dates are not accepted as UD/IP/EXP evidence
- text-layer UD/IP/EXP document-number extraction uses label-aware boundaries so buyer labels and neighboring LC/SC, date, and quantity labels are not captured into the document number
- storage-path resolution now reports attachment-level evidence whenever live-derived documents fail one-family resolution
- same-mail live-derived UD documents now hard-block with discrepancy code `ud_live_document_conflict` when required UD evidence disagrees across attachments
- mixed-quality same-family UD documents still hard-block when any UD payload is missing required fields; selection/reporting uses the most complete UD payload only to keep allocation context deterministic and stable

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
- Compare quantities using normalized numeric values and unit compatibility.
- Generate all valid candidate combinations for UD allocation.
- Score candidates using the normative tie-break keys.
- Persist/report all candidate evidence.

### 4. Write Staging

Implemented behavior:
- No direct Excel writes during validation.
- All target cells must be prevalidated before live write.
- UD shared-column writes stage explicit `WriteOperation` records using expected post-write values.
- Conflicting non-blank target values currently hard-block with `ud_shared_column_nonblank_policy_unresolved`.

Still deferred:
- append/update behavior for non-blank shared-column cells beyond the current hard-block path
- IP/EXP shared-column staging beyond explicit unresolved-policy hard-block reporting

### 5. Rule Pack

Implemented rule categories:
- required UD document present
- required UD extraction fields present
- deterministic UD allocation selected
- unresolved IP/EXP policy hard-block when IP/EXP documents are present

Still expected before workflow completion:
- required document classification present
- LC/SC family match
- quantity/unit compatibility
- workbook target update permitted

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
- IP/EXP document processing can still hard-block before transport because IP/EXP matching and write policies remain unresolved

## Deterministic Fixture Manifest

The initial implementation includes a read-only fixture path for deterministic UD validation via `validate-run ud_ip_exp --ud-payload-json <path>`.

This manifest is only for tests, fixture-backed dry runs, and development while live UD/IP/EXP PDF identification and extraction rules remain open. It must not be treated as a replacement for the unresolved live extraction rules.

Manifest shape:
- Top-level JSON value is a list of records.
- Each record must include `mail_id` or `entry_id` matching the mail snapshot.
- Each record may include `document_kind` (`UD`, `IP`, or `EXP`); omitted kind defaults to `UD` for backward-compatible UD fixtures.
- Multiple records may reference the same mail when a fixture mail carries UD plus IP/EXP documents.
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
- eventual IP/EXP completion-path tests once the business rules are finalized

## Open Questions Before Production Completion

These must be answered or intentionally hard-blocked before production release:

1. What exact PDF patterns identify UD, IP, and EXP documents?
2. Which extracted fields are mandatory for UD?
3. Which extracted fields are mandatory for IP?
4. Which extracted fields are mandatory for EXP?
5. Which workbook columns besides `UD No. & IP No.` are written by this workflow?
6. How should dates be stored if UD/IP/EXP has date columns?
7. Are existing values in `UD No. & IP No.` appendable, replaceable, or hard-blocking?
8. How should duplicate UD/IP/EXP values already present in a row be handled?
9. Should duplicate-only/no-write `ud_ip_exp` mails move exactly like export duplicate-only mails after successful validation?
10. What source/destination Outlook folders should production use for this workflow?

Until answered, default to hard-block with comprehensive discrepancy reporting rather than guessing.

## Suggested Next Implementation Step

The next highest-value work is whichever of these the team wants to unblock first:
1. Update this handoff and adjacent durable docs whenever the implementation boundary changes.
2. Improve live UD extraction quality using deterministic saved-document analysis fixtures and hard-block reporting, especially around the future ERP LC/SC + `Ship. Remarks` linkage rule.
3. Finalize IP/EXP business rules in docs, then replace the current unresolved-policy hard-block path with deterministic matching and staging logic.

## Guardrails for New Sessions

When starting a new session, use this prompt:

```text
Read AGENTS.md, docs/architecture.md, docs/workflows.md, docs/domain-rules.md, docs/discrepancy-codes.md, PLANS.md, README.md, and docs/ud-ip-exp-implementation-handoff.md. We are implementing workflow ud_ip_exp. Preserve existing export_lc_sc behavior and tests. Do not invent unresolved business rules; hard-block or ask for clarification when the handoff marks a rule open.
```
