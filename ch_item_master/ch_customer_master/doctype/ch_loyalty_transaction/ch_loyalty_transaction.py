# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate, today

from ch_item_master.config import get_int_setting
from ch_item_master.id_sequences import next_numeric_id


class CHLoyaltyTransaction(Document):
	def before_insert(self):
		"""Auto-generate the atomic loyalty transaction integration ID."""
		if not self.loyalty_txn_id:
			self.loyalty_txn_id = next_numeric_id("loyalty_transaction")

	def validate(self):
		self.validate_points()
		self.set_closing_balance()

	def on_submit(self):
		self.update_customer_balance()

	def on_cancel(self):
		self.update_customer_balance()

	def validate_points(self):
		"""Ensure redeem types have negative points, earn types have positive."""
		if self.transaction_type in ("Redeem",):
			if self.points > 0:
				self.points = -abs(self.points)
		elif self.transaction_type in ("Expire",):
			if self.points > 0:
				self.points = -abs(self.points)
		elif self.transaction_type in ("Earn", "Referral Bonus", "Sign Up Bonus", "Service Bonus"):
			if self.points < 0:
				self.points = abs(self.points)

		if self.transaction_type == "Redeem":
			current_balance = get_loyalty_balance(self.customer)
			if abs(self.points) > current_balance:
				frappe.throw(
					_("Insufficient loyalty points. Balance: {0}, Trying to redeem: {1}").format(
						current_balance, abs(self.points)
					)
				)

	def set_closing_balance(self):
		"""Calculate closing balance after this transaction."""
		current = get_loyalty_balance(self.customer, exclude=self.name if not self.is_new() else None)
		self.closing_balance = current + cint(self.points)

	def update_customer_balance(self):
		"""Update the customer's ch_loyalty_points_balance custom field."""
		balance = get_loyalty_balance(self.customer)
		frappe.db.set_value("Customer", self.customer, "ch_loyalty_points_balance", balance)


def get_loyalty_balance(customer, exclude=None):
	"""Get total loyalty points balance for a customer across all companies."""
	filters = {"customer": customer, "docstatus": 1, "is_expired": 0}
	if exclude:
		filters["name"] = ("!=", exclude)

	result = frappe.db.sql(
		"""
		SELECT IFNULL(SUM(points), 0) as balance
		FROM `tabCH Loyalty Transaction`
		WHERE customer = %(customer)s
			AND docstatus = 1
			AND is_expired = 0
			{exclude}
		""".format(  # noqa: UP032
			exclude=f"AND name != %(exclude)s" if exclude else ""
		),
		{"customer": customer, "exclude": exclude},
		as_dict=True,
	)
	return cint(result[0].balance) if result else 0


def expire_loyalty_points():
	"""Expire a locked, bounded batch and create retry-safe debit entries."""
	batch_limit = min(get_int_setting("scheduler_batch_limit", 500, minimum=1), 5000)
	rows = frappe.db.sql(
		"""
			SELECT `name`, `customer`, `company`, `points`
			FROM `tabCH Loyalty Transaction`
			WHERE `docstatus` = 1
			  AND `is_expired` = 0
			  AND `expiry_date` IS NOT NULL
			  AND `expiry_date` <= %(today)s
			  AND `transaction_type` IN ('Earn', 'Referral Bonus', 'Sign Up Bonus', 'Service Bonus')
			ORDER BY `expiry_date` ASC, `name` ASC
			LIMIT %(fetch_limit)s
			FOR UPDATE
		""",
		{"today": today(), "fetch_limit": batch_limit + 1},
		as_dict=True,
	)
	entries = rows[:batch_limit]
	if not entries:
		return {"expired": 0, "failed": 0, "has_more": False}

	names = tuple(entry.name for entry in entries)
	existing_expiry_refs = set(frappe.get_all(
		"CH Loyalty Transaction",
		filters={
			"docstatus": 1,
			"transaction_type": "Expire",
			"reference_doctype": "CH Loyalty Transaction",
			"reference_name": ("in", names),
		},
		pluck="reference_name",
		limit=len(names),
	))
	if existing_expiry_refs:
		frappe.db.sql(
			"""
				UPDATE `tabCH Loyalty Transaction`
				SET `is_expired` = 1
				WHERE `name` IN %(names)s AND `is_expired` = 0
			""",
			{"names": tuple(existing_expiry_refs)},
		)

	to_create = [entry for entry in entries if entry.name not in existing_expiry_refs]
	successful = len(existing_expiry_refs)
	failed = 0
	if to_create:
		for index, entry in enumerate(to_create, start=1):
			save_point = f"loyalty_expiry_{index}"
			frappe.db.savepoint(save_point)
			try:
				frappe.db.sql(
					"""
						UPDATE `tabCH Loyalty Transaction`
						SET `is_expired` = 1
						WHERE `name` = %s AND `is_expired` = 0
					""",
					(entry.name,),
				)
				doc = frappe.get_doc({
					"doctype": "CH Loyalty Transaction",
					"customer": entry.customer,
					"company": entry.company,
					"transaction_type": "Expire",
					"points": -abs(entry.points),
					"reference_doctype": "CH Loyalty Transaction",
					"reference_name": entry.name,
					"remarks": f"Auto-expired points from {entry.name}",
				})
				doc.insert(ignore_permissions=True)
				doc.submit()
				successful += 1
			except Exception:
				frappe.db.rollback(save_point=save_point)
				failed += 1
				frappe.log_error(
					frappe.get_traceback(),
					f"Loyalty expiry failed for {entry.name}",
				)

	return {
		"expired": successful,
		"failed": failed,
		"has_more": len(rows) > batch_limit or bool(failed),
	}
