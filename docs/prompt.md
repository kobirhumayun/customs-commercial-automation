# Finalized Architecture Prompt (Revised with Clarifications)

You are a senior enterprise solutions architect. Design a **robust, scalable, modular Windows automation architecture** in **Python** for a customs/commercial operations team in a denim fabrics and garments business.

## Scope
Design the **system architecture only**. Do **not** write implementation code.

The first release must be designed as a **single monolithic codebase with clear internal modules**, exposed as a **set of separate CLI tools**. Each workflow is launched **manually by an operator**. There is **no scheduler requirement in phase 1**.

The architecture must support:
- deterministic, rule-based automation first
- safe incremental rollout of independent CLI tools
- strong auditability and discrepancy reporting
- surgical Excel updates with zero unintended format breakage
- future AI-assisted orchestration only as an extension point after workflows are battle-tested

---

## Business Domain
The business produces **denim fabrics and garments**. The automation domain covers customs/commercial document handling related to export and import operations.

The key business documents include:
- Export Letters of Credit (LC)
- Sales Contracts (SC)
- Proforma Invoices (PI)
- Import / Back-to-Back LCs (BTB LC)
- Utilization Declarations (UD)
- Import Permissions (IP)
- Export Forms (EXP)
- Bank payment-related records
- Permissions from bond/government office
- Association declarations required against LCs

The architecture must be extensible enough to support future automation tasks in this domain.

---

## Required Runtime Shape
Design the system as:
- **A set of manually triggered CLI tools**
- **Windows-first**
- **Monolithic repository / deployable application** with clean internal modular boundaries
- **Local desktop integration** with Outlook, Excel, Adobe Acrobat, Playwright, file system, and JSON reporting
- **No operator dashboard required in phase 1**; log files and structured JSON reports are sufficient

---

## Preferred Technology Constraints
Use these as preferred technologies unless there is a strong architectural reason to recommend an alternative:
- **Language:** Python
- **Package manager:** uv
- **Database:** PostgreSQL (future-ready or optional foundation; phase 1 reporting may remain JSON-only)
- **Excel read/write:** xlwings
- **PDF parsing:** PyMuPDF, pdfplumber
- **OCR / image processing:** Pillow, Tesseract, OCRmyPDF, img2table
- **Data processing:** pandas
- **Web automation / scraping:** Playwright
- **Printing:** Adobe Acrobat SDK
- Recommend extra technologies only when clearly justified.

---

## Core Design Problem
Design an architecture that automates Windows-based business workflows involving:
1. Outlook email intake using **local Outlook desktop automation (COM/pywin32)**
2. Attachment download and deduplicated local storage
3. PDF extraction from text PDFs, scanned PDFs, and hybrid PDFs
4. ERP Excel report download via Playwright
5. Cross-source reconciliation using email subject/body, PDFs, ERP Excel, and master Excel workbook
6. Surgical updates to an existing master Excel workbook while preserving its formatting, formulas, styles, validations, filters, merged cells, comments, and protection exactly as-is
7. Structured JSON reporting for audit trails, discrepancies, and downstream steps
8. Automatic printing of newly saved PDFs in business-defined order
9. Future extensibility for AI-assisted orchestration, without coupling phase 1 to AI

---

## General Processing Principles
Your architecture must explicitly enforce these principles:
- **Never modify the master Excel workbook unless all required validation rules pass**
- If rules are incomplete, contradictory, or not satisfied, the system must **not write** and must instead generate a discrepancy report
- Workflows must be **idempotent** and safe to re-run
- Every action must be **auditable**
- Focus on **new PDFs only**
- New documents must never overwrite existing local files
- All workflows must be decomposed into reusable, independently triggerable CLI tools
- Borderline or ambiguous cases must support **human review/approval before write**
- Stable access to Outlook, ERP, database, and portals may be assumed during processing

---

## Storage Rules
### Local File Storage
All documents and JSON reports live on a **local PC**.

### Duplicate PDF Rule
A duplicate PDF is defined by **same filename**.

### Reporting Persistence
For now, metadata and processing results need only be stored in **JSON reports**. PostgreSQL may be proposed as a future-ready persistence layer, but JSON reporting is sufficient for phase 1.

---

## Folder Structure Requirements
### Export documents
Save into:
- `Year (LC/SC opening year) / Buyer Name / LC Number or Sales Contract Number / All Attachments`

### Import documents
Save into:
- a designated import folder organized by year

