# PLANS.md

## Objective
Translate the customs/commercial automation architecture into a durable, modular delivery plan that supports safe incremental implementation inside a monolithic Python codebase.

## Target delivery shape
- Single repository
- Python + `uv`
- Separate manually triggered CLI tools
- Shared core services and workflow-specific rule packs
- JSON-first reporting in phase 1
- Windows desktop integrations for Outlook, Excel, Acrobat, Playwright, and local file storage
- Staged workflow execution per run: snapshot first, validate per mail, then apply batched writes, printing, and mail moves

## Phase roadmap

### Phase 0 — Architecture baseline and durable guidance
Goal: make the repository self-describing before implementation begins.
- Maintain architecture docs for system context, workflows, business rules, and operating model.
- Define canonical entities, reports, and failure categories.
- Freeze the phase 1 boundaries: no scheduler, no dashboard, no AI dependency.
- Capture open questions explicitly so they are resolved before sensitive write logic is built.

### Phase 1 — Core platform skeleton
Goal: establish the monolithic modular foundation without workflow-specific complexity.
- CLI entrypoints and command dispatcher.
- Configuration and secrets management.
- Structured logging, run snapshots, and JSON report writer.
- Job identity, idempotency keys, rerun semantics, and local state tracking.
- Shared adapters/contracts for Outlook, file storage, Excel, PDF extraction, OCR, Playwright, and printing.
- Discrepancy reporting contract and future human-review checkpoint hooks.

## Open-question implementation gate matrix (normative)
Use this matrix to decide whether implementation may proceed while a question remains unresolved.

| Open question | Impacted workflow/module | Implementation status | Temporary default behavior | Owner / decision deadline |
|---|---|---|---|---|
| Future business-approved exceptions not yet encoded in rule packs | All workflow-specific rule-pack modules | Can implement with hard-block fallback | Treat unmatched/exception cases as `hard_block`; emit comprehensive discrepancy report with rule lineage | Business + automation engineering / before enabling exception in production |
| New naming-pattern exceptions beyond current canonicalization profiles | Parsing/normalization layer + workflow packs | Can implement with hard-block fallback | Reject unsupported patterns with deterministic discrepancy code; no silent normalization expansion | Automation engineering / before releasing affected workflow update |
| Any request to bypass deterministic candidate tie-break outcomes | Matching/reconciliation engines | Must defer (no bypass in phase 1) | Keep strict tie behavior and block on unresolved ties | Program owner / phase-2+ policy review |
| Any proposal to introduce human-review routing in live decision path | Orchestrator + reporting + routing modules | Must defer (phase 1 exclusion) | Continue `hard_block` default for unspecified/ambiguous outcomes | Program governance / post recurring-issue taxonomy |

### Gate interpretation
- **Can implement with hard-block fallback:** coding may proceed now with deterministic blocking behavior documented in `docs/domain-rules.md` and `docs/workflows.md`.
- **Must defer:** do not implement production-path behavior until a durable architecture/rules update lands.

### Phase 2 — Export LC/SC intake workflow
Goal: support manual export email processing with strict validation and safe workbook append/skip logic.
- Outlook working-folder intake.
- Attachment deduplicated storage plus per-run mail snapshots.
- ERP `rptDateWiseLCRegister` download and normalization.
- Subject/body parsing and file-number extraction.
- LC/SC + PI identification and reconciliation.
- Master workbook append/skip behavior with discrepancy blocking.
- Print batch creation for newly saved PDFs.

### Phase 3 — UD / IP / EXP workflow
Goal: support shared-column population and quantity/value matching rules.
- Use `docs/ud-ip-exp-implementation-handoff.md` as the implementation handoff before coding this workflow.
- UD/IP/EXP PDF extraction.
- Matching candidate workbook rows for a single LC/SC family.
- Combination-based UD allocation logic.
- Ordered shared-column writing rules for UD/EXP/IP values and dates.
- Hard-block discrepancy outcomes for under-specified, ambiguous, or contradictory cases in phase 1 (no human-review routing in this phase).
- Human-review routing is explicitly deferred to a later phase after recurring issue categories are formalized.

### Phase 4 — Import / BTB LC workflow
Goal: process fabric-related import emails and map validated BTB LC data to a single eligible workbook row.
- Subject-based relevance filtering.
- Save and iterate all new import PDFs.
- Extract import LC number/date/value, PI yarn quantity, and related export LC.
- Candidate-row filtering and 40%-80% validation rule.
- One import LC mapped to exactly one row.

### Phase 5 — Bangladesh Bank dashboard verification
Goal: implement verification-only workflow with workbook status results.
- Dashboard login via Playwright.
- Candidate-row filtering from master workbook.
- ERP amendment aggregation and dashboard comparison.
- Status writeback of `OK`, `OK (Kgs)`, or descriptive discrepancy string.

### Phase 6 — Hardening, operations, and future-ready extensions
Goal: productionize the operator experience while preserving deterministic behavior.
- Retry/resume tooling.
- Recovery playbooks for partial failures.
- Validation/rule test suites.
- Packaging and Windows deployment model.
- Optional persistence foundation for PostgreSQL.
- AI-assisted extraction/review extension points that remain off the critical path.

## Cross-cutting implementation tracks
These tracks progress in parallel with the phases above:
- **Rule management:** represent business rules as versioned, testable rule packs.
  - Implement the default contract from `docs/architecture.md` for module layout (shared core + per-workflow packs), execution ordering (core first, workflow exceptions second), decision schema, rule-pack function interface, runtime discovery/loading, and report lineage fields (rule-pack version + applied rule IDs).
- **Excel safety:** preserve formulas, formatting, validation, comments, filters, merges, and protection exactly.
- **Auditability:** every workflow emits machine-readable JSON artifacts with source provenance and write decisions.
- **Idempotency:** no duplicate saves, writes, prints, or conflicting reruns.
- **Operator trust:** anything ambiguous becomes a hard-block discrepancy in phase 1, never a silent guess.

## Ready-for-build checklist
Before coding a workflow, confirm:
- the workflow contract is documented in `docs/workflows.md`
- required business rules are captured in `docs/domain-rules.md`
- write/no-write conditions are explicit
- idempotency keys and report outputs are defined
- open questions affecting the workflow are resolved or intentionally gated behind review
