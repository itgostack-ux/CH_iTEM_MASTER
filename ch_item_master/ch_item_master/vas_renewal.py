# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""Renewing a care plan — the half of the AMC cycle that was never built.

Everything either side of this already existed. Plans are sold
(``issue_warranty_plan``), they accrue claims, their revenue is recognised
month by month (``recognize_revenue_up_to``), and they expire on a daily sweep
(``expire_sold_plans``). What nothing did was offer the customer a second term,
so every plan this estate has ever sold ends silently and the customer walks.

Three pieces, in the order they matter:

  ``renewals_due``       who is about to fall out of cover, and who just did
  ``renew_plan``         issue the continuation term
  ``notify_renewals_due`` tell somebody, durably, once a day

Two rules are worth stating because they are easy to get wrong and expensive
either way.

**Cover is never backdated.** Renewing before expiry starts the new term the
day after the old one ends, so there is no gap and no overlap. Renewing after
expiry starts it *today* — not at the old end date. Selling someone cover for a
fortnight that has already passed, during which they may already have broken
the device, is not a renewal; it is a claim waiting to happen.

**A plan renews once.** The chain is ``renewed_from``, and a second renewal of
the same term would fork it — two live plans on one device, both thinking they
are the current one. The guard is on the record, not on a flag, so it holds
even if two people press the button at the same moment.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, flt, getdate, nowdate

from ch_item_master.config import get_enabled_role_users, get_int_setting, get_role_setting
from ch_item_master.security import ensure_company_access, get_company_scope

# Renewing one of these is not a renewal — it is a new sale, and should be
# recorded as one. A Claimed plan is renewable: the claims reset with the term.
DEAD_STATUSES = ("Void", "Cancelled")

_DEFAULT_WINDOW_DAYS = 30
_DEFAULT_GRACE_DAYS = 30


def _window_days() -> int:
	return get_int_setting("vas_renewal_window_days", _DEFAULT_WINDOW_DAYS, minimum=1)


def _grace_days() -> int:
	"""How long after expiry a plan is still offered as a renewal.

	Past this it is a lapsed customer to win back, not a renewal to process,
	and leaving them on the list forever makes the list useless.
	"""
	return get_int_setting("vas_renewal_grace_days", _DEFAULT_GRACE_DAYS, minimum=0)


# ── who is falling out of cover ──────────────────────────────────────────────

@frappe.whitelist()
def renewals_due(company=None, within_days=None, grace_days=None, limit=None) -> list[dict]:
	"""Plans expiring soon, or lapsed recently, that nobody has renewed yet.

	Ordered by end date so the most urgent is first. Bounded, because this
	feeds a screen and the table it reads grows for as long as the business
	sells plans.
	"""
	within = cint(within_days) if within_days not in (None, "") else _window_days()
	grace = cint(grace_days) if grace_days not in (None, "") else _grace_days()
	row_limit = min(cint(limit) or 200, 1000)

	today = getdate(nowdate())
	conditions = [
		"p.docstatus = 1",
		"p.status NOT IN %(dead)s",
		"p.end_date BETWEEN %(from_date)s AND %(to_date)s",
		# The renewal itself is the record that this one is handled. Asked of
		# the table rather than of a flag on the parent, so a renewal made by
		# someone else a second ago is already reflected here.
		"""NOT EXISTS (
			SELECT 1 FROM `tabActive VAS Plans` r
			WHERE r.renewed_from = p.name AND r.docstatus < 2
		)""",
	]
	params = {
		"dead": DEAD_STATUSES,
		"from_date": add_days(today, -grace),
		"to_date": add_days(today, within),
		"row_limit": row_limit,
	}

	companies = get_company_scope()
	if company:
		ensure_company_access(company)
		conditions.append("p.company = %(company)s")
		params["company"] = company
	elif companies is not None:
		# Fail closed: a caller whose scope resolves to nothing sees nothing,
		# never everything.
		if not companies:
			return []
		conditions.append("p.company IN %(companies)s")
		params["companies"] = tuple(companies)

	rows = frappe.db.sql(
		f"""
		SELECT p.name, p.company, p.customer, p.customer_name, p.customer_phone,
		       p.item_code, p.item_name, p.serial_no, p.warranty_plan, p.plan_title,
		       p.plan_type, p.start_date, p.end_date, p.status, p.plan_price,
		       p.claims_used, p.max_claims
		FROM `tabActive VAS Plans` p
		WHERE {' AND '.join(conditions)}
		ORDER BY p.end_date ASC, p.name ASC
		LIMIT %(row_limit)s
		""",
		params, as_dict=True,
	)

	for row in rows:
		days = (getdate(row.end_date) - today).days
		row["days_to_expiry"] = days
		row["lapsed"] = days < 0
	return rows


