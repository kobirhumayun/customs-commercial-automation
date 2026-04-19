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
- Rule pack stub: present at `project/rules/workflows/ud_ip_exp/__init__.py`
- Rule pack id: `ud_ip_exp.default`
- Rule pack version: `1.0.0`
- Implementation status: not implemented beyond shared workflow infrastructure and placeholder rule-pack registration.
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

Add workflow-specific payload models under `project/workflows/ud_ip_exp/`.

Expected modules:
- `payloads.py`
- `parsing.py` or `extraction.py`
- `staging.py`
- optional `document_classification.py` if shared export classification cannot be reused cleanly

The payload should carry:
- parsed/extracted document type: UD, IP, EXP
- extracted LC/SC number
- extracted quantities and units
- extracted UD/IP/EXP identifiers and dates
- selected workbook candidate rows
- extraction provenance/confidence, where available
- saved document metadata

### 2. Workbook Header Mapping

Extend workbook mapping with UD/IP/EXP-owned columns.

At minimum, confirm and map:
- `UD No. & IP No.`

Do not assume additional workbook columns without verifying `docs/workflows.md`, `docs/domain-rules.md`, the real workbook headers, and user confirmation.

### 3. Candidate Matching

Implement candidate row selection from an existing workbook snapshot:
- Match rows by LC/SC family.
- Compare quantities using normalized numeric values and unit compatibility.
- Generate all valid candidate combinations for UD allocation.
- Score candidates using the normative tie-break keys.
- Persist/report all candidate evidence.

### 4. Write Staging

Stage workbook operations using existing `WriteOperation` contracts.

Required behavior:
- No direct Excel writes during validation.
- All target cells must be prevalidated before live write.
- If updating a shared multiline column, use an explicit expected pre-write value and expected post-write value.
- Preserve existing line-break ordering and avoid duplicate UD/IP/EXP entries.
- Treat conflicting non-blank target values as hard-block unless the rule explicitly permits append/update.

### 5. Rule Pack

Replace the placeholder rule pack in `project/rules/workflows/ud_ip_exp/__init__.py` with deterministic rules.

Likely rule categories:
- required document classification present
- required extraction fields present
- LC/SC family match
- quantity/unit compatibility
- candidate combination selected
- no full tie after deterministic scoring
- workbook target update permitted

Any new discrepancy code must first be added to `docs/discrepancy-codes.md`.

### 6. Validation Orchestration

Extend shared validation branching currently focused on `export_lc_sc` so `ud_ip_exp` can:
- build its workflow payload
- save/classify documents
- run rules
- stage workbook operations
- emit mail outcomes and reports

Keep export behavior unchanged. Add tests proving `export_lc_sc` still passes.

### 7. Print and Mail Move

Confirm whether UD/IP/EXP requires printing all newly saved PDFs, only selected document types, or no print. Until clarified, do not invent a different transport policy.

If print is required:
- reuse `plan_print_batches`
- ensure only eligible saved PDFs are print-planned
- preserve deterministic order

If print is not required for some terminal path:
- document that policy and encode it explicitly in mail outcomes and mail-move eligibility.

## Deterministic Fixture Manifest

The initial implementation includes a read-only fixture path for deterministic UD validation via `validate-run ud_ip_exp --ud-payload-json <path>`.

This manifest is only for tests, fixture-backed dry runs, and development while live UD/IP/EXP PDF identification and extraction rules remain open. It must not be treated as a replacement for the unresolved live extraction rules.

Manifest shape:
- Top-level JSON value is a list of records.
- Each record must include `mail_id` or `entry_id` matching the mail snapshot.
- Each record must include `document_number`, `document_date`, and `lc_sc_number`.
- `quantity` and `quantity_unit` should be supplied for UD allocation.
- Optional confidence/provenance fields are supported for required extracted values.

See `docs/ud-ip-exp-payload-manifest-example.json` for a minimal example.

## Tests to Add

Minimum test groups:
- `tests/test_ud_ip_exp_payloads.py`
- `tests/test_ud_ip_exp_staging.py`
- `tests/test_ud_ip_exp_rules.py`
- CLI integration tests in `tests/test_cli.py`
- regression tests proving `export_lc_sc` behavior unchanged

Core scenarios:
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

## Open Questions Before Coding

These must be answered or intentionally hard-blocked before production release:

1. What exact PDF patterns identify UD, IP, and EXP documents?
2. Which extracted fields are mandatory for UD?
3. Which extracted fields are mandatory for IP?
4. Which extracted fields are mandatory for EXP?
5. Which workbook columns besides `UD No. & IP No.` are written by this workflow?
6. How should dates be stored if UD/IP/EXP has date columns?
7. Are existing values in `UD No. & IP No.` appendable, replaceable, or hard-blocking?
8. How should duplicate UD/IP/EXP values already present in a row be handled?
9. Does UD/IP/EXP require print execution, or only save/write/mail-move?
10. Should duplicate-only/no-write mails be moved, left in working, or reported only?
11. What source/destination Outlook folders should production use for this workflow?

Until answered, default to hard-block with comprehensive discrepancy reporting rather than guessing.

## Suggested First Implementation Step

Start with read-only tests and models:
1. Add UD/IP/EXP payload dataclasses.
2. Add workbook header mapping for `UD No. & IP No.` only.
3. Add candidate-combination selection as a pure function with unit tests.
4. Add rule-pack tests for the deterministic tie behavior.

Only after those pass should live workbook staging be connected.

## Guardrails for New Sessions

When starting a new session, use this prompt:

```text
Read AGENTS.md, docs/architecture.md, docs/workflows.md, docs/domain-rules.md, docs/discrepancy-codes.md, PLANS.md, README.md, and docs/ud-ip-exp-implementation-handoff.md. We are implementing workflow ud_ip_exp. Preserve existing export_lc_sc behavior and tests. Do not invent unresolved business rules; hard-block or ask for clarification when the handoff marks a rule open.
```
