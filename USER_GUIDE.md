# End User Runbook: Complete Start-to-End Operations

This is a practical runbook for business users. Keep this document open while working.

Goal of this guide:
- Help teams complete the full process from setup to daily execution.
- Cover all custom apps in this workspace.
- Use only verified, implemented features from code.

Covered apps:
1. ch_item_master
2. ch_erp15
3. ch_pos
4. ch_mg_reports
5. gofix
6. buyback
7. ch_payments

## 1. How To Use This Guide

Use this document in order.

1. Complete Section 2 once during implementation or rollout.
2. Complete Section 3 at the start of each day.
3. Run operational flows in Section 4 during business hours.
4. Run controls and closures in Section 5 before day close.
5. Run reconciliation in Section 6 at day close.
6. Use Section 7 for weekly governance checks.

If a step fails, go to Section 8 for troubleshooting.

## 2. One-Time Setup (Implementation Phase)

Complete this section once per company or rollout.

## 2.1 App Access Check

1. Open app launcher.
2. Confirm these app entries are visible:
- CH Item Master
- ERP
- CH POS
- CEO Analytics
- GoFix
- BuyBack
- Bank Payments
3. If an app is missing, inform your system admin before proceeding.

Expected result:
- All required app tiles are visible and open.

## 2.2 Workspace Access Check

Open each workspace and confirm it loads without permission errors.

ch_item_master:
- CH Core
- CH Item Master
- CH Customer Master
- CH Vendor Master
- CH VAS
- Scheme Management

ch_erp15:
- ERP
- CH Operations

ch_pos:
- POS

ch_mg_reports:
- CEO Analytics

gofix:
- GoFix
- Services
- Masters

buyback:
- BuyBack

ch_payments:
- Bank Payments

Expected result:
- All workspaces open successfully.

## 2.3 Master Data Foundation (Mandatory Order)

Run in this order to avoid dependency issues.

1. Create location and organization masters:
- Company
- Warehouse and store-related masters
- CH City, CH Store Zone, CH Store (CH Core)

2. Create item hierarchy and model masters:
- CH Category
- CH Sub Category
- CH Model

3. Create customer and vendor base masters:
- Customer
- Supplier

4. Create commercial and control masters:
- CH Commercial Policy
- CH Discount Reason
- CH Exception Type
- CH Payment Method

5. Create warranty/VAS masters:
- CH Warranty Plan
- CH VAS Settings

6. Create scheme masters:
- Supplier Scheme Circular
- Scheme Product Map
- Scheme Rule Detail

Expected result:
- All masters exist and are searchable from their workspaces.

## 2.4 Integration and Control Setup

1. In Bank Payments workspace, configure:
- Bank Integration Settings
- Bank Integration Profile
- Bank Beneficiary

2. In BuyBack workspace, configure:
- Buyback Settings
- Buyback SLA Settings
- Buyback Price Master
- Grade Master

3. In GoFix workspaces, configure:
- GoFix SLA Rule
- Issue Category
- Walkin Source
- Withdrawal Reason

4. In POS workspace, configure:
- POS Profile Extension
- CH Sale Type
- POS Executive
- POS Incentive Slab

Expected result:
- No pending configuration items remain in setup checklists.

## 3. Daily Start Checklist (Operations Opening)

Complete this at the beginning of every business day.

1. Open CH Operations workspace.
2. Confirm CH Accounting Day Lock status for current date.
3. Open POS workspace and verify session readiness.
4. Open BuyBack workspace and review pending cards:
- Pending inspections
- Awaiting approvals
- Pending payments
5. Open Services workspace and review open Service Requests.
6. Open Bank Payments workspace and review pending requests/batches.
7. Open CEO Analytics and confirm no critical alert backlog.

Expected result:
- Team starts with clean queue visibility.

## 4. End-to-End Operational Flows

This section is the core execution path for users.

## 4.1 Product and Pricing Flow (ch_item_master)

### A. Create and release a new product

1. Go to CH Item Master workspace.
2. Open Item list and click New.
3. Select CH Model.
4. Save item.
5. Open full item form.
6. Verify category, sub-category, model-derived fields.
7. Fill remaining business fields.
8. Save.
9. Submit for approval using item actions.
10. Approver approves item.
11. Move lifecycle and PLM state as per business stage.

Checkpoint:
- Item is approved and available for operations.

### B. Create pricing and offers

