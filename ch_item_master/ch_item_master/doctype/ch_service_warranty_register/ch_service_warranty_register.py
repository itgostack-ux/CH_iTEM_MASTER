# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import getdate, nowdate, add_months


class CHServiceWarrantyRegister(Document):
	def autoname(self):
		pass  # Uses naming rule from JSON

	def validate(self):
		if not self.warranty_end_date and self.warranty_start_date and self.warranty_months:
			self.warranty_end_date = add_months(self.warranty_start_date, self.warranty_months)
		if not self.title:
			self.title = f"{self.serial_no} - {self.part_replaced or self.service_type}"

	def is_active(self):
		"""Check if this repair warranty is still valid."""
		if self.status != "Active":
			return False
		if self.claims_used >= (self.max_claims or 1):
			return False
		if getdate(self.warranty_end_date) < getdate(nowdate()):
			return False
		return True

	def matches_issue(self, issue_categories=None):
		"""Check if the reported issue matches covered categories."""
		if not issue_categories:
			return True  # no categories specified = general coverage

		covered = [c.strip().lower() for c in (self.covered_issue_categories or "").split(",") if c.strip()]
		excluded = [c.strip().lower() for c in (self.excluded_issue_categories or "").split(",") if c.strip()]

		for cat in issue_categories:
			cat_lower = cat.lower().strip()
			if cat_lower in excluded:
				return False
			if covered and cat_lower not in covered:
				return False
		return True

	def record_claim(self, claim_name=None):
		"""Increment claims_used and check if warranty should be marked claimed."""
		self.claims_used = (self.claims_used or 0) + 1
		if self.claims_used >= (self.max_claims or 1):
			self.status = "Claimed"
		self.save(ignore_permissions=True)


def create_service_warranty(service_invoice=None, service_ticket=None, serial_no=None,
                            item_code=None, customer=None, part_replaced=None,
                            warranty_months=3, service_date=None, service_company=None,
                            service_type=None, technician=None, part_item_code=None,
                            covered_categories="same_part_failure,workmanship_defect",
                            excluded_categories="accidental_damage,water_damage,physical_abuse",
                            claim_name=None):
	"""Create a Service Warranty Register entry after a GoFix repair."""
	swr = frappe.new_doc("CH Service Warranty Register")
	swr.serial_no = serial_no
	swr.item_code = item_code
	swr.customer = customer
	swr.service_invoice = service_invoice
	swr.service_ticket = service_ticket
	swr.service_date = service_date or frappe.utils.nowdate()
	swr.service_company = service_company
	swr.service_type = service_type
	swr.technician = technician
	swr.part_replaced = part_replaced
	swr.part_item_code = part_item_code
	swr.warranty_months = warranty_months
	swr.warranty_start_date = service_date or frappe.utils.nowdate()
	swr.warranty_end_date = add_months(swr.warranty_start_date, warranty_months)
	swr.covered_issue_categories = covered_categories
	swr.excluded_issue_categories = excluded_categories
	swr.created_from = claim_name
	swr.status = "Active"
	swr.insert(ignore_permissions=True)
	return swr.name
