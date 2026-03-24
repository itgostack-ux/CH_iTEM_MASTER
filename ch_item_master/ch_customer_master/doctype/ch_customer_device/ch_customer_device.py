# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CHCustomerDevice(Document):
	def before_insert(self):
		"""Auto-generate device_id with advisory lock."""
		if not self.device_id:
			frappe.db.sql("SELECT GET_LOCK('ch_customer_device_id', 10)")
			try:
				last = frappe.db.sql(
					"SELECT IFNULL(MAX(device_id), 0) FROM `tabCH Customer Device`"
				)[0][0] or 0
				self.device_id = last + 1
			finally:
				frappe.db.sql("SELECT RELEASE_LOCK('ch_customer_device_id')")

	def validate(self):
		self.set_item_details()
		self.set_lifecycle_link()
		self.sync_warranty_status()

	def set_item_details(self):
		"""Auto-populate item details from serial / item."""
		if self.serial_no:
			serial_doc = frappe.get_cached_doc("Serial No", self.serial_no)
			if not self.item_code:
				self.item_code = serial_doc.item_code
			if not self.imei_number and serial_doc.serial_no:
				self.imei_number = serial_doc.serial_no

		if self.item_code:
			item = frappe.get_cached_doc("Item", self.item_code)
			self.item_name = item.item_name
			self.brand = item.brand

			# Auto-fill Colour and Storage from Item Variant Attributes
			attrs = frappe.db.get_all(
				"Item Variant Attribute",
				filters={"parent": self.item_code, "attribute": ["in", ["Colour", "Storage", "RAM"]]},
				fields=["attribute", "attribute_value"],
			)
			attr_map = {a.attribute: a.attribute_value for a in attrs if a.attribute_value}
			if attr_map.get("Colour"):
				self.color = attr_map["Colour"]
			if attr_map.get("Storage"):
				self.storage_capacity = attr_map["Storage"]

			# Auto-fill default warranty months from Item if not yet set
			if not self.warranty_months and item.ch_default_warranty_months:
				self.warranty_months = item.ch_default_warranty_months

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
			{"serial_no": serial_no},
			"name",
		)
		if existing:
			doc = frappe.get_doc("CH Customer Device", existing)
			doc.customer = customer
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