1. Create CH Item Price for the item.
2. Create CH Item Offer if promotion is required.
3. Add CH Item Commercial Tag if needed.
4. Validate in Price Change Log after updates.

Checkpoint:
- Item has active pricing data.

### C. Bulk price update using batch

1. Open CH Price Upload Batch.
2. Create a new batch.
3. Add CH Price Upload Item rows.
4. Submit for approval.
5. Approver approves and applies.
6. Review status and row-level results.

Checkpoint:
- Batch status is Applied or reviewed if Partially Applied.

## 4.2 Vendor, Sourcing, and Scheme Flow (ch_item_master)

### A. Vendor sourcing setup

1. Open CH Vendor Master workspace.
2. Create or verify Supplier.
3. Create CH Vendor Info Record for item-supplier pair.
4. Add quantity-based rows in CH Vendor Price Break.
5. Add contract rows in CH Vendor Contract if applicable.
6. Record vendor performance in CH Vendor Performance.

Checkpoint:
- Sourcing records are complete and active.

### B. Supplier scheme execution

1. Open Scheme Management workspace.
2. Create or update Supplier Scheme Circular.
3. Upload scheme documents in Scheme Document Upload.
4. Update Scheme Product Map and rule details.
5. During billing cycle, track Scheme Achievement Ledger.
6. Process Scheme Claim Summary and Scheme Settlement.
7. Review CH Scheme Receivable and aging report.

Checkpoint:
- Scheme claims and receivables are traceable and reconciled.

## 4.3 Customer, Warranty, and Voucher Flow (ch_item_master)

1. Open CH Customer Master workspace.
2. Create or update Customer.
3. Register CH Customer Device.
4. Open CH VAS workspace.
5. Issue CH Sold Plan where plan is sold.
6. Process CH Warranty Claim where claim is raised.
7. Track CH VAS Ledger entries.
8. Issue and track CH Voucher where applicable.

Checkpoint:
- Customer, device, warranty, and voucher lifecycle is complete.

## 4.4 Procurement and Store Operations Flow (ch_erp15)

### A. Demand to procurement

1. Open ERP workspace.
2. Create Material Request.
3. Submit as per approval process.
4. Convert to Purchase Order.
5. On goods arrival, create Purchase Receipt.
6. Create Purchase Invoice.
7. Complete Payment Entry.

Checkpoint:
- Procurement cycle is complete with financial posting.

### B. Inter-store transfer and logistics

1. Create Stock Entry for transfer.
2. Validate transfer quantities.
3. Track CH Transfer Manifest.
4. Track courier status updates.
5. Confirm receipt at destination process.

Checkpoint:
- Transfer executed with manifest visibility.

### C. Store controls

1. Use CH Operations workspace daily.
2. Record CH Stock Audit Session.
3. Record CH Damage Report.
4. Process CH Supplier Return where needed.
5. Review key reports:
- Received Not Billed
- Accounts Invoice Settlement
- Courier Billing Analytics

Checkpoint:
- Store and finance control records are current.

## 4.5 POS Sales and Settlement Flow (ch_pos)

### A. POS shift execution

1. Open POS workspace.
2. Start CH POS Session.
3. Open POS screen.
4. Create Sales Invoice through POS.
5. Submit invoice.
6. Repeat through shift.

Checkpoint:
- Sales invoices post successfully without policy violations.

### B. Shift close and reconciliation

1. Create POS Closing Entry.
2. Complete CH POS Settlement.
3. Capture POS EDC Settlement if card machine is used.
4. Record CH Cash Drop if applicable.
5. Review reconciliation reports:
- Session vs POS Invoice
- Session vs Payment Reconciliation
- Cash Variance

Checkpoint:
- Session is closed and reconciled.

### C. Assisted selling and intake flows

1. Use POS Guided Session for assisted recommendations.
2. Use POS Comparison Request for compare-led sale.
3. Use POS Repair Intake for repair handoff.
4. Use Buyback Assessment intake where needed.

Checkpoint:
- Assisted flows are fully captured and traceable.

## 4.6 Service Flow (gofix)

1. Open Services workspace.
2. Create Service Request.
3. Create Job Assignment.
4. Capture Spare Parts Usage during repair.
5. Create Service Sales Order.
6. Progress service workflow states.
7. Submit Sales Invoice when billable.
8. Submit Delivery Note when handover is done.

