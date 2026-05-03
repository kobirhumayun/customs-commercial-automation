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
4. `generate-print-annotation-html` for `ud_ip_exp`
5. `execute-print`
6. `execute-mail-moves`

### Proven live behavior
- live Outlook snapshot and folder move execution
- live ERP report download and row lookup
- live workbook staging, prevalidation, commit, and recovery
- silent Acrobat printing without relying on the visible GUI window remaining open
- configured-printer support through fallback temporary default-printer switching and automatic restore
- duplicate-only mail handling with no workbook write and no print requirement
- stable document storage under one canonical family path per LC/SC:
  `Year / Buyer Name / LC-or-SC Number / All Attachments`

### UD/IP/EXP live UD slice
- The `ud_ip_exp` UD-only path has completed a live end-to-end run for a newly saved structured UD Amendment PDF.
- Live proof run: `run-20260423T080206Z-ud_ip_exp-610790f2`.
- The run completed:
  - ERP lookup from email-body file number
  - structured UD Amendment extraction
  - workbook commit
  - one-print-group live print submission
  - post-print mail move
- The committed workbook row was row `372`, with `UD No. & IP No.` = `BGMEA/DHK/AM/2026/4148/017-018`, `UD & IP Date` = `13/04/2026`, and `UD Recv. Date` = `23/04/2026`.
- A later two-mail live proof run, `run-20260423T094320Z-ud_ip_exp-9474d4c6`, completed with `pass = 2`, `hard_block = 0`, six committed workbook writes, two moved mails, and zero discrepancies.
- The later live proof covered the structured UD Amendment zero-`Increased/Decreased` rule for row `402`, where the amendment row's `Value` column was used, and a Base UD MTR quantity case for row `475`, where workbook quantity number formats remained authoritative across the batch.
- `ud_ip_exp` structured UD writes populate `UD No. & IP No.`, `UD & IP Date`, and `UD Recv. Date`; both date columns use `DD/MM/YYYY`.
- IP/EXP completion rules remain intentionally unresolved and continue to hard-block until durable business rules are finalized.

### Printing behavior
- phase 1 print completion means deterministic silent submission order completed
- phase 1 does not wait for or verify physical paper completion
- for `ud_ip_exp`, the print-annotation checklist is a mandatory pre-print artifact gate
- if checklist generation fails, print and post-run mail moves stay hard-blocked
- if a no-write live run leaves `eligible_mail_count = 0`, the launcher now skips `execute-mail-moves` instead of generating a misleading downstream mail-move gate discrepancy
- the checklist HTML is opened automatically only after successful post-run mail moves complete
- when Acrobat times out after physical output, operators must use:
  `acknowledge-partial-print`
- when printer-specific JSObject submission is unavailable, the adapter may:
  - temporarily switch the Windows default printer
  - submit through hidden `AVDoc.PrintPagesSilent`
  - restore the original default printer automatically

### Audit-trail note
- completed runs may still retain discrepancy entries from earlier failed attempts in the same run history
- terminal phase statuses are the authoritative operational state

### UD allocation note
- structured `ud_ip_exp` allocation now evaluates exact-value workbook row combinations within one LC/SC family instead of only the earliest contiguous prefix, so later exact matches and non-prefix exact combinations can still resolve deterministically

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
- for `ud_ip_exp`, the bundled one-click launcher is:
  `scripts/run_ud_ip_exp_live_cycle.cmd`

### Out of scope for this release
- scheduler/orchestrator service
- web dashboard as the operational control plane
- AI-dependent production decisions
- physical printer completion monitoring
