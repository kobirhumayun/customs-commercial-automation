# customs-commercial-automation

Repository for the durable architecture, planning guidance, and eventual implementation of a Windows-first customs/commercial automation platform for export and import document workflows.

For the shipped phase-1 operator summary, see `docs/release-notes.md`.

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

## Live PDF printing mode
- Live PDF printing uses hidden Acrobat OLE automation plus the `JSObject` bridge for silent job submission.
- The operator flow does not rely on the visible Acrobat GUI staying open.
- Print execution is acknowledged when the print job is handed off to Acrobat/the target printer queue in deterministic document order.
- Phase 1 does not wait for or verify physical paper completion. Printer conditions such as empty trays remain operator-managed.
- On machines where the COM `JSObject` bridge cannot provide print parameters, the live adapter automatically falls back to hidden `AVDoc.PrintPagesSilent` submission.
- If `print_printer_name` is configured, that fallback temporarily switches the Windows default printer to the configured printer for the submission window, then restores the original default printer.

## Released operator flow
For normal phase-1 operation, the primary operator commands are:
- `report-live-readiness`
- `validate-run`
- `plan-print`
- `execute-print`
- `acknowledge-partial-print` when Acrobat times out after physical paper output
- `execute-mail-moves`
- `recover-run` when a prior run is uncertain or interrupted

The expected terminal paths are:
- `new writes`:
  `validate-run` stages and commits workbook rows, `plan-print`/`execute-print` handle newly saved PDFs, then `execute-mail-moves` moves the mail
- `duplicate-only`:
  the mail is validated against ERP and workbook state, no new workbook row is written, no print is required, and `execute-mail-moves` may still move the mail as intentional duplicate-only handling

## Release Checklist
Before treating a workstation/configuration as release-ready for daily use, confirm:
- `report-live-readiness` returns `overall_status = "ready"`
- Outlook source and destination folder `EntryID` values are configured and verified
- ERP live download succeeds against the real report form
- the yearly workbook path and sheet mapping are verified for the active year
- `print_printer_name`, if configured, has been tested with a real silent print run
- the silent print path is understood:
  JSObject printer submission is preferred, and hidden `AVDoc.PrintPagesSilent` fallback may temporarily switch the Windows default printer before restoring it
- a full live cycle has been completed successfully:
  write committed, print completed, and mail move completed
- operators know the partial-print recovery command:
  `acknowledge-partial-print`

## Phase 1 Released Behavior
The released operator sequence is:
1. `report-live-readiness`
2. `validate-run`
3. `plan-print`
4. `execute-print`
5. `execute-mail-moves`

Operational notes:
- `acknowledge-partial-print` is the only supported recovery step when Acrobat times out after physical paper output.
- When `print_printer_name` is configured and printer-specific JSObject submission is unavailable, the silent fallback may temporarily switch the Windows default printer, submit the job, and then restore the original default printer automatically.
- Completed runs may still retain earlier discrepancy records from failed intermediate attempts in the audit trail; the terminal phase statuses are the authoritative operational state.
- Daily `validate-run` saves PDF attachments when `--document-root` is used, but it no longer performs OCR-based saved-document analysis by default in the released export workflow path.

## Daily Operator Decision Tree
Use this as the normal day-to-day workflow guide after release:

1. Start with `validate-run`.
2. Use `report-live-readiness` at session start, after environment/config changes, or whenever something looks off.
3. If `validate-run` commits new writes and printable documents exist, run `plan-print` and `execute-print`.
4. If `execute-print` completes, run `execute-mail-moves`.
5. If the mail is duplicate-only and no print is required, move directly to `execute-mail-moves` when the run is move-eligible.
6. If any phase is uncertain or interrupted, stop and use the recovery path:
   `recover-run` or `acknowledge-partial-print`

In short:
- every cycle centers on `validate-run`
- print commands are conditional
- mail moves are terminal-path commands
- recovery commands are exception-only

## One-Click Operator Launcher
For daily use on Windows, operators can launch the full happy-path cycle with the bundled scripts:

- reusable PowerShell runner:
  `scripts/run_live_cycle.ps1`
- one-click `export_lc_sc` launcher:
  `scripts/run_export_lc_sc_live_cycle.cmd`

