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


def run_congruence_default_check():
	"""The seeded, brand-wide 'Congruence Loyalty' default must exist with a
	BLANK company (so it applies to every company) and leave no customer with a
	dangling loyalty_program reference."""
	from ch_item_master.ch_customer_master.loyalty import (
		CONGRUENCE_LOYALTY_PROGRAM,
		ensure_congruence_loyalty_program,
		get_applicable_loyalty_programs,
	)

	result = ensure_congruence_loyalty_program()
	name = CONGRUENCE_LOYALTY_PROGRAM

	if not frappe.db.exists("Loyalty Program", name):
		raise AssertionError(f"{name} program was not created")
	if frappe.db.get_value("Loyalty Program", name, "company"):
		raise AssertionError(f"{name} must have a BLANK company to be cross-company")

	# Universal: applies to every real (non-test) company.
	for company in frappe.get_all("Company", filters={"is_group": 0}, pluck="name"):
		if company.startswith("_Test"):
			continue
		ctx = frappe._dict(company=company, customer_group=None, territory=None, flags=frappe._dict())
		progs = get_applicable_loyalty_programs(ctx, company=company)
		if name not in progs:
			raise AssertionError(f"{name} not applicable for {company}: {progs}")

	# No dangling customer references remain.
	dangling = frappe.db.sql_list(
		"""SELECT c.name FROM `tabCustomer` c
		   LEFT JOIN `tabLoyalty Program` lp ON lp.name = c.loyalty_program
		   WHERE c.loyalty_program IS NOT NULL AND c.loyalty_program != '' AND lp.name IS NULL"""
	)
	if dangling:
		raise AssertionError(f"{len(dangling)} customers still have a dangling loyalty_program")

	print({"program": name, "healed": result.get("healed_dangling_customers"), "ok": True})
	return {"program": name, "ok": True}