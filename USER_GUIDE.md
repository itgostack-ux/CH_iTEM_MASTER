# CH Item Master User Guide

This guide covers the latest CH Item Master process for:
- Creating items on the go
- Uploading data in bulk
- Applying price updates safely

It is written for ERPNext v16 deployments using the ch_item_master app.

## Legacy Guide Note

There was an older document named MOBILE_API_ACCESS_GUIDE.md in historical commits.
That guide documented mobile API endpoints and a tester page that were removed from the app.

What this means now:
- The old mobile API routes from that guide are not active in the current codebase.
- The old Mobile API Tester page is not present in current builds.
- This document (USER_GUIDE.md) is now the primary and current user guide.

For current integrations and bulk operations, use the flows documented in this guide:
- ERPNext Data Import
- CH Master Import API / CSV API
- CH Price Upload Batch

## 1. Who Can Do What

Common role gates in the latest flow:
- Item creation and master upload: `System Manager`, `CH Master Manager`
- Approval actions on Item: `CH Master Approver` (or valid delegate), `System Manager`
- Vendor Info updates: `CH Vendor Manager`
- Sensitive field visibility/edit (permlevel 1): price/compliance roles configured by CH RBAC

## 2. Create Items On The Go

There are two supported creation patterns:
- Quick Entry (fastest)
- Full Item Form (best for detailed setup)

### 2.1 Quick Entry Flow (Recommended for speed)

Path:
- Item list -> New

What is customized:
- The default ERPNext item quick entry is replaced by a CH model-driven quick entry.
- You select `CH Model`; category/sub-category logic is applied automatically.

Steps:
1. Click New Item.
2. Select `CH Model`.
3. Review model preview shown in the quick entry dialog.
4. Confirm `Default Unit of Measure` (defaults to `Nos`).
5. Set `Maintain Stock` as needed.
6. Save.

What gets auto-populated:
- `ch_category`, `ch_sub_category`
- `item_group`
- `gst_hsn_code` (if configured on sub-category)
- Variant template setup (`has_variants`, attributes) when model/spec config requires it
- Property specs and model features from model mapping

### 2.2 Full Item Form Flow

Use this when you need additional fields before first save.

Steps:
1. Open Item -> New.
2. Select `CH Model` first (primary driver).
3. Confirm hierarchy values and defaults.
4. Fill additional fields (stock/accounting/sales/purchase).
5. Save.

Latest UX features on Item form:
- Status indicators on dashboard for:
  - Lifecycle
  - PLM
  - Approval status
  - Completeness percentage
- Quick action buttons under `CH Actions`:
  - `Submit for Review`
  - `Approve` / `Reject` (role-based)
  - `PLM -> Next State`
  - `Version History`
- Version history timeline dialog with change snapshots

### 2.3 Variant Items (Template -> Variants)

When model setup supports variants:
1. Save template Item.
2. Use `Create -> Multiple Variants`.
3. CH customization filters values to model-allowed attribute values only.

Result:
- Faster and cleaner variant generation
- Prevents invalid attribute combinations from global value lists

## 3. Lifecycle, Approval, and PLM During Creation

### 3.1 Lifecycle and Approval Gate

Key rules:
- Items are generally created in `Draft` lifecycle unless privileged flow is used.
- Lifecycle `Active` requires approval status `Approved`.
- Submitter/approver segregation of duties is enforced.

Approval actions:
- `Submit for Review` -> status moves to `Submitted for Review`
- `Approve` -> status moves to `Approved`
- `Reject` -> status moves to `Rejected`

### 3.2 PLM State Machine

Supported states:
- `NPI`
- `Under Review`
- `Sample Testing`
- `Approved`
- `Active Production`
- `End of Life`
- `Discontinued`

Use CH quick actions to move to allowed next states.

## 4. Upload Data (Bulk)

There are three main upload channels:
- ERPNext Data Import tool (generic)
- CH Master Import API / CSV API (structured masters)
- CH Price Upload Batch (maker-checker price governance)