The launcher performs:
- `report-live-readiness`
- `validate-run --apply-live-writes`
- `plan-print`
- `execute-print`
- `execute-mail-moves`
- `report-run-status`

It stops safely if write or print does not complete cleanly and prints the next recovery command instead of blindly continuing.
It also writes a timestamped launcher log under `D:\customs-automation\reports\launcher_logs` so wrapper-level failures can be inspected after the window closes.
For normal live use, the launcher now uses a stable document root such as `D:\customs-automation\documents-live-click` directly rather than creating a timestamped document-root subfolder per run. That keeps all documents for the same LC/SC under the same canonical family folder:
`Year / Buyer Name / LC-or-SC Number / All Attachments`.

## Final E2E Test Commands
Use this sequence for the final end-to-end release check on a fresh live mail:

```powershell
$WORKFLOW = "export_lc_sc"
$CONFIG = "D:\customs-automation\export_lc_sc.toml"
$DOCROOT = "D:\customs-automation\documents-live-click"
```

```powershell
uv run python -m project report-live-readiness $WORKFLOW --config $CONFIG
uv run python -m project validate-run $WORKFLOW --config $CONFIG --live-outlook-snapshot --live-erp --live-workbook --document-root $DOCROOT --apply-live-writes
```

Copy the returned `run_id`, then run:

```powershell
$RUN_ID = "<RUN_ID>"
uv run python -m project plan-print $WORKFLOW --config $CONFIG --run-id $RUN_ID
uv run python -m project execute-print $WORKFLOW --config $CONFIG --run-id $RUN_ID --live-print
uv run python -m project execute-mail-moves $WORKFLOW --config $CONFIG --run-id $RUN_ID --live-outlook
uv run python -m project report-run-status $WORKFLOW --config $CONFIG --run-id $RUN_ID
```

Expected final state:
- `write_phase_status = committed`
- `print_phase_status = completed`
- `mail_move_phase_status = completed`

## Partial print recovery
If `execute-print` returns `print_phase_status = uncertain_incomplete` but Acrobat already submitted paper output, do not rerun print blindly.

Use the operator acknowledgment command to record how many leading PDFs in the planned print group physically printed:

```powershell
uv run python -m project acknowledge-partial-print export_lc_sc --config "D:\customs-automation\export_lc_sc.toml" --run-id "<RUN_ID>" --printed-count <N>
```

Then rerun:

```powershell
uv run python -m project execute-print export_lc_sc --config "D:\customs-automation\export_lc_sc.toml" --run-id "<RUN_ID>" --live-print
```

If all planned PDFs physically printed during timeout/retry attempts, acknowledge the full count. The marker will be finalized as `completed`, and one final `execute-print` pass will close the print phase without sending any additional Acrobat submission commands.

Duplicate suppression is file-number-based. A file already present in `Commercial File No.` must not be written again, even if the incoming mail is otherwise valid.

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

## ERP download debugging
The live ERP page currently behaves like a form-driven download flow rather than a directly parseable HTML table. Use the debug command below to capture the real page state, form behavior, and downloaded export while refining selectors.

Example:

```powershell
uv run python -m project inspect-erp-download export_lc_sc --config "D:\customs-automation\export_lc_sc.toml" --headed
```

Typical selector-driven run:

```powershell
uv run python -m project inspect-erp-download export_lc_sc --config "D:\customs-automation\export_lc_sc.toml" --headed `
  --fill "#fromDate=2026-03-01" `
  --fill "#toDate=2026-03-31" `
  --submit-selector "#btnShow" `
  --post-submit-wait-selector "#downloadDropdown" `
  --download-menu-selector "#downloadDropdown" `
  --download-format-selector "text=CSV"
```

Once the selectors are known, you can store them in the TOML config and rerun the command without repeating all the flags. The config-friendly key for repeated form fields is:

```toml
erp_report_fill_values = ["#fromDate=2026-03-01", "#toDate=2026-03-31"]
```

The command saves:
- page HTML
- full-page screenshot
- downloaded file, when the format click triggers one

By default, artifacts are written under `report_root/erp_debug/<workflow>.<timestamp>/`.
