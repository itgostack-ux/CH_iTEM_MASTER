"""Item Sales Readiness — SAP/Oracle/Dynamics-style checklist.

Surfaces every gate that must be cleared before an Item becomes selectable
in Sales Invoice / POS / Sales Order, per company.

Returns a structured list of checks so the UI can show "Sellable in <Co>" vs
"Not Sellable — N issues" with a one-click deep link to fix each gap.

This is read-only — it never mutates the item or skips PLM. Following the
SAP S/4HANA "Material Status + view extension" pattern: transparency, not
bypass.
"""
from __future__ import annotations

import frappe
from frappe import _


def _company(company: str | None) -> str | None:
	if company:
		return company
	return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)


@frappe.whitelist()
def get_item_readiness(item_code: str, company: str | None = None) -> dict:
	"""Return a structured readiness report for the given item + company.

	Returns:
	    {
	      "item_code": str,
	      "company": str,
	      "is_sellable": bool,
	      "blockers": int,
	      "warnings": int,
	      "checks": [
	        {
	          "key": "is_sales_item",
	          "label": "Marked as Sales Item",
	          "passed": bool,
	          "severity": "blocker" | "warning" | "info",
	          "message": str,
	          "fix": {"type": "field"|"child"|"new_doc"|"action",
	                  "doctype": str, "name": str, "field": str, ...}
	        },
	        ...
	      ]
	    }
	"""
	if not frappe.db.exists("Item", item_code):
		frappe.throw(_("Item {0} does not exist.").format(item_code))

	company = _company(company)
	item = frappe.get_doc("Item", item_code)
	checks: list[dict] = []

	# ── 1. Disabled ─────────────────────────────────────────────────
	checks.append({
		"key": "not_disabled",
		"label": _("Item is enabled"),
		"passed": not bool(item.disabled),
		"severity": "blocker",
		"message": _("Item is disabled.") if item.disabled else _("Enabled."),
		"fix": {"type": "field", "doctype": "Item", "name": item.name,
		        "field": "disabled", "value": 0},
	})

	# ── 2. is_sales_item ────────────────────────────────────────────
	checks.append({
		"key": "is_sales_item",
		"label": _("Marked as Sales Item"),
		"passed": bool(item.is_sales_item),
		"severity": "blocker",
		"message": _("'Allow Sales' must be checked.") if not item.is_sales_item
		           else _("Sellable flag is on."),
		"fix": {"type": "field", "doctype": "Item", "name": item.name,
		        "field": "is_sales_item", "value": 1},
	})

	# ── 3. Has Variants (template can't be sold) ────────────────────
	if item.has_variants:
		checks.append({
			"key": "has_variants",
			"label": _("Template item — sell variants instead"),
			"passed": False,
			"severity": "blocker",
			"message": _("This is a template item. Create and sell its variants instead."),
			"fix": {"type": "info"},
		})

	# ── 4. HSN code (India compliance) ──────────────────────────────
	hsn = (item.gst_hsn_code or "").strip()
	checks.append({
		"key": "gst_hsn_code",
		"label": _("HSN / SAC code set"),
		"passed": bool(hsn),
		"severity": "blocker",
		"message": _("HSN/SAC code missing — GST tax calculation will fail.")
		           if not hsn else _("HSN: {0}").format(hsn),
		"fix": {"type": "field", "doctype": "Item", "name": item.name,
		        "field": "gst_hsn_code"},
	})

	# ── 5. Item Default for the active company ──────────────────────
	# This is the #1 cause of "can't add to Sales Invoice"
	defaults = frappe.get_all(
		"Item Default",
		filters={"parent": item.name, "company": company},
		fields=["name", "default_warehouse", "income_account", "expense_account",
		        "selling_cost_center"],
		limit=1,
	)
	has_default = bool(defaults)
	checks.append({
		"key": "item_default",
		"label": _("Item Default exists for {0}").format(company or "—"),
		"passed": has_default,
		"severity": "blocker",
		"message": (_("No 'Item Default' row for company {0}. Sales Invoice will reject "
		              "the item.").format(company)) if not has_default
		           else _("Default warehouse: {0}").format(defaults[0].default_warehouse or "—"),
		"fix": {"type": "child", "doctype": "Item", "name": item.name,
		        "field": "item_defaults", "company": company},
	})

	# ── 6. Selling Item Price ───────────────────────────────────────
	price = frappe.db.get_value(
		"Item Price",
		{"item_code": item.name, "selling": 1},
		["name", "price_list", "price_list_rate", "currency"],
		as_dict=True,
	)
	checks.append({
		"key": "item_price",
		"label": _("Selling Item Price set"),
		"passed": bool(price and price.price_list_rate),
		"severity": "blocker" if not price else "warning",
		"message": (_("No Selling Item Price — POS will show ₹0 and require manual rate.")
		            if not price else
		            _("{0} {1} on {2}").format(
		                price.currency or "", price.price_list_rate, price.price_list)),
		"fix": {"type": "new_doc", "doctype": "Item Price",
		        "defaults": {"item_code": item.name, "selling": 1, "price_list": ""}},
	})

	# ── 7. Stock UOM ────────────────────────────────────────────────
	checks.append({
		"key": "stock_uom",
		"label": _("Stock UOM set"),
		"passed": bool(item.stock_uom),
		"severity": "blocker",
		"message": _("Stock UOM missing.") if not item.stock_uom
		           else _("UOM: {0}").format(item.stock_uom),
		"fix": {"type": "field", "doctype": "Item", "name": item.name,
		        "field": "stock_uom"},
	})

	# ── 8. PLM Approval Status ──────────────────────────────────────
	approval = (item.get("ch_approval_status") or "").strip() or "Draft"
	approval_passed = approval == "Approved"
	approval_action = None
	if approval in ("Draft", "Rejected"):
		approval_action = {"type": "action",
		                   "method": "ch_item_master.ch_item_master.tier_c.submit_for_approval",
		                   "label": _("Submit for Approval"),
		                   "args": {"item_code": item.name}}
	elif approval == "Submitted for Review":
		approval_action = {"type": "info",
		                   "label": _("Awaiting CH Master Approver — different user must approve (SoD).")}
	checks.append({
		"key": "ch_approval_status",
		"label": _("PLM Approval"),
		"passed": approval_passed,
		"severity": "blocker",
		"message": _("Approval status: {0}").format(approval)
		           if not approval_passed else _("Approved."),
		"fix": approval_action or {"type": "info"},
	})

	# ── 9. Lifecycle Status = Active ────────────────────────────────
	lifecycle = (item.get("ch_lifecycle_status") or "").strip() or "Draft"
	lifecycle_passed = lifecycle == "Active"
	lifecycle_action = None
	if not lifecycle_passed and approval_passed:
		# Approval passed but lifecycle still not Active → user just needs to flip it
		lifecycle_action = {"type": "field", "doctype": "Item", "name": item.name,
		                    "field": "ch_lifecycle_status", "value": "Active"}
	elif lifecycle in ("End of Life", "Discontinued"):
		lifecycle_action = {"type": "info",
		                    "label": _("Item is past production. Will only show in POS if stock exists.")}
	checks.append({
		"key": "ch_lifecycle_status",
		"label": _("Lifecycle status is Active"),
		"passed": lifecycle_passed,
		"severity": "blocker" if not lifecycle_passed else "info",
		"message": _("Lifecycle: {0} — POS / item search hides non-Active items.").format(lifecycle)
		           if not lifecycle_passed else _("Active."),
		"fix": lifecycle_action or {"type": "info"},
	})

	# ── 10. Stock availability (informational only) ─────────────────
	if item.is_stock_item and not item.has_variants:
		try:
			from erpnext.stock.utils import get_stock_balance
			# Total qty across warehouses for the company
			balance = frappe.db.sql(
				"""SELECT IFNULL(SUM(actual_qty), 0)
				   FROM `tabBin` b
				   INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
				   WHERE b.item_code = %(item)s AND w.company = %(company)s""",
				{"item": item.name, "company": company},
			)[0][0]
			has_stock = float(balance or 0) > 0
		except Exception:
			has_stock = False
			balance = 0
		checks.append({
			"key": "stock_qty",
			"label": _("Has stock in {0}").format(company or "—"),
			"passed": has_stock,
			"severity": "warning",
			"message": _("Available qty: {0}").format(balance)
			           if has_stock else _("No stock — submission will fail unless allow_negative_stock is on."),
			"fix": {"type": "info"},
		})

	blockers = sum(1 for c in checks if not c["passed"] and c["severity"] == "blocker")
	warnings = sum(1 for c in checks if not c["passed"] and c["severity"] == "warning")

	return {
		"item_code": item.name,
		"item_name": item.item_name,
		"company": company,
		"is_sellable": blockers == 0,
		"blockers": blockers,
		"warnings": warnings,
		"checks": checks,
	}


@frappe.whitelist()
def get_readiness_for_missing_item(query_term: str, company: str | None = None) -> dict | None:
	"""Used by POS / Sales Invoice when a search returns nothing for an exact
	item_code — surfaces the readiness reason so users understand why it's
	hidden instead of seeing a blank result.
	"""
	if not query_term:
		return None
	# Try exact name first, then barcode, then ch_gtin
	item_code = frappe.db.get_value("Item", {"name": query_term}, "name") or \
	            frappe.db.get_value("Item", {"item_code": query_term}, "name")
	if not item_code:
		bc = frappe.db.get_value("Item Barcode", {"barcode": query_term}, "parent")
		if bc:
			item_code = bc
	if not item_code and frappe.get_meta("Item").has_field("ch_gtin"):
		item_code = frappe.db.get_value("Item", {"ch_gtin": query_term}, "name")
	if not item_code:
		return None
	return get_item_readiness(item_code, company)