### 4.1 ERPNext Data Import (DocType-wise)

Use for:
- Standard bulk import into supported doctypes (Item, Brand, Model-related entities, etc.)

Recommended steps:
1. Open `Data Import`.
2. Download template for target DocType.
3. Fill and validate data.
4. Upload and submit import.
5. Confirm `Success` status.

What CH does after successful import:
- `on_data_import_complete` runs post-submit for relevant doctypes.
- Denormalized ID cascade is executed automatically.
- Failures are logged to Error Log (no silent DB corruption path).

Tip:
- Import in dependency order (Category -> Sub Category -> Model -> Item) for smoother validation.

### 4.2 CH Masters Import API (JSON)

Use for:
- Structured hierarchical master data loads from external systems

Endpoint behavior highlights:
- Supports `dry_run=1` for validation-only mode
- Supports idempotency key replay protection
- Returns structured summary and errors
- Role-restricted to `System Manager` and `CH Master Manager`

Best practice:
1. Run dry run first.
2. Fix payload issues.
3. Execute final import with idempotency key.
4. Archive response summary for audit trail.

### 4.3 CH Masters CSV Upload API

Use for:
- CSV-based master import when API clients cannot send JSON payloads

Requirements:
- File must be attached as `file` in request
- Expected CSV columns are fixed by API contract

Before upload:
- Keep header names and order aligned to API expectations.
- Avoid merged cells, formulas, and non-UTF8 encodings.

### 4.4 Price Upload Batch (Maker-Checker)

Use for:
- Selling price
- Buyback price
- Commercial tags

Lifecycle:
- `Draft` -> `Pending Approval` -> `Applying` -> `Applied` / `Partially Applied`
- Rejection path: `Pending Approval` -> `Rejected`
- Revision path: `Rejected` or `Partially Applied` -> `Draft`

Steps:
1. Create `CH Price Upload Batch`.
2. Add rows in items table (change type + target fields + new values).
3. Submit for approval.
4. Checker approves and applies.
5. Review applied/skipped/error counts.

Guardrails in latest implementation:
- Pre-flight hierarchy validation (`MRP >= MOP >= Selling Price`)
- Negative buyback value blocking
- Grouped apply logic for stable upserts
- Row-level status + error messages
- Change log writing after apply

## 5. Upload Validation Checklist (Production)

Before running any large upload:
1. Verify mandatory references exist (company, item group, category tree, model links).
2. Confirm role access for import/approval actors.
3. Run dry run whenever available.
4. Validate sample batch in staging first.
5. Keep source file immutable after approval sign-off.

After upload:
1. Check import/batch status (`Success`, `Applied`, `Partially Applied`).
2. Review Error Log for exceptions.
3. Verify spot records in list + form.
4. For price updates, verify downstream price visibility in sales flows.

## 6. Common Errors and Fixes

`MandatoryError: ch_model`
- Cause: importing/creating CH item without model context where business rules expect model-driven setup.
- Fix: provide valid `CH Model`, or use the right item nature path for non-model items.

`Invalid Price Hierarchy`
- Cause: `MRP`, `MOP`, `Selling Price` relationship violated.
- Fix: ensure `MRP >= MOP >= Selling Price` in upload rows.

`PermissionError on approve/reject`
- Cause: user lacks approver role or valid delegation.
- Fix: assign `CH Master Approver` or configure active delegation with valid dates.

Partial apply in price batch
- Cause: some rows failed while others succeeded.
- Fix: review row-level errors, revise batch, resubmit.

## 7. Recommended Operating Procedure

For day-to-day teams:
1. Use Quick Entry for new model-driven items.
2. Use full form only when additional setup is required before first save.
3. Use Data Import for generic master/doc uploads.
4. Use CH Import APIs for controlled system-to-system loads.
5. Use Price Upload Batch for governed commercial changes.
6. Keep approvals and PLM transitions inside CH actions for full auditability.

## 8. Reference

Primary docs:
- App overview: `README.md`
- This guide: `USER_GUIDE.md`
