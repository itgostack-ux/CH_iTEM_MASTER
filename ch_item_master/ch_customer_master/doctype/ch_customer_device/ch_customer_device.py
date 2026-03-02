# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CHCustomerDevice(Document):
	def validate(self):
		self.set_item_details()
		self.set_lifecycle_link()
		self.sync_warranty_status()

	def set_item_details(self):
		"""Auto-populate item details from serial / item."""
		if self.serial_no and not self.imei_number:
			serial_doc = frappe.get_cached_doc("Serial No", self.serial_no)
			if not self.item_code:
				self.item_code = serial_doc.item_code

		if self.item_code:
			item = frappe.get_cached_doc("Item", self.item_code)
			self.item_name = item.item_name
			self.brand = item.brand

	def set_lifecycle_link(self):
		"""Link to CH Serial Lifecycle if it exists."""
		if self.serial_no and not self.lifecycle:
			lifecycle = frappe.db.get_value(
				"CH Serial Lifecycle",
				{"serial_no": self.serial_no},
				"name",
			)
			if lifecycle:
				self.lifecycle = lifecycle

	def sync_warranty_status(self):
		"""Sync warranty info from active CH Sold Plan."""
		if self.active_warranty_plan:
			try:
				plan = frappe.get_cached_doc("CH Sold Plan", self.active_warranty_plan)
				self.warranty_plan_name = plan.plan_name
				self.warranty_expiry = plan.valid_to
				if plan.status == "Active":
					self.warranty_status = "In Warranty"
				elif plan.status in ("Expired", "Void"):
					self.warranty_status = "Expired"
				elif plan.status == "Claimed":
					self.warranty_status = "Claimed"
			except frappe.DoesNotExistError:
				pass

	@staticmethod
	def create_or_update_for_serial(serial_no, customer, **kwargs):
		"""Create or update a CH Customer Device record.
		Called by hooks when a device changes ownership.
		"""
		existing = frappe.db.get_value(
			"CH Customer Device",
			{"serial_no": serial_no, "customer": customer},
			"name",
		)
		if existing:
			doc = frappe.get_doc("CH Customer Device", existing)
			doc.update(kwargs)
			doc.save(ignore_permissions=True)
			return doc

		serial_doc = frappe.get_cached_doc("Serial No", serial_no)
		doc = frappe.get_doc(
			{
				"doctype": "CH Customer Device",
				"customer": customer,
				"serial_no": serial_no,
				"item_code": serial_doc.item_code,
				"current_status": "Owned",
				**kwargs,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc
