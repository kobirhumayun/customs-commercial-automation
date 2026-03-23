# Workflow Specifications

## Shared workflow contract
Every CLI workflow should follow the same control shape:
1. capture operator execution context
2. collect all source emails/documents from the relevant manual intake location
3. save only new attachments/documents
4. extract and normalize entities
5. run workflow rule pack
6. either hard-block or write safely during the initial live-deployment phase
7. emit JSON report
8. print only when the workflow completed successfully and printing applies

## Export LC/SC intake

### Inputs
- Outlook folder: `working` after operator triage from `temp-export`; process all messages in the folder when the CLI is triggered
- ERP report: `rptDateWiseLCRegister`
- Attachments: LC/SC PDFs and PI PDFs

### Deterministic checks
- parse subject into document type, LC/SC end sequence, buyer, and optional suffix
- extract all body file numbers matching `P/<yy>/<nnnn>`
- validate every extracted file number through ERP lookup and pathing rules while retaining all file numbers for audit
- hard-block if the extracted file numbers do not resolve to the same LC/SC family
- normalize ERP buyer name by splitting on `\`, trimming whitespace, and trimming trailing periods
- hard-block if normalized subject buyer and LC/SC number do not exactly match ERP-derived values
- identify base/amendment context from ERP `Amd No`, clause text, and attachment naming patterns

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
- duplicate file number already present when workflow expects skip
- ambiguous document identity not resolved by rules
- any incomplete validation needed for append/skip decision

## UD / IP / EXP processing

During the initial live-deployment phase, any mismatch, unknown exception, or incomplete rule condition should hard-block with a comprehensive report rather than route to human review.

### Inputs
- Outlook folder: `working`; process all messages in the folder when the CLI is triggered
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
- Outlook folder: `working` after operator triage from `temp-import`; process all messages in the folder when the CLI is triggered
- Fabric-related import/back-to-back LC emails identified by case-insensitive substring matching on fabric keywords in the subject

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
- print groups are organized by originating mail
- group order follows workbook row sequence
- insert one blank page between mail groups
- any print failure must be reported with retry/review metadata