Checkpoint:
- Service request reaches closed state with financial linkage.

Daily SLA check:
1. Review SLA-related queues and reports.
2. Prioritize breached or near-breach jobs.

## 4.7 Buyback Flow (buyback)

1. Open BuyBack workspace.
2. Create Buyback Assessment.
3. Complete Buyback Inspection.
4. Generate Buyback Order.
5. Run approval and customer confirmation steps.
6. Run OTP verification steps.
7. Mark payment readiness.
8. Complete payout and closure.
9. If exchange case, process Buyback Exchange Order.

Checkpoint:
- Buyback case is closed with payout and audit trail.

## 4.8 Bank Payment Flow (ch_payments)

1. Open Bank Payments workspace.
2. Create Bank Payment Request.
3. Add linked invoice details.
4. Add to Bank Payment Batch if bulk release is needed.
5. Submit request or batch.
6. Track callback and API log status.
7. Reconcile using reports:
- Bank Payment Summary
- Payment Reconciliation Status

Checkpoint:
- Payment status is final and reconciled.

## 4.9 Executive Monitoring Flow (ch_mg_reports)

1. Open CEO Analytics workspace.
2. Open CEO Command Center.
3. Review CH CEO Alert list.
4. Confirm KPI targets in Business KPI Target.
5. Review dashboard settings if display scope is incorrect.

Checkpoint:
- Leadership dashboard and alerting are current.

## 5. Day-End Closure Checklist

Run this sequence before business day close.

1. POS:
- Confirm all active sessions are closed.
- Confirm settlement and cash drop records are completed.

2. Service:
- Confirm all completed jobs are invoice-linked.
- Confirm pending estimates are reviewed.

3. Buyback:
- Confirm pending OTP, payment, settlement queues are reviewed.

4. ERP operations:
- Confirm transfer manifests and store receipts are updated.
- Confirm accounting day lock status as per policy.

5. Payments:
- Confirm pending bank requests are either processed or tagged for next day.

6. Analytics:
- Confirm unresolved critical alerts are assigned.

Expected result:
- No critical pending records without owner assignment.

## 6. Reconciliation Checklist (Finance and Audit)

Run at day end or next morning.

1. ch_erp15:
- Received Not Billed
- Accounts Invoice Settlement

2. ch_pos:
- Session vs POS Invoice
- Session vs Payment Reconciliation
- Cash Variance Report

3. buyback:
- Pending Payments
- Pending Settlement
- Settlement Register

4. ch_payments:
- Bank Payment Summary
- Payment Reconciliation Status

5. ch_item_master:
- CH Scheme Receivable Aging
- Claim vs Received

Expected result:
- Exceptions identified, assigned, and tracked.

## 7. Weekly Governance Checklist

1. Review stale or inactive masters.
2. Review price governance logs and overrides.
3. Review scheme pending compliance reports.
4. Review SLA breach trends (service and buyback).
5. Review POS margin leakage and tax summary trends.
6. Review executive alerts trend and root causes.

## 8. Troubleshooting and Recovery Steps

## 8.1 Permission errors

1. Capture screenshot with user and doctype.
2. Verify user has correct role profile.
3. Re-test from workspace link, then direct list view.
4. Escalate to admin if permission query restrictions apply.

## 8.2 Record not moving to next stage

1. Open timeline and latest comments.
2. Check whether required fields are missing.
3. Check linked child records are complete.
4. Re-run action from the same document state.

## 8.3 Scheduler-dependent updates delayed

1. Confirm expected job window passed.
2. Check related queue/report after scheduler window.
3. If still not updated, assign to support with document IDs.

## 8.4 Reconciliation mismatch

1. Identify source document list from report.
2. Open each source document and validate statuses.
3. Check reversals/cancellations in same period.
4. Re-run report with exact date filter.
5. Escalate with report export and document IDs.

## 9. Role-Wise Runbooks

Use this section when each team member follows only their role-specific checklist.

## 9.1 Store Executive Runbook

### Start of shift

1. Open POS workspace.
2. Start CH POS Session.
3. Open CH POS app.
4. Verify device and payment machine are mapped.
5. Check open queue for repair intake and buyback intake.

### During shift

1. Create Sales Invoice from POS for each walk-in sale.
2. Capture mandatory sale type and payment details.
3. For guided selling, run POS Guided Session before billing.
4. For comparison-led sale, create POS Comparison Request.
5. For repair customer, create POS Repair Intake.
6. For exchange customer, create Buyback Assessment intake.