# ── issuing the next term ────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def renew_plan(sold_plan, warranty_plan=None, start_date=None, plan_price=None,
               sales_invoice=None, sales_order=None, remarks=None) -> dict:
	"""Issue the continuation of an existing care plan.

	``warranty_plan`` only needs supplying when the customer is moving to a
	different product, or when the one they are on has been retired — which is
	refused rather than silently substituted, because the terms a renewal
	inherits are the terms the customer is agreeing to.
	"""
	source = frappe.get_doc("Active VAS Plans", sold_plan)
	source.check_permission("read")
	ensure_company_access(source.company)

	if cint(source.docstatus) != 1:
		frappe.throw(
			_("Only a submitted plan can be renewed. {0} is {1}.").format(
				sold_plan, {0: _("a draft"), 2: _("cancelled")}.get(cint(source.docstatus), "?")),
			title=_("Not Renewable"))

	if source.status in DEAD_STATUSES:
		frappe.throw(
			_("Plan {0} is {1}. Sell a new plan instead — renewing carries "
			  "forward terms that no longer apply.").format(sold_plan, source.status),
			title=_("Not Renewable"))

	existing = frappe.db.get_value(
		"Active VAS Plans", {"renewed_from": sold_plan, "docstatus": ("<", 2)}, "name")
	if existing:
		frappe.throw(
			_("Plan {0} has already been renewed as {1}.").format(sold_plan, existing),
			title=_("Already Renewed"))

	target_plan = warranty_plan or source.warranty_plan
	plan = frappe.get_doc("CH Warranty Plan", target_plan)
	if plan.status != "Active":
		frappe.throw(
			_("Warranty plan {0} is {1} and cannot be sold. Choose a current plan "
			  "to renew onto.").format(target_plan, plan.status),
			title=_("Plan Withdrawn"))
	if plan.company and plan.company != source.company:
		frappe.throw(
			_("Warranty plan {0} belongs to another company.").format(target_plan),
			frappe.PermissionError)
	if not cint(plan.duration_months):
		frappe.throw(
			_("Warranty plan {0} has no duration, so a renewal term cannot be "
			  "calculated.").format(target_plan), title=_("Validation Error"))

	# A renewal is a sale. The doctype already refuses to activate a plan with
	# no commercial source behind it, but it refuses deep inside submit, after
	# the record exists -- so ask here, where the message can say what to do.
	if not (sales_invoice or sales_order):
		frappe.throw(
			_("Take the renewal payment first: a submitted Sales Invoice or Sales Order "
			  "is needed before cover can start."),
			title=_("Nothing Sold Yet"))

	# Continuous where possible, never backdated. See the module docstring.
	today = getdate(nowdate())
	if start_date:
		new_start = getdate(start_date)
		if new_start < today:
			frappe.throw(
				_("A renewal cannot start in the past — cover would be sold for days "
				  "that have already happened."), title=_("Validation Error"))
	elif getdate(source.end_date) >= today:
		new_start = add_days(getdate(source.end_date), 1)
	else:
		new_start = today

	renewal = frappe.new_doc("Active VAS Plans")
	renewal.update({
		"company": source.company,
		"warranty_plan": target_plan,
		"customer": source.customer,
		"item_code": source.item_code,
		"serial_no": source.serial_no,
		"brand": source.brand,
		"is_external_device": cint(source.is_external_device),
		"external_device_source": source.external_device_source,
		"external_device_model_item": source.external_device_model_item,
		"external_device_sub_category": source.external_device_sub_category,
		"start_date": new_start,
		"end_date": add_months(new_start, cint(plan.duration_months)),
		"duration_months": cint(plan.duration_months),
		"sales_invoice": sales_invoice,
		"sales_order": sales_order,
		# A new term buys a new allowance. Carrying claims_used forward would
		# sell someone a year of cover they had already used up.
		"claims_used": 0,
		"max_claims": cint(plan.max_claims),
		"claims_per_year": cint(plan.claims_per_year),
		"deductible_amount": flt(plan.deductible_amount),
		"plan_price": flt(plan_price) if plan_price not in (None, "") else flt(plan.price),
		"fulfillment_type": plan.fulfillment_type or source.fulfillment_type,
		"device_purchase_price": flt(source.device_purchase_price),
		"renewed_from": sold_plan,
		"remarks": remarks,
	})
	renewal.insert()
	renewal.submit()

	from ch_item_master.ch_item_master.doctype.ch_vas_ledger.ch_vas_ledger import log_vas_event

	log_vas_event(
		sold_plan=renewal.name,
		event_type="Plan Renewed",
		reference_doctype="Active VAS Plans",
		reference_name=sold_plan,
		remarks=_("Renewed from {0}, which ran to {1}.").format(sold_plan, source.end_date))

	return {
		"renewal": renewal.name,
		"renewed_from": sold_plan,
		"start_date": str(renewal.start_date),
		"end_date": str(renewal.end_date),
		"plan_price": flt(renewal.plan_price),
		"continuous": getdate(source.end_date) >= today,
	}


