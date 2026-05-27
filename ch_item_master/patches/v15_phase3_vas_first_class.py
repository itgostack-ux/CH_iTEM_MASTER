"""Phase 3 - VAS as first-class citizen (reuse-first).

Backfills VAS-facing doctypes from mature CH warranty/claim structures:
- VAS Product   <- CH Warranty Plan.service_item
- VAS Plan      <- CH Warranty Plan
- VAS Claim     <- CH Warranty Claim

Idempotent: updates existing rows by source links and only inserts missing ones.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "VAS Product") or not frappe.db.exists("DocType", "VAS Plan"):
		return

	for wp in frappe.get_all(
		"CH Warranty Plan",
		fields=["name", "plan_name", "plan_type", "service_item", "brand", "price", "duration_months", "status"],
		limit=0,
	):
		product_name = f"{wp.plan_name} Product"
		product = frappe.db.get_value("VAS Product", {"source_warranty_plan": wp.name}, "name")
		if not product:
			product = frappe.db.get_value("VAS Product", {"service_item": wp.service_item}, "name")

		if not product:
			doc = frappe.get_doc(
				{
					"doctype": "VAS Product",
					"product_name": product_name,
					"service_item": wp.service_item,
					"product_type": wp.plan_type or "Value Added Service",
					"brand": wp.brand,
					"is_active": 1 if (wp.status or "") == "Active" else 0,
					"source_warranty_plan": wp.name,
				}
			)
			doc.insert(ignore_permissions=True)
			product = doc.name
		else:
			frappe.db.set_value(
				"VAS Product",
				product,
				{
					"product_type": wp.plan_type or "Value Added Service",
					"brand": wp.brand,
					"is_active": 1 if (wp.status or "") == "Active" else 0,
					"source_warranty_plan": wp.name,
				},
				update_modified=False,
			)

		plan = frappe.db.get_value("VAS Plan", {"source_warranty_plan": wp.name}, "name")
		if not plan:
			frappe.get_doc(
				{
					"doctype": "VAS Plan",
					"plan_name": wp.plan_name,
					"vas_product": product,
					"source_warranty_plan": wp.name,
					"duration_months": wp.duration_months,
					"list_price": wp.price,
					"status": "Active" if (wp.status or "") == "Active" else "Inactive",
				}
			).insert(ignore_permissions=True)
		else:
			frappe.db.set_value(
				"VAS Plan",
				plan,
				{
					"plan_name": wp.plan_name,
					"vas_product": product,
					"duration_months": wp.duration_months,
					"list_price": wp.price,
					"status": "Active" if (wp.status or "") == "Active" else "Inactive",
				},
				update_modified=False,
			)

	if not frappe.db.exists("DocType", "VAS Claim"):
		return

	for wc in frappe.get_all(
		"CH Warranty Claim",
		fields=["name", "claim_date", "customer", "sold_plan", "claim_status", "approved_amount", "gogizmo_invoice"],
		limit=0,
	):
		if frappe.db.exists("VAS Claim", {"source_warranty_claim": wc.name}):
			continue
		status_raw = (wc.claim_status or "").strip().lower()
		if status_raw in {"closed", "cancelled", "delivered", "repaired under vas plan", "repaired under anniversary warranty", "repaired under repair warranty", "repaired paid", "goodwill repair"}:
			mapped_status = "Closed"
		elif status_raw in {"rejected claim", "rejected", "not repairable at intake", "not repairable after diagnosis"}:
			mapped_status = "Rejected"
		elif status_raw in {"approved", "approved by manager", "approval pending"}:
			mapped_status = "Approved"
		else:
			mapped_status = "Open"
		frappe.get_doc(
			{
				"doctype": "VAS Claim",
				"claim_date": wc.claim_date,
				"customer": wc.customer,
				"sales_invoice": wc.gogizmo_invoice,
				"sold_plan": wc.sold_plan,
				"claim_status": mapped_status,
				"approved_amount": wc.approved_amount,
				"source_warranty_claim": wc.name,
			}
		).insert(ignore_permissions=True)