### End of shift

1. Complete POS Closing Entry.
2. Complete CH POS Settlement.
3. Record CH Cash Drop and EDC settlement if applicable.
4. Hand over mismatch list to Store Manager.

Outputs handed off:
- POS closing and settlement records.
- Pending customer cases (repair/buyback).

## 9.2 Store Manager Runbook

### Start of day

1. Open CH Operations workspace.
2. Verify CH Accounting Day Lock for current date.
3. Open pending dashboards in BuyBack and Services.
4. Assign high-priority queues to team.

### Operational oversight

1. Review CH Stock Audit Session and CH Damage Report queue.
2. Review transfer and manifest statuses.
3. Review open POS sessions and pending settlement cases.
4. Review pending buyback approvals and customer confirmations.
5. Review open service jobs and SLA risk cases.

### Day closure

1. Confirm all cashiers closed sessions.
2. Confirm unresolved mismatches are tagged with owner.
3. Confirm transfer and receipt exceptions are assigned.
4. Share closure summary to Finance and Procurement.

Outputs handed off:
- Store closure summary.
- Exception ownership matrix.

## 9.3 Finance Runbook

### Start of day

1. Open CH Operations and Bank Payments workspaces.
2. Review pending settlement and pending payment queues.
3. Review prior-day reconciliation exceptions.

### Payment processing

1. Create and review Bank Payment Request entries.
2. Group into Bank Payment Batch when required.
3. Submit batch and monitor callback and API logs.
4. Resolve failed requests using error code mapping.

### Reconciliation

1. Run Accounts Invoice Settlement and Received Not Billed.
2. Run Session vs Payment Reconciliation and Cash Variance.
3. Run Buyback pending and settlement reports.
4. Run Bank Payment Summary and Payment Reconciliation Status.
5. Post final exception list to Store Manager and Procurement.

Outputs handed off:
- Reconciliation report pack.
- Pending exception tracker with owners.

## 9.4 Service Desk Runbook

### Intake and triage

1. Open Services workspace.
2. Create Service Request for each incoming case.
3. Validate customer and device details.
4. Assign technician through Job Assignment.

### Job execution tracking

1. Track work progress and parts usage in Spare Parts Usage.
2. Create Service Sales Order when estimate is approved.
3. Raise invoice and delivery note at completion.
4. Update customer-facing status for each milestone.

### SLA control

1. Review SLA breach report and open alerts.
2. Prioritize near-breach requests first.
3. Escalate blocked requests to Store Manager.

Outputs handed off:
- Completed jobs ready for billing closure.
- Escalated SLA exception list.

## 9.5 Buyback Inspector Runbook

### Assessment stage

1. Open BuyBack workspace.
2. Pick assigned Buyback Assessment records.
3. Complete question bank responses and diagnostics.

### Inspection stage

1. Create or open Buyback Inspection.
2. Complete condition checks and grading evidence.
3. Confirm pricing impact fields and deductions.
4. Submit inspection to order generation queue.

### Handover stage

1. Ensure Buyback Order is created with complete details.
2. Confirm OTP and approval readiness notes are complete.
3. Hand over payment-ready cases to Finance queue.

Outputs handed off:
- Inspection-complete cases.
- Payment-ready buyback case list.

## 9.6 Procurement Runbook

### Demand processing

1. Open ERP workspace.
2. Review submitted Material Requests.
3. Validate quantity and urgency.
4. Convert approved requests to Purchase Orders.

### Inward and invoice processing

1. Record Purchase Receipt on goods arrival.
2. Validate inward quantities and quality notes.
3. Record Purchase Invoice and link to receipts.
4. Hand over payment due list to Finance.

### Sourcing and vendor control

1. Maintain CH Vendor Info Record for active items.
2. Update CH Vendor Price Break where needed.
3. Maintain CH Vendor Contract entries for negotiated rates.
4. Record vendor performance evaluations periodically.
5. Review scheme receivable and vendor claim data.

Outputs handed off:
- PO to PR to PI completion status.
- Vendor performance and sourcing update log.

## 9.7 CEO Runbook

### Daily review

1. Open CEO Analytics workspace.
2. Open CEO Command Center.
3. Review CH CEO Alerts by severity.
4. Review KPI target vs actual drift.

### Exception governance