The system must:
- create missing folders automatically
- check duplicates before saving
- avoid overwriting files
- persist file paths grouped by originating email/job

---

## Master Excel Workbook Context
The system writes into **one master workbook per year**.

Automation may assume **exclusive access during writes**.

### General Write Strategy
For export LC/SC processing:
- the system generally **appends new rows**
- but before appending it must check whether the **same file number already exists in the master workbook**
- if the same file number already exists, **skip that file**
- if needed, the system should first try to locate an existing row for the same file/amendment to avoid duplicate insertion

### Important Preservation Requirement
The architecture must preserve **exactly as-is**:
- formulas
- styles
- merged cells
- conditional formatting
- filters
- comments
- validations
- workbook protection

There are **no workbook macros** that need accommodation.

### Relevant Master Workbook Headers
Headers are in **row 2 of sheet 1**. Relevant columns include:
- `Name of Buyers`
- `L/C Issuing Bank`
- `L/C & S/C No.`
- `LC Issue Date`
- ` Amount`
- `Shipment Date`
- `Expiry Date`
- `Quantity of Fabrics (Yds/Mtr)`
- `L/C Amnd No.`
- `L/C Amnd Date`
- `Lien Bank`
- `Master L/C No.`
- `Master L/C Issue Dt.`
- `UD No. & IP No.`
- `UD & IP Date`
- `BTB LC No.`
- `BTB LC Issue Date`
- `Amount`
- `Quantity (Kgs)`
- `UP No.`
- `UP Date`
- `Bangladesh Bank Dashboard`
- `Commercial File No.`
- `Remarks`

### Row Identity / Ordering
Use **row sequence** as the operational ordering concept for later printing and reporting.

---

## ERP Report Dependency
Before export email processing, the system must download the ERP Excel report **`rptDateWiseLCRegister`** via Playwright.

Headers are in **row 2 of sheet 1** and include:
- `File No.`
- `Buyer Name`
- `LC No.`
- `LC DT.`
- `Amd No`
- `Amd DT`
- `Current LC Value`
- `LC Qty`
- `LC Unit`
- `Ship. DT.`
- `Expiry DT.`
- `Nego Bank`
- `Notify Bank`
- `Master LC No.`
- `M.L/C Date`
- `PI Number`
- `Net Weight`
- etc.

The architecture must describe how the system:
- downloads the report
- normalizes the header row
- extracts a single row per file number
- handles rare duplicate rows for a file number by selecting **any one** because they are duplicates
- uses one extracted file number from the email body to retrieve LC/SC number, buyer name, LC/SC date, amendment information, PI references, and other mapped data

