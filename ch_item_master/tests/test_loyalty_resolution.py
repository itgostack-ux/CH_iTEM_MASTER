"""Focused validation helpers for loyalty program resolution."""

import random

import frappe

from ch_item_master import monkey_patches as _monkey_patches  # noqa: F401
from ch_item_master.ch_customer_master.loyalty import ensure_loyalty_baseline


def _customer_context():
	return frappe._dict(
		customer_group=None,
		territory="All Territories",
		flags=frappe._dict(ignore_permissions=True),
	)


def run_resolution_check():
	from erpnext.selling.doctype.customer import customer as customer_module

	programs = customer_module.get_loyalty_programs(_customer_context())
	print({"programs": programs})
	if any(name.startswith("Test ") for name in programs):
		raise AssertionError(f"unexpected test loyalty programs resolved: {programs}")
	return {"programs": programs}


def run_end_to_end_fix():
	from erpnext.selling.doctype.customer import customer as customer_module

	result = ensure_loyalty_baseline()
	programs = customer_module.get_loyalty_programs(_customer_context())
	print({"baseline": result, "programs": programs})

	if result.get("naming") != "Naming Series":
		raise AssertionError(f"customer naming is still {result.get('naming')}")
	if not result.get("program_name"):
		raise AssertionError("no live-company loyalty program is available after baseline fix")
	if programs != [result["program_name"]]:
		raise AssertionError(f"expected only {result['program_name']} to resolve, got {programs}")

	return {"baseline": result, "programs": programs}


def run_enrollment_smoke():
	company = frappe.defaults.get_global_default("company")
	result = ensure_loyalty_baseline(company=company)
	program_name = result["program_name"]
	if not program_name:
		raise AssertionError("no loyalty program available for enrollment smoke")

	customer = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": f"Loyalty Smoke {frappe.generate_hash(length=6)}",
			"customer_type": "Individual",
			"customer_group": frappe.db.get_value("Customer Group", {}, "name") or "All Customer Groups",
			"territory": frappe.db.get_value("Territory", {}, "name") or "All Territories",
			"mobile_no": "9" + "".join(random.choice("0123456789") for _ in range(9)),
		}
	)
	customer.insert(ignore_permissions=True)

	enrolled_program = frappe.db.get_value("Customer", customer.name, "loyalty_program")
	print({"customer": customer.name, "loyalty_program": enrolled_program})
	if enrolled_program != program_name:
		raise AssertionError(
			f"expected {program_name} on new customer {customer.name}, got {enrolled_program}"
		)

	return {"customer": customer.name, "loyalty_program": enrolled_program}