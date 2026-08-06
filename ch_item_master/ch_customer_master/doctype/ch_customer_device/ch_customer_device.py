# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from ch_item_master.id_sequences import next_free_numeric_id


INVENTORY_SERIAL = "Inventory Serial"
CUSTOMER_PROVIDED = "Customer Provided"
LEGACY_UNVERIFIED = "Legacy Unverified"


class CHCustomerDevice(Document):
	def before_insert(self):
		"""Auto-generate the atomic customer-device integration ID.

		`device_id` carries a unique index, so allocation must skip values a
		restored dump or bulk import already occupies — otherwise the insert
		dies with "Device ID must be unique" and takes the whole POS invoice
		submit down with it (this hook runs on Sales Invoice on_submit).
		"""
		if not self.device_id:
			self.device_id = next_free_numeric_id("customer_device")

	def validate(self):
		self.validate_device_source()
		self.set_item_details()
		self.set_lifecycle_link()
		self.sync_warranty_status()

	def validate_device_source(self):
		"""Enforce one inventory identity and an explicit external-device boundary."""
		self.serial_no = (self.serial_no or "").strip()
		if not self.serial_no:
			frappe.throw(_("Device Identifier / IMEI is required."))

		self.device_source = self.device_source or INVENTORY_SERIAL
		if self.device_source == INVENTORY_SERIAL:
			inventory_serial = (self.inventory_serial or self.serial_no or "").strip()
			if inventory_serial != self.serial_no:
				frappe.throw(
					_("Inventory Serial must match Device Identifier / IMEI."),
					frappe.ValidationError,
				)
			serial = frappe.db.get_value(
				"Serial No",
				inventory_serial,
				["name", "item_code", "status", "customer"],
				as_dict=True,
			)
			if not serial:
				frappe.throw(
					_("Inventory device {0} must exist in Serial No.").format(
						frappe.bold(inventory_serial)
					),
					frappe.ValidationError,
				)
			self.inventory_serial = serial.name
			self.item_code = serial.item_code
			self.imei_number = self.serial_no
			self.ownership_verification = self.ownership_verification or "Verified"
			if (
				self.ownership_verification == "Verified"
				and self.current_status in ("Owned", "Sold")
				and serial.status == "Active"
			):
				frappe.throw(
					_(
						"Device {0} cannot be customer-owned while Serial No is Active warehouse stock."
					).format(frappe.bold(self.serial_no)),
					frappe.ValidationError,
				)
			if serial.customer and self.customer and serial.customer != self.customer:
				frappe.throw(
					_("Device owner does not match Serial No customer."),
					frappe.ValidationError,
				)
		elif self.device_source == CUSTOMER_PROVIDED:
			if frappe.db.exists("Serial No", self.serial_no):
				frappe.throw(
					_(
						"Customer-provided device {0} already exists in inventory. Select the inventory device instead."
					).format(frappe.bold(self.serial_no)),
					frappe.ValidationError,
				)
			self.inventory_serial = None
			self.lifecycle = None
			self.imei_number = self.serial_no
			self.ownership_verification = "Verified"
		elif self.device_source == LEGACY_UNVERIFIED:
			# Migration-only quarantine. New business flows never create this source.
			self.inventory_serial = None
			self.lifecycle = None
			self.ownership_verification = LEGACY_UNVERIFIED
			self.current_status = "Unverified"
		else:
			frappe.throw(_("Invalid device source: {0}").format(self.device_source))

	def set_item_details(self):
		"""Auto-populate item details from serial / item."""
		if self.device_source == INVENTORY_SERIAL and self.inventory_serial:
			serial_doc = frappe.get_cached_doc("Serial No", self.inventory_serial)
			self.item_code = serial_doc.item_code
			self.imei_number = self.serial_no

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

			# Auto-fill the base (manufacturer/seller) warranty from the Item's
			# default-warranty profile. Base warranty lives in its own fields so
			# a sold Extended Warranty / VAS plan never overwrites it.
			from ch_item_master.ch_item_master.warranty_api import get_item_default_warranty
			base = get_item_default_warranty(self.item_code)
			if base["months"]:
				if not self.base_warranty_type:
					self.base_warranty_type = base["type"]
				if not self.base_warranty_months:
					self.base_warranty_months = base["months"]
				if not self.base_warranty_expiry and self.purchase_date:
					self.base_warranty_expiry = frappe.utils.add_months(
						self.purchase_date, base["months"])
			# Legacy field kept in sync for existing consumers
			if not self.warranty_months and item.ch_default_warranty_months:
				self.warranty_months = base["months"] or item.ch_default_warranty_months

	def set_lifecycle_link(self):
		"""Link to CH Serial Lifecycle if it exists."""
		if self.device_source != INVENTORY_SERIAL:
			self.lifecycle = None
			return
		if self.inventory_serial:
			lifecycle = frappe.db.get_value(
				"CH Serial Lifecycle",
				{"serial_no": self.inventory_serial},
				"name",
			)
			self.lifecycle = lifecycle or None

	def sync_warranty_status(self):
		"""Sync warranty info from active Active VAS Plans.

		Without a sold plan, warranty status derives from the base
		(manufacturer/seller) warranty window.
		"""
		if not self.active_warranty_plan and self.base_warranty_expiry:
			if not self.warranty_expiry:
				self.warranty_expiry = self.base_warranty_expiry
			if not self.warranty_status or self.warranty_status in ("In Warranty", "Expired"):
				self.warranty_status = (
					"In Warranty"
					if frappe.utils.getdate(self.base_warranty_expiry) >= frappe.utils.getdate()
					else "Expired"
				)
		if self.active_warranty_plan:
			try:
				plan = frappe.get_cached_doc("Active VAS Plans", self.active_warranty_plan)
				self.warranty_plan_name = plan.plan_title or plan.warranty_plan
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
			if doc.device_source == CUSTOMER_PROVIDED:
				frappe.throw(_("A customer-provided device cannot be converted into inventory."))
			doc.customer = customer
			doc.device_source = INVENTORY_SERIAL
			doc.inventory_serial = serial_no
			doc.ownership_verification = "Verified"
			doc.update(kwargs)
			doc.flags.from_device_projection_api = True
			doc.save(ignore_permissions=True)
			return doc

		serial_doc = frappe.get_cached_doc("Serial No", serial_no)
		doc = frappe.get_doc(
			{
				"doctype": "CH Customer Device",
				"customer": customer,
				"serial_no": serial_no,
				"device_source": INVENTORY_SERIAL,
				"inventory_serial": serial_no,
				"ownership_verification": "Verified",
				"item_code": serial_doc.item_code,
				"current_status": "Owned",
				**kwargs,
			}
		)
		doc.flags.from_device_projection_api = True
		doc.insert(ignore_permissions=True)
		return doc

	@staticmethod
	def create_or_update_external(identifier, customer, item_code, **kwargs):
		"""Register a customer-provided device without creating inventory stock."""
		identifier = (identifier or "").strip()
		if not identifier:
			frappe.throw(_("Customer-provided device identifier is required."))
		if frappe.db.exists("Serial No", identifier):
			frappe.throw(
				_("Device {0} already exists in inventory.").format(frappe.bold(identifier)),
				frappe.ValidationError,
			)

		existing = frappe.db.get_value("CH Customer Device", {"serial_no": identifier}, "name")
		doc = frappe.get_doc("CH Customer Device", existing) if existing else frappe.new_doc("CH Customer Device")
		if existing and doc.device_source == INVENTORY_SERIAL:
			frappe.throw(_("An inventory device cannot be converted into customer-provided."))
		doc.customer = customer
		doc.serial_no = identifier
		doc.device_source = CUSTOMER_PROVIDED
		doc.inventory_serial = None
		doc.ownership_verification = "Verified"
		doc.item_code = item_code
		doc.current_status = kwargs.pop("current_status", None) or "Owned"
		doc.update(kwargs)
		doc.flags.from_device_projection_api = True
		if existing:
			doc.save(ignore_permissions=True)
		else:
			doc.insert(ignore_permissions=True)
		return doc

	@staticmethod
	def set_projection_status(serial_no, customer, status, **updates):
		"""System-owned status update used by sale return/cancellation hooks."""
		name = frappe.db.get_value(
			"CH Customer Device", {"serial_no": serial_no, "customer": customer}, "name"
		)
		if not name:
			return None
		doc = frappe.get_doc("CH Customer Device", name)
		doc.current_status = status
		doc.update(updates)
		doc.flags.from_device_projection_api = True
		doc.save(ignore_permissions=True)
		return doc