### Buyer Name Normalization
The ERP `Buyer Name` field may include an address separated by `\`.
Normalize it by:
- isolating the buyer name
- trimming whitespace
- trimming trailing periods

### Subject Validation Rule
Compare buyer name using **exact match after normalization**.
If the parsed mail subject does not match ERP-derived buyer name and LC/SC number, the item is **fully blocked**.

---

## Outlook Export Workflow
### Manual Intake Step
The operator reviews emails in Outlook inbox subfolder `temp-export`, selects emails containing:
- LC + PI, or
- SC + PI

and moves them into the Outlook subfolder `working`.

Automation begins only when the operator manually triggers the relevant CLI tool.

### Email Subject Pattern
Typical subject examples:
- `LC-0515-L-DESIGNER FASHION LTD_ACK`
- `LC-0038-ANANTA GARMENTS LTD_AMD_05`
- `SC-010-PDL-8-ZYTA APPARELS LTD`
- `LC-05NU384-DESIGNER FASHION LTD`
- `LC-092387-COMTRADING APPAREL`

The architecture must support parsing of:
- document type prefix (`LC` or `SC`)
- LC/SC number end-sequence
- buyer name
- optional suffixes such as `_ACK` or amendment indicators

### Email Body File Numbers
Extract all file numbers using regex matching this structure:
- `P/<2-digit year>/<4-digit zero-padded sequence>`

Examples:
- `P/26/0214`
- `P/25/0004`

Store **all extracted file numbers** in the job context, but use **one file number only** for:
- ERP lookup
- subject validation
- folder-path construction

All file numbers in one email are assumed to refer to the same LC/SC family.

### Export Document Identification Rules
When multiple PDFs exist in an email, identify LC/SC and PI by combining:
- file numbers from mail body
- ERP `Amd No` to determine base LC vs amendment context
- LC clause review in the PDF to identify referenced base/amendment
- PI references in LC clauses
- ERP `PI Number` column to verify the corresponding PI(s)

### Naming Conventions / Content Markers
Assume these naming conventions are available and reliable:

#### Proforma Invoice (PI)
- Format: `PDL-YY-NNNN`
- Revision example: `PDL-26-0799 R4`

#### Utilization Declaration (UD)
- Format: `UD-LC/SC-LC/SC_ENDSEQ-BUYERNAME_EXTENSION`
- Example: `UD-LC-0090-FASHION FLOW APPARELS LTD_AMD_01 & 02`

#### LC / SC
- Standard: `LC-0090-FASHION FLOW APPARELS LTD`
- Amendment: `LC-0090-FASHION FLOW APPARELS LTD_AMD_02`
- Acknowledgment: `LC-4510-TARASIMA APPARELS LTD_ACK`

#### Import Permission (IP)
- Example: `IP-LC-0043-VINTAGE DENIM STUDIO LTD`

#### Export Form (EXP)
- Format: `[Four-digit Invoice Number]-EXP [Extension]`
- Example: `1087-EXP SIGN`

### Export Mapping to Master Workbook
Map ERP report fields to master workbook as follows:
- `Name of Buyers` <= `Buyer Name`
- `L/C Issuing Bank` <= `Notify Bank`
- `L/C & S/C No.` <= `LC No.`
- `LC Issue Date` <= `LC DT.`
- ` Amount` <= `Current LC Value`
- `Shipment Date` <= `Ship. DT.`
- `Expiry Date` <= `Expiry DT.`
- `Quantity of Fabrics (Yds/Mtr)` <= `LC Qty`
- `L/C Amnd No.` <= `Amd No`
- `L/C Amnd Date` <= `Amd DT`
- `Lien Bank` <= `Nego Bank`
- `Master L/C No.` <= `Master LC No.`
- `Master L/C Issue Dt.` <= `M.L/C Date`
- `Commercial File No.` <= `File No.`

### Quantity Format Rule
When writing `Quantity of Fabrics (Yds/Mtr)`:
- if `LC Unit` is `YDS`, preserve existing number format
- if `LC Unit` is `MTR`, apply `#,###.00 "Mtr"`

---

## Export LC/SC Amendment Rules
Model these rules explicitly in a maintainable rule engine or equivalent rule module:
- a new file is typically created for each new export LC/SC or for some amendments
- a file may contain the base LC and sometimes one or more amendments if received together
- a single file must never contain two different LCs
- some amendments create a new file only when they independently increase value/quantity with new PI coverage
- dependent amendments (terms changes, decreases, PI revision, or linked changes) may be added to an existing file instead of receiving a new file
- all extracted file numbers must be retained in the report for traceability

---

## UD / IP / EXP Workflow
The architecture must support emails with subject patterns such as:
- `UD-LC-0515-L-DESIGNER FASHION LTD_ACK`
- `UD-LC-0038-ANANTA GARMENTS LTD_AMD_05`
- `EXP-SC-010-PDL-8-ZYTA APPARELS LTD`
- `IP-LC-05NU384-DESIGNER FASHION LTD`
- `UD-LC-092387-COMTRADING APPAREL`

### General Rules
- only **new PDF attachments** are saved
- duplicates are determined by filename
- the same foldering logic based on LC/SC and buyer applies
- only the LC/SC attached in the same mail (as indicated by the mail body file number) is processed for this job even if a UD references multiple LC/SCs

### Shared Column Rule
`UD No. & IP No.` is a **single shared column** used for **UD, EXP, and IP values**, even though the header text mentions only UD and IP.

Use it as follows:
- UD numbers (local buyers)
- EXP numbers (foreign buyers or EPZ cases)
- IP numbers (EPZ cases)

For EXP and IP values written into the shared column:
- prefix with `EXP:` or `IP:`
- add a space after the prefix
- separate multiple entries by line breaks
- list **EXP first**, then **IP**

UD values do **not** require a prefix.

### Data to Extract from UD / IP / EXP PDFs
Extract for storage/reporting and date-writing logic:
- document number(s)
- LC/SC number
- quantity
- quantity unit
- document date

