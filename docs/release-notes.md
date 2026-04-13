# Release Notes

## Phase 1 Release

### Scope
- Windows-first, Python-first monolithic CLI workflow automation
- deterministic Outlook -> ERP -> workbook -> print -> mail-move execution
- audit-first run artifacts, discrepancy reporting, backup/recovery, and idempotent workbook protection

### Official operator sequence
1. `report-live-readiness`
2. `validate-run`
3. `plan-print`
4. `execute-print`
5. `execute-mail-moves`

### Proven live behavior
- live Outlook snapshot and folder move execution
- live ERP report download and row lookup
- live workbook staging, prevalidation, commit, and recovery
- silent Acrobat printing without relying on the visible GUI window remaining open
- configured-printer support through fallback temporary default-printer switching and automatic restore
- duplicate-only mail handling with no workbook write and no print requirement
- stable document storage under one canonical family path per LC/SC:
  `Year / Buyer Name / LC-or-SC Number / All Attachments`

### Printing behavior
- phase 1 print completion means deterministic silent submission order completed
- phase 1 does not wait for or verify physical paper completion
- when Acrobat times out after physical output, operators must use:
  `acknowledge-partial-print`
- when printer-specific JSObject submission is unavailable, the adapter may:
  - temporarily switch the Windows default printer
  - submit through hidden `AVDoc.PrintPagesSilent`
  - restore the original default printer automatically

### Audit-trail note
- completed runs may still retain discrepancy entries from earlier failed attempts in the same run history
- terminal phase statuses are the authoritative operational state

### Release readiness checklist
- `report-live-readiness` returns `overall_status = "ready"`
- Outlook source/destination `EntryID` values are configured correctly
- ERP form/download flow is validated on the live page
- workbook year, sheet, and header mapping are confirmed
- named-printer silent print path is tested if `print_printer_name` is configured
- at least one full live cycle reaches:
  - `write = committed`
  - `print = completed`
  - `mail move = completed`

### Out of scope for this release
- scheduler/orchestrator service
- web dashboard as the operational control plane
- AI-dependent production decisions
- physical printer completion monitoring
