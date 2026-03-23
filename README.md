# customs-commercial-automation

Repository for the durable architecture, planning guidance, and eventual implementation of a Windows-first customs/commercial automation platform for export and import document workflows.

## Current repository contents
- `AGENTS.md` — repository-level operating instructions for agents and contributors.
- `PLANS.md` — phased roadmap for turning the architecture into implementation.
- `docs/architecture.md` — system architecture and module boundaries.
- `docs/workflows.md` — workflow-specific contracts for each CLI tool.
- `docs/domain-rules.md` — durable business rules and invariants.

## Phase 1 architectural direction
- Monolithic Python codebase with clear internal modules.
- Manually triggered CLI tools for each workflow.
- Windows desktop integrations with Outlook, Excel, Acrobat, Playwright, local storage, and JSON reporting.
- Deterministic rule-based automation first; AI later as an extension point.