### UD Allocation Logic
Support this conceptual process:
1. Extract LC/SC number, value, quantity, quantity unit, UD number, and UD date from the UD PDF
2. Find candidate master workbook rows for the related LC/SC family
3. Apply the business filter rules for row eligibility
4. Build a value-combination data structure for the UD-matched values
5. Traverse rows and populate a row whenever its value is present in the remaining combination set/bag
6. Remove matched values from the combination structure until the required combination is exhausted
7. Sum the matched row quantities
8. If UD quantity is greater than or equal to the matched quantity, populate the UD number into the matched rows
9. If excess quantity remains, ignore it **only if the excess is at least 50 yards/meters**; otherwise report a discrepancy
10. If rules are not fully satisfied, do not write and produce a report

Important clarifications already known:
- combinations are **not necessarily sequential**
- choose the **first occurrence** of rows whose values are present in the required combination
- the same numeric value may appear more than once and must be handled correctly
- one email may contain several UD PDFs or UD amendments; process all of them

### IP / EXP Rules
- IP and EXP do **not** have amendments
- each IP or EXP is newly issued against a specific LC/SC or amendment
- if multiple IPs or EXPs are included in one mail, the total value and quantity of all IP/EXP documents must equal the LC total, except where business rules explicitly say otherwise
- write dates for IP and EXP with line-break-separated values aligned to their numbers

---

## Import / Back-to-Back LC Workflow
### Manual Intake Step
The operator reviews emails in Outlook inbox subfolder `temp-import`, selects fabric-related back-to-back LC emails, and moves them into `working`.

Automation begins only when the operator manually triggers the relevant CLI tool.

### Fabric Filter Rule
An import email is considered relevant if the **subject keyword indicates fabric**.

### Processing Expectations
Each mail may contain multiple import/back-to-back LC PDFs.
The system must:
- save all new files to the import folder
- store all saved file paths in job context
- iterate through all saved files
- extract data from relevant PDFs
- write validated results into the master workbook

### Required Extraction
Extract:
- import LC number
- import LC date
- import LC value
- yarn quantity from PI
- related export LC number from the relevant LC clause

### Write Targets
Populate only these master workbook columns:
- `BTB LC No.`
- `BTB LC Issue Date`
- `Amount`
- `Quantity (Kgs)`

### Matching Rule
The related export LC may exist on multiple rows due to amendments.
Filter candidate rows where:
- export LC matches
- `UP No.` is blank
- back-to-back LC field is blank

Then evaluate top-to-bottom using this **strict validation rule**:
- BTB LC value must be between **40% and 80%** of export LC value
- the **first occurrence** satisfying the rule is the row that gets populated
- one import LC maps to **exactly one row**

---

## Bangladesh Bank Dashboard Workflow
Design this as a separate manually triggered CLI tool.

### Candidate Row Filter
Filter master workbook rows where:
- `UP No.` is blank
- UD number is not blank
- `Bangladesh Bank Dashboard` is blank, or not equal to `OK`, or not equal to `OK (Kgs)`

### Data Sourcing
From the master workbook:
- `L/C & S/C No.`
- `Master L/C No.`

From `rptDateWiseLCRegister`:
- sum LC value across all amendments
- sum quantity across all amendments
- sum Net Weight across all amendments
- use shipment and expiry dates from the final rows

### Verification Logic
Use Playwright with username/password login only.

If dashboard quantity matches `LC Qty` and all other fields are consistent, write:
- `OK`

If dashboard quantity does not match `LC Qty`, but matches `Net Weight`, and all other fields are consistent, write:
- `OK (Kgs)`

If discrepancies remain:
- identify all mismatched fields
- combine them into a single descriptive string
- write that result back to `Bangladesh Bank Dashboard`

Dashboard results are **verification only** and do not populate any other workbook fields.

---

## Printing Workflow
Printing happens **automatically after successful processing**.

Print:
- **only newly saved PDFs**
- grouped by originating mail
- in the order determined by the row sequence of the mail data written into the master workbook

Rule:
- print all newly saved PDFs for one mail
- then print one blank page
- then print all newly saved PDFs for the next mail
- continue in sequence

Use **Adobe Acrobat SDK** for reliable PDF printing.

The architecture must explain:
- how row sequence is tracked
- how job-to-mail grouping is preserved
- how print batches are built
- how print failures, retries, and operator review are handled

---

## Reporting / Audit Requirements
Generate structured JSON reports containing at least:
- job identifier
- tool/workflow name
- source emails processed
- subject/body parsing results
- extracted file numbers
- saved attachments and local paths
- normalized entities (buyer, LC/SC, amendment, UD/IP/EXP, BTB LC, etc.)
- validation results
- rows targeted in the master workbook
- writes performed or blocked
- reasons for mismatch/blocking
- print sequence metadata
- timestamps
- operator-triggered execution context

