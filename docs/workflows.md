# Workflow Specifications

## Shared workflow contract
Every CLI workflow should follow the same control shape:
1. capture operator execution context
2. capture a run-level snapshot of all source emails/documents from the relevant manual intake location
3. determine deterministic mail iteration order for the snapshot by:
   - primary key: `ReceivedTime` converted to the workflow state timezone configured for operations (current deployment basis: Bangladesh Standard Time, UTC+06:00)
   - tie-breaker: ascending Outlook `EntryID`
4. save only new attachments/documents while iterating the snapshotted mails in that order
5. extract and normalize entities per mail
6. run workflow rule packs and stage per-mail write/print/move outcomes
7. apply a controlled batch workbook-write phase for successful mails only
8. derive deterministic print-group order from the successful mail groups using the earliest master-workbook row sequence assigned to each group
9. emit JSON reports for both the run and each mail outcome, including persisted `mail_iteration_order` and final `print_group_order`
10. batch print only after the workbook-write phase completes successfully for the eligible mails
11. perform post-run mail moves for successful mails only

### Batch write contract (normative)
Batch atomicity applies only to mails with approved staged write operations, not to all mails in the run snapshot.
If one mail in the run snapshot is blocked while others are approved, the blocked mail contributes no workbook writes and each approved mail still participates in the same atomic commit of the approved staged write set.
Example run (3 mails): Mail A = blocked, Mail B = approved (2 staged writes), Mail C = approved (1 staged write) ⇒ batch write outcome: commit Mail B + Mail C writes together (3 total) in one atomic transaction; commit none if that transaction fails; Mail A writes remain zero.

## Export LC/SC intake

### Inputs
- Outlook folder: `working` after operator triage from `temp-export`; snapshot all messages in the folder when the CLI is triggered
- ERP report: `rptDateWiseLCRegister`
- Attachments: LC/SC PDFs and PI PDFs

### Deterministic checks
- parse subject into document type, LC/SC end sequence, buyer, and optional suffix
- extract all body file numbers matching `P/<yy>/<nnnn>`
- validate every extracted file number through ERP lookup and pathing rules while retaining all file numbers for audit
- define LC/SC family consistency using LC/SC number, normalized buyer, and LC/SC date
- canonical row selection follows ERP row order
- the first occurrence row is the canonical row for that file number/family context
- canonical row fields drive folder path construction, workbook mapping, and reporting metadata
- duplicate true-equivalent ERP rows do not alter canonical selection once the first occurrence is chosen
- hard-block if the extracted file numbers do not resolve to the same LC/SC family; any partial family match is a hard block
- normalize ERP buyer name by splitting on `\`, trimming whitespace, and trimming trailing periods
- hard-block if normalized subject buyer and LC/SC number do not exactly match ERP-derived values
- identify base/amendment context from ERP `Amd No`, clause text, and attachment naming patterns

Example (canonical selection): if two ERP rows are true-equivalent for `P/26/0042` and appear as row 118 then row 241, row 118 remains canonical and its fields are used for folder pathing, workbook mapping, and reporting metadata.

### Workbook mapping
Use ERP fields to populate:
- `Name of Buyers` ← `Buyer Name`
- `L/C Issuing Bank` ← `Notify Bank`
- `L/C & S/C No.` ← `LC No.`
- `LC Issue Date` ← `LC DT.`
- ` Amount` ← `Current LC Value`
- `Shipment Date` ← `Ship. DT.`
- `Expiry Date` ← `Expiry DT.`
- `Quantity of Fabrics (Yds/Mtr)` ← `LC Qty`
- `L/C Amnd No.` ← `Amd No`
- `L/C Amnd Date` ← `Amd DT`
- `Lien Bank` ← `Nego Bank`
- `Master L/C No.` ← `Master LC No.`
- `Master L/C Issue Dt.` ← `M.L/C Date`
- `Commercial File No.` ← `File No.`

### No-write rules
- subject mismatch
- any extracted file number is missing its required ERP row
- any partial family match across LC/SC number, normalized buyer, and LC/SC date
- duplicate file number already present when workflow expects skip
- ambiguous document identity not resolved by rules
- any incomplete validation needed for append/skip decision

### Batch execution behavior
- blocked emails remain in `working`
- successfully processed export-team emails move to `UD and LC` only after the batch workbook-write and batch print phases finish
- print batches are built from successful mails in the active run snapshot, using only newly saved PDFs

## UD / IP / EXP processing

During the initial live-deployment phase, any mismatch, unknown exception, or incomplete rule condition should hard-block with a comprehensive report rather than route to human review.

### Inputs
- Outlook folder: `working`; snapshot all messages in the folder when the CLI is triggered
- PDF attachments for UD, EXP, and/or IP
- Existing master workbook rows for the same LC/SC family

### Shared-column behavior
- Column `UD No. & IP No.` stores UD/EXP/IP values together.
- UD entries have no prefix.
- EXP entries use `EXP: ` prefix.
- IP entries use `IP: ` prefix.
- When both EXP and IP exist, EXP must be listed before IP.
- Multiple entries are line-break separated.

### UD allocation logic
- extract UD number, date, LC/SC number, quantity, quantity unit, and relevant values
- find candidate rows for the LC/SC family
- select the first occurrence of each required row value, even when combinations are non-sequential
- maintain a multiset/bag of remaining values so duplicates are handled correctly
- write UD number to matched rows only if quantity rules are satisfied
- ignore excess quantity only when excess is at least 50 yards/meters; otherwise hard-block

### IP / EXP rules
- no amendment model
- each document is newly issued against a specific LC/SC or amendment
- when multiple IP/EXP docs appear in one mail, their total value and quantity should match LC total unless a future documented exception applies
- dates must be written line-by-line aligned with their corresponding numbers

## Import / BTB LC processing

### Inputs
- Outlook folder: `working` after operator triage from `temp-import`; snapshot all messages in the folder when the CLI is triggered
- Fabric-related import/back-to-back LC emails identified by case-insensitive substring matching on fabric keywords in the subject, with the keyword list stored in code

### Extraction targets
- BTB LC number
- BTB LC date
- BTB LC value
- yarn quantity from PI
- related export LC number from clause text

### Candidate row rules
- export LC matches related export LC
- `UP No.` blank
- BTB LC target field blank
- choose the first row where BTB LC value is between 40% and 80% of export LC value
- one import LC maps to one row only

### Batch execution behavior
- blocked emails remain in `working`
- successfully processed import-team emails move to `Import` only after the batch workbook-write and batch print phases finish

## Bangladesh Bank dashboard verification

### Candidate filters
Rows where:
- `UP No.` is blank
- UD value exists
- dashboard field is blank, not `OK`, or not `OK (Kgs)`

### Verification result values
- `OK` when quantity matches LC Qty and remaining fields are consistent
- `OK (Kgs)` when quantity mismatches LC Qty but matches Net Weight and remaining fields are consistent
- otherwise write a combined descriptive discrepancy string into `Bangladesh Bank Dashboard`

## Printing
- only newly saved PDFs are printed
- print groups are organized by originating mail from the active run snapshot
- group order follows master-workbook row sequence across mail groups
- within each mail group, print every newly saved PDF exactly in saved/staged order, with no additional intra-group sorting
- insert exactly one blank page between consecutive mail groups
- persist final print group order in run JSON metadata
- any print failure must be reported with retry/review metadata
