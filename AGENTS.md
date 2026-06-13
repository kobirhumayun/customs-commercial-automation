# AGENTS.md

## Purpose
This repository stores the durable architecture and execution guidance for the customs/commercial automation program. Agents should treat the documentation here as the primary source of truth for **how the system must be designed and evolved**.

## Repository operating model
- Phase 1 architecture is **Windows-first**, **Python-first**, and delivered as a **single monolithic repository** with clean internal module boundaries.
- Runtime shape is a set of **manually triggered CLI tools**, not a scheduler-driven platform.
- Deterministic, rule-based automation comes first. AI is a **future extension point only**.
- Auditability, idempotency, discrepancy reporting, and safe Excel writes are non-negotiable.
- The system must preserve workbook fidelity exactly when writing to the yearly master workbook.
- Workflow execution should follow a staged run model: **run-level snapshot → mail-level validation → batch workbook write → batch print → post-run mail moves**.

## Canonical architecture documents
Read these documents before changing code or planning major work:
1. `docs/architecture.md` — system context, modules, workflows, data model, and safety constraints.
2. `docs/workflows.md` — workflow-level requirements and validation boundaries for each CLI tool.
3. `docs/domain-rules.md` — durable business rules, reconciliation constraints, and Excel mapping rules.
4. `PLANS.md` — phased implementation roadmap and delivery sequencing.

## How agents should work in this repo
- Keep changes aligned with the documented module boundaries and workflow boundaries.
- Prefer extending the architecture documents before introducing implementation that changes behavior or operating assumptions.
- Record unresolved ambiguities in the appropriate docs instead of silently inventing business rules.
- Preserve the distinction between:
  - **hard block**: no write, produce discrepancy report
  - **human review**: reserved for a later phase after common failure patterns are categorized
  - **successful write**: validations passed, report emitted, downstream print sequencing updated
- During early live deployment, any case that fails to satisfy all specified parameters should default to a hard block with a comprehensive report rather than a review workflow.
- When planning code, design for reusable shared services plus workflow-specific rule packs.
- Any future implementation should use `uv` as the package manager and keep Windows desktop integrations explicit.

## Finalized workflow preservation protocol
- Treat already finalized workflows as behavior-frozen unless the user explicitly approves a behavioral change for that workflow.
- For finalized workflows, the effective source of truth is the combination of:
  - the current implementation
  - the existing regression tests
  - the durable docs
- If those three sources are not perfectly aligned, agents must preserve current runtime behavior first and update docs separately rather than silently reinterpreting the workflow.
- New workflow implementation work, including `import_btb_lc`, must prefer workflow-scoped extensions over shared-behavior rewrites.
- Shared models, shared report payloads, shared recovery logic, shared print logic, and shared mail-move logic must not be changed in a way that alters finalized workflow outputs unless regression coverage is updated intentionally and the behavioral change is explicitly approved.
- Additive backward-compatible fields are preferred over renaming or removing fields that finalized workflows or existing tests already consume.
- Bug fixes in finalized workflows must be minimal-scope fixes for the observed defect; do not use a bug-fix task as justification to "clean up" surrounding logic, normalize other workflows, or retrofit new workflow assumptions into older flows.
- When a requested fix touches a shared component, agents must check which finalized workflows consume that component and evaluate regression risk before editing.
- If a proposed change could alter finalized workflow behavior, pause and surface that risk explicitly instead of assuming cross-workflow consistency is desired.

## AI-agent bug-fix discipline
- Do not infer that two workflows should behave the same just because they use similar terms such as LC, PI, print, duplicate, report, or mail move.
- Do not rewrite older workflow logic to match newer workflow documentation without explicit approval.
- When fixing a finalized workflow, preserve existing field names, artifact names, phase semantics, and discrepancy behavior unless the task explicitly calls for changing them.
- Before modifying shared code for one workflow, identify the exact tests and workflow paths that protect the other finalized workflows and use that evidence to constrain the edit.

## Documentation maintenance rules
- The repository should contain durable architecture guidance, not ephemeral prompt files.
- When architecture guidance is finalized, keep it in `AGENTS.md`, `PLANS.md`, and the `docs/` files listed above.
- If a prompt file is used temporarily for authoring, it should be removed after its content has been incorporated into durable docs.

## Out-of-scope assumptions for phase 1
- No web dashboard.
- No scheduler/orchestrator service.
- No mandatory PostgreSQL dependency for phase 1 execution.
- No AI-dependent production path.

## Current clarification gaps to preserve
If implementation starts before these are answered, treat them as open questions rather than fixed rules:
- Any future business-approved exceptions that have not yet been encoded into workflow-specific rule-pack modules.

## Confirmed phase 1 exclusions
- Buyer-type inference for UD/IP/EXP stays out of phase 1 entirely unless explicitly reintroduced by a later business decision.
- Human-review routing is not part of the initial live-deployment decision path; unspecified failures default to hard block until common issue categories are formally defined.