1. Review unresolved critical exceptions from Finance.
2. Review SLA breach trend from Service and Buyback.
3. Review margin leakage and settlement anomalies from POS.
4. Assign cross-functional action owners.

### Weekly governance review

1. Review trend of alert categories.
2. Review major aging buckets in receivables and settlements.
3. Review procurement exceptions and vendor risk movement.
4. Publish top priorities for next cycle.

Outputs handed off:
- Executive action list.
- Priority and accountability matrix.

## 9.8 RACI-Style Handoff Matrix

RACI legend:
- R = Responsible (executes the task)
- A = Accountable (owns final outcome)
- C = Consulted (provides input/approval)
- I = Informed (kept updated)

Roles in this matrix:
- Store Executive
- Store Manager
- Finance
- Service Desk
- Buyback Inspector
- Procurement
- CEO

| Process / Handoff Point | Store Executive | Store Manager | Finance | Service Desk | Buyback Inspector | Procurement | CEO |
|---|---|---|---|---|---|---|---|
| Store opening checks and queue readiness | R | A | I | I | I | I | I |
| POS sale creation and billing discipline | R | A | I | I | I | I | I |
| POS session closure and cash/EDC settlement | R | A | C | I | I | I | I |
| Cash variance and session mismatch resolution | C | R | A | I | I | I | I |
| Material Request to Purchase Order conversion | I | C | I | I | I | R | A |
| Purchase Receipt and Purchase Invoice completion | I | I | C | I | I | R | A |
| Payment release through Bank Payments | I | I | R/A | I | I | C | I |
| Service request intake and assignment | I | C | I | R/A | I | I | I |
| Service job completion to invoice handoff | I | C | C | R/A | I | I | I |
| Buyback assessment and inspection completion | I | C | I | I | R/A | I | I |
| Buyback payment-ready handoff to finance | I | C | A | I | R | I | I |
| Vendor sourcing and contract maintenance | I | I | C | I | I | R/A | I |
| Vendor performance scoring review | I | I | C | I | I | R/A | I |
| Scheme claim and receivable monitoring | I | C | A | I | I | R | I |
| Daily reconciliation pack closure | I | I | R/A | I | I | C | I |
| Critical exception ownership assignment | I | R | C | C | C | C | A |
| Weekly KPI and risk governance review | I | C | C | C | C | C | R/A |

Mandatory handoff rules:
1. No payment release without completed source documents (PR/PI, buyback readiness, or approved payout basis).
2. No day close without POS session closure and mismatch ownership.
3. No unresolved critical exception may remain unassigned at end of day.
4. CEO review consumes only owner-assigned exception queues, not unowned backlog.

## 10. Complete Functional Inventory (Reference)

This inventory is included so users and auditors can confirm scope.

## 10.1 ch_item_master

Modules:
- CH Item Master
- CH Customer Master
- CH Vendor Master
- CH Core
- Supplier Scheme

Key pages:
- campaign_hub
- ch_customer_dashboard
- ch_item_master_dashboard
- ch_ready_reckoner
- imei_tracker
- location_hierarchy
- vas_hub

## 10.2 ch_erp15

Modules:
- Ch Erp15

Key pages:
- approval_matrix
- delivery_app
- distribution_hub
- hub_portal
- logistics_hub
- mr_cockpit
- mr_tracker
- purchase_hub
- sales_hub
- scheme_hub
- stock_hub

## 10.3 ch_pos

Modules:
- POS Core
- POS Kiosk
- POS AI
- POS Repair

Key pages:
- ch_pos_app
- store_hub

## 10.4 ch_mg_reports

Modules:
- CEO Analytics

Key pages:
- ceo_command_center

## 10.5 gofix

Modules:
- GoFix
- GoFix Services

Key pages:
- gofix_ops_hub
- job_tracker
- quick_intake
- service_hub
- store_queue

## 10.6 buyback

Modules:
- BuyBack

Key pages:
- buyback_hub
- category_manager_dashboard
- compliance_dashboard
- finance_dashboard
- operations_dashboard
- store_manager_dashboard

## 10.7 ch_payments

Modules:
- CH Payments
- Bank Payments

## 11. Source-of-Truth Files Used For This Guide

This guide is based on actual implementation wiring from:
- hooks.py in each custom app
- workspace JSON files
- modules.txt
- doctype, report, and page folders

This is an implementation-based guide, not a generic ERP template.
