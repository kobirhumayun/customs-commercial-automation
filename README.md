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

## Operator setup helpers
When preparing a local config for live Outlook workflows, use the Outlook folder inspection command to discover the real folder `EntryID` values for:
- `source_working_folder_entry_id`
- `destination_success_entry_id`

Example:

```powershell
uv run python -m project inspect-outlook-folders --outlook-profile "outlook"
```

Useful filters:

```powershell
uv run python -m project inspect-outlook-folders --outlook-profile "outlook" --contains "working"
uv run python -m project inspect-outlook-folders --outlook-profile "outlook" --contains "UD and LC"
uv run python -m project inspect-outlook-folders --outlook-profile "outlook" --max-depth 4
```

The command prints JSON with folder `display_name`, `folder_path`, `entry_id`, `depth`, `store_name`, and `parent_entry_id`. Copy the relevant `entry_id` values into your local TOML config.