# ── telling somebody ─────────────────────────────────────────────────────────

def notify_renewals_due():
	"""Daily: one digest per recipient of the plans about to lapse.

	Durable, and deduped on the Notification Log row rather than on a cache
	key. The SLA sweep in GoFix spent months raising realtime toasts that
	reached nobody and recording nothing; there is no reason to repeat it here.
	"""
	today = getdate(nowdate())
	# Same resolution the claim notifier uses: the VAS-settings roles, falling
	# back to the item-master role registry. Reading it from CH Item Master
	# Settings instead would silently find nothing — the field lives on
	# CH VAS Settings.
	roles = frappe.db.get_single_value("CH VAS Settings", "claim_notification_roles") \
		or get_role_setting("warranty_claim_management_roles")
	if not roles:
		return {"notified": 0, "companies": 0}

	companies = frappe.get_all("Company", pluck="name")
	notified = 0
	touched = 0
	for company in companies:
		rows = frappe.db.sql(
			"""
			SELECT p.name, p.customer_name, p.end_date
			FROM `tabActive VAS Plans` p
			WHERE p.docstatus = 1 AND p.company = %(company)s
			  AND p.status NOT IN %(dead)s
			  AND p.end_date BETWEEN %(today)s AND %(horizon)s
			  AND NOT EXISTS (
				SELECT 1 FROM `tabActive VAS Plans` r
				WHERE r.renewed_from = p.name AND r.docstatus < 2)
			ORDER BY p.end_date ASC
			LIMIT 500
			""",
			{"company": company, "dead": DEAD_STATUSES,
			 "today": today, "horizon": add_days(today, _window_days())},
			as_dict=True,
		)
		if not rows:
			continue
		touched += 1
		subject = _("{0} care plans expiring within {1} days — {2}").format(
			len(rows), _window_days(), company)
		body = "<br>".join(
			f"{r.name} · {frappe.utils.escape_html(r.customer_name or '')} · {r.end_date}"
			for r in rows[:25])
		if len(rows) > 25:
			body += "<br>" + _("…and {0} more.").format(len(rows) - 25)

		for user in get_enabled_role_users(roles, company=company, limit=50):
			# One per user per company per day. The row is the dedupe; a cache
			# key set inside a transaction that rolls back would suppress a
			# digest that was never delivered.
			already = frappe.db.sql(
				"""SELECT 1 FROM `tabNotification Log`
				   WHERE for_user = %s AND document_type = 'Active VAS Plans'
				     AND subject LIKE %s AND DATE(creation) = %s LIMIT 1""",
				(user, f"%{company}%", today))
			if already:
				continue
			try:
				frappe.get_doc({
					"doctype": "Notification Log",
					"for_user": user,
					"type": "Alert",
					"document_type": "Active VAS Plans",
					"document_name": rows[0].name,
					"subject": subject,
					"email_content": body,
				}).insert(ignore_permissions=True)
				notified += 1
				# Committed per recipient so one failure cannot roll back the
				# people already told.
				frappe.db.commit()
			except Exception:
				frappe.log_error(frappe.get_traceback(), "VAS renewal digest")

	return {"notified": notified, "companies": touched}
