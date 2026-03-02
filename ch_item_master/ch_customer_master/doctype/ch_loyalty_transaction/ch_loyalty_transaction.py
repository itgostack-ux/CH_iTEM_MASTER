# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate, today


class CHLoyaltyTransaction(Document):
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
		""".format(
			exclude=f"AND name != %(exclude)s" if exclude else ""
		),
		{"customer": customer, "exclude": exclude},
		as_dict=True,
	)
	return cint(result[0].balance) if result else 0


def expire_loyalty_points():
	"""Scheduled task: expire points past their expiry date."""
	expired = frappe.get_all(
		"CH Loyalty Transaction",
		filters={
			"docstatus": 1,
			"is_expired": 0,
			"expiry_date": ("<=", today()),
			"expiry_date": ("is", "set"),
			"transaction_type": ("in", ["Earn", "Referral Bonus", "Sign Up Bonus", "Service Bonus"]),
		},
		fields=["name", "customer", "points"],
	)

	for entry in expired:
		frappe.db.set_value("CH Loyalty Transaction", entry.name, "is_expired", 1)
		# Create an expiry debit entry
		doc = frappe.get_doc(
			{
				"doctype": "CH Loyalty Transaction",
				"customer": entry.customer,
				"company": frappe.db.get_value("CH Loyalty Transaction", entry.name, "company"),
				"transaction_type": "Expire",
				"points": -abs(entry.points),
				"reference_doctype": "CH Loyalty Transaction",
				"reference_name": entry.name,
				"remarks": f"Auto-expired points from {entry.name}",
			}
		)
		doc.insert(ignore_permissions=True)
		doc.submit()

	frappe.db.commit()
