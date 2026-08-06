"""Regression checks for the CH Item Price -> ERPNext Item Price authority contract."""

import frappe


def run():
	managed_name = frappe.db.get_value(
		"Item Price",
		{"ch_source_price": ("is", "set")},
		"name",
	)
	if not managed_name:
		raise AssertionError("No managed ERPNext Item Price is available for the projection test")

	managed = frappe.get_doc("Item Price", managed_name)
	source_name = managed.ch_source_price
	source = frappe.get_doc("CH Item Price", source_name)
	original_rate = managed.price_list_rate

	# Standard Item Price is a read-only projection whenever ch_source_price is set.
	managed.price_list_rate = original_rate + 1
	try:
		managed.save(ignore_permissions=True)
	except frappe.PermissionError:
		pass
	else:
		raise AssertionError("Managed ERPNext Item Price allowed an independent direct save")

	# The canonical CH synchronizer remains allowed and is idempotent.
	projection_name = source._sync_to_erp_item_price()
	if projection_name != managed_name:
		raise AssertionError(
			f"CH Item Price {source_name} projected to {projection_name}, expected {managed_name}"
		)
	projected_rate = frappe.db.get_value("Item Price", managed_name, "price_list_rate")
	if float(projected_rate or 0) != float(source.selling_price or 0):
		raise AssertionError(
			f"Projection rate {projected_rate} does not match CH selling price {source.selling_price}"
		)

	# A managed projection cannot be removed independently either.
	managed = frappe.get_doc("Item Price", managed_name)
	try:
		managed.delete(ignore_permissions=True)
	except frappe.PermissionError:
		pass
	else:
		raise AssertionError("Managed ERPNext Item Price allowed an independent delete")

	frappe.db.rollback()
	return {
		"source": source_name,
		"projection": managed_name,
		"rate": projected_rate,
		"direct_save_blocked": True,
		"direct_delete_blocked": True,
	}
