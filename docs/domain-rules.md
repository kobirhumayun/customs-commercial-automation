# Domain Rules and Invariants

## Global invariants
- Never modify the master workbook unless all required validation rules pass.
- If rules are incomplete, contradictory, or not satisfied, do not write; produce a discrepancy report instead.
- All workflows must be idempotent and safe to rerun.
- Each run must begin from a fixed snapshot of the messages currently present in `working` for that workflow.
- New documents must never overwrite existing local files.
- Focus on new PDFs only.
- All extracted file numbers from an email remain in the report for traceability and must be validated as belonging to the same LC/SC family before processing can continue. Family consistency is determined by LC/SC number, normalized buyer, and LC/SC date.

## File storage rules

### Export path
`Year (LC/SC opening year) / Buyer Name / LC Number or Sales Contract Number / All Attachments`

### Import path
Designated import root organized by year.

### Duplicate rule
A duplicate PDF is defined only by identical filename.

## Subject and naming rules

### Export subject parsing targets
- prefix: `LC` or `SC`
- LC/SC number end sequence
- buyer name
- optional suffixes such as `_ACK` and amendment markers

### Supported examples
- `LC-0515-L-DESIGNER FASHION LTD_ACK`
- `LC-0038-ANANTA GARMENTS LTD_AMD_05`
- `SC-010-PDL-8-ZYTA APPARELS LTD`
- `LC-05NU384-DESIGNER FASHION LTD`
- `LC-092387-COMTRADING APPAREL`

### Naming conventions assumed reliable in phase 1
- PI: `PDL-YY-NNNN` with optional revision like `R4`
- UD: `UD-LC/SC-LC/SC_ENDSEQ-BUYERNAME_EXTENSION`
- LC standard/amendment/ack patterns as documented
- IP example: `IP-LC-0043-VINTAGE DENIM STUDIO LTD`
- EXP example: `[Invoice]-EXP [Extension]`

## ERP normalization rules
- ERP report is `rptDateWiseLCRegister`.
- Headers are read from row 2 of sheet 1.
- Canonical row selection follows ERP row order.
- The first occurrence row is the canonical row for that file number/family context.
- Canonical row fields drive folder path construction, workbook mapping, and reporting metadata.
- Duplicate true-equivalent ERP rows do not alter canonical selection once the first occurrence is chosen.
- When multiple file numbers are extracted from one email, each must be validated against ERP and all resolved rows must be consistent with the same LC/SC family.
- Any partial family match is a hard block.
- `Buyer Name` may contain an address separated by `\`; normalize by taking the buyer segment, trimming whitespace, and removing trailing periods.

Example (canonical selection): if two true-equivalent ERP rows for `P/26/0042` are found at row 118 and row 241, row 118 is canonical and row 241 cannot replace it for pathing, workbook mapping, or reporting metadata.

## Master workbook rules
- One master workbook per year.
- Headers are in row 2 of sheet 1.
- Export workflow generally appends new rows.
- Before appending, check whether the same file number already exists.
- If the same file number exists, skip that file.
- If required, first attempt to locate an existing row for the same file/amendment to avoid duplicate insertion.
- Operational ordering is row sequence and drives staged write ordering, reporting, and print ordering.

## Workbook preservation rules
These must remain exactly as-is after writes:
- formulas
- styles
- merged cells
- conditional formatting
- filters
- comments
- validations
- workbook protection

## Quantity formatting rule
For `Quantity of Fabrics (Yds/Mtr)`:
- if ERP `LC Unit` is `YDS`, preserve existing number format
- if ERP `LC Unit` is `MTR`, apply `#,###.00 "Mtr"`

## Export amendment rules
- A new file is typically created for each new export LC/SC or for some amendments.
- A file may contain the base LC and one or more amendments if received together.
- A single file must never contain two different LCs.
- Some amendments create a new file only when they independently increase value/quantity with new PI coverage.
- Dependent amendments may be added to an existing file instead of receiving a new file.

## UD/IP/EXP shared column rule
Column `UD No. & IP No.` stores:
- UD numbers for local buyers with no prefix
- `EXP: ` prefixed values for EXP
- `IP: ` prefixed values for IP
- EXP listed before IP when both exist
- multiple values separated by line breaks

## Bangladesh Bank rules
Verification uses:
- workbook LC/SC number and master LC number
- ERP totals across amendments for value, quantity, and net weight
- shipment and expiry dates from final amendment rows

The dashboard column is verification-only and should not be used to drive other writes in phase 1.

## Confirmed phase 1 exclusions
- Buyer-type inference for UD/IP/EXP is excluded entirely from phase 1.
- Human-review routing is excluded from the initial live-deployment path; unspecified failures default to hard block with comprehensive reporting until recurring issue categories are formally classified.

## Initial exception-handling rule
- Any naming mismatch, unsupported rule exception, or partially specified case must hard-block and produce a comprehensive discrepancy report during early deployment.
- Business-approved exceptions should be implemented only inside workflow-specific rule-pack modules and should run after standard validation rules.

## Import relevance rule
- Fabric-related import emails are identified by case-insensitive substring matching against the subject keyword list stored in code.

## Staged run execution rule
- A run snapshots all candidate emails before validation and side effects begin.
- Validation outcomes are decided per mail, but workbook writes, printing, and email moves execute in controlled post-validation phases.
- One blocked mail does not invalidate unrelated successful mails in the same run.

## Outlook post-processing rule
- Blocked emails remain in `working`.
- Successfully processed export-team emails move to `UD and LC` only after batch workbook writes and printing complete.
- Successfully processed import-team emails move to `Import` only after batch workbook writes and printing complete.

## Open questions that remain intentionally unresolved
- Any future business-approved exceptions to the documented value/quantity matching constraints or naming conventions that have not yet been encoded in workflow-specific rule-pack modules.