---

## Architecture Deliverables Required from the Architect
Produce the response in this structure:
1. Architecture summary
2. Proposed system context and module diagram description
3. Workflow-by-workflow architecture
4. Canonical data model
5. Rule engine and validation design
6. Excel integration design
7. Document extraction design
8. Storage, audit, and reporting design
9. Deployment and operations design for Windows
10. Risks and mitigation table
11. Open questions / clarifications needed
12. Recommended phased implementation roadmap

---

## What the Architecture Must Explicitly Cover
Your architecture response must include:

### 1. Executive Overview
Why a Windows-first monolithic modular CLI architecture is appropriate for this domain.

### 2. Internal Modules
Define major modules such as:
- CLI entrypoints / command dispatcher
- Outlook mailbox adapter (COM/pywin32)
- workflow orchestrator
- local document storage manager
- ERP downloader
- PDF extraction pipeline
- OCR / table extraction pipeline
- subject/body parser
- entity normalization layer
- rule / validation engine
- matching / reconciliation engine
- Excel adapter via xlwings
- reporting / audit engine
- printing engine
- configuration / secrets management
- human-review checkpoint mechanism
- future AI extension points

### 3. Workflow Boundaries
Separate architecture for:
- export LC/SC intake
- UD/IP/EXP processing
- import/back-to-back LC processing
- Bangladesh Bank dashboard verification
- printing

Explain shared services vs workflow-specific logic.

### 4. Canonical Data Model
Define domain entities such as:
- EmailMessage
- Attachment
- FileNumber
- Buyer
- ExportLC
- SalesContract
- Amendment
- PI
- UD
- IP
- EXP
- ImportLC / BTBLC
- MasterWorkbookRowReference
- ExtractionResult
- ValidationResult
- ProcessingJob
- PrintBatch

Also explain what is held only in local files / JSON and what could later be persisted in PostgreSQL.

### 5. State Management / Idempotency
Show how the system avoids:
- duplicate processing
- duplicate saves
- duplicate writes
- duplicate printing
- conflicting reruns

### 6. Rule Engine Design
Show how business rules should be represented, versioned, tested, and maintained.
Distinguish:
- hard failures
- soft warnings
- human-review cases

### 7. Excel Write Safety
Describe the safest approach for surgical workbook updates, including:
- exclusive access assumptions
- row detection / append strategy
- skip-if-file-number-exists rule
- write batching
- compensating controls / rollback strategy
- selective number-format override
- preserving workbook fidelity exactly

### 8. PDF Extraction Strategy
Design a layered extraction approach for:
- text PDFs
- scanned PDFs
- hybrid PDFs
- clause extraction
- table extraction
- OCR fallback
- provenance and confidence scoring

### 9. Windows Deployment Model
Recommend:
- packaging
- environment management with uv
- local secrets handling
- log/report locations
- Adobe/Outlook/Excel desktop dependencies
- operator command usage patterns

### 10. Error Handling / Recovery
Describe:
- retries
- resumability
- blocked-item reporting
- operator review flow
- safe recovery after partial failure

### 11. Security / Compliance
Address:
- local document sensitivity
- credentials storage
- auditability
- least-privilege access
- local PC controls

### 12. Extensibility Roadmap
Explain how to evolve the architecture later for:
- AI-assisted extraction
- AI-based decision support
- agentic workflow orchestration
without tightly coupling phase 1 to AI.

### 13. Documentation Breakdown
Recommend separate architecture/docs for:
- architecture overview
- domain glossary and business rules
- workflow specs by CLI tool
- file/folder conventions
- data contracts
- Excel integration specification
- extraction specification
- printing specification
- deployment specification
- operations / runbook
- validation / test strategy

---

## Important Constraints** define exactly which conditions are “borderline cases” that should pause for operator approval instead of auto-blocking.

5. **Buyer-type inference for UD/IP/EXP:** the system currently should not depend on buyer-address logic, but future architecture may need it. Confirm whether this stays out of phase 1 entirely.

---

## Important Constraints
- No code
- No generic architecture; tailor it to this exact business process
- Preserve Excel fidelity exactly
- Assume Windows desktop integration is required
- Separate CLI tools, manually triggered
- Monolithic repository with modular internal design
- Log/report output is enough for phase 1
- Deterministic rule-based automation first
- AI only as a future extension point

