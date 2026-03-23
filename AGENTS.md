# AGENTS.md

## Purpose
This repository stores the durable architecture and execution guidance for the customs/commercial automation program. Agents should treat the documentation here as the primary source of truth for **how the system must be designed and evolved**.

## Repository operating model
- Phase 1 architecture is **Windows-first**, **Python-first**, and delivered as a **single monolithic repository** with clean internal module boundaries.
- Runtime shape is a set of **manually triggered CLI tools**, not a scheduler-driven platform.
- Deterministic, rule-based automation comes first. AI is a **future extension point only**.
- Auditability, idempotency, discrepancy reporting, and safe Excel writes are non-negotiable.
- The system must preserve workbook fidelity exactly when writing to the yearly master workbook.

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
