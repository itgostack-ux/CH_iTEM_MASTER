import frappe
from frappe.utils import add_days, cint, flt, getdate, today


def get_applicable_loyalty_programs(doc, company=None):
	"""Return auto-opt-in loyalty programs scoped to the active company.

	ERPNext's stock resolver matches by date, customer group, and territory but
	ignores company entirely. On mixed sites with leftover `_Test Company`
	programs, that causes false multi-program matches for live customers.
	"""
	from erpnext.selling.doctype.customer.customer import get_nested_links

	company = company or getattr(doc, "company", None) or frappe.defaults.get_global_default("company")
	ignore_permissions = bool(getattr(getattr(doc, "flags", None), "ignore_permissions", False))
	today_date = getdate(today())
	programs = frappe.get_all(
		"Loyalty Program",
		fields=["name", "company", "customer_group", "customer_territory", "from_date", "to_date"],
		filters={"auto_opt_in": 1},
	)

	matches = []
	for program in programs:
		if program.from_date and getdate(program.from_date) > today_date:
			continue
		if program.to_date and getdate(program.to_date) < today_date:
			continue

		group_ok = (
			not program.customer_group
			or getattr(doc, "customer_group", None)
			in get_nested_links("Customer Group", program.customer_group, ignore_permissions)
		)
		territory_ok = (
			not program.customer_territory
			or getattr(doc, "territory", None)
			in get_nested_links("Territory", program.customer_territory, ignore_permissions)
		)
		if group_ok and territory_ok:
			matches.append(program)

	if not company:
		return [program.name for program in matches]

	scoped = [program.name for program in matches if not program.company or program.company == company]
	if scoped:
		return scoped

	# If all matches belong to other companies, return none rather than surfacing
	# irrelevant test/demo programs to live customers.
	if any(program.company for program in matches):
		return []

	return [program.name for program in matches]


def ensure_loyalty_baseline(
	company=None,
	program_name=None,
	collection_factor=1000,
	conversion_factor=1,
	expiry_duration=365,
):
	"""Repair site-level loyalty baseline for the live default company.

	- force Customer naming to Naming Series
	- disable seeded `_Test Company` auto-opt-in programs
	- ensure exactly one live-company auto-opt-in loyalty program exists
	"""
	company = company or frappe.defaults.get_global_default("company")
	if not company:
		raise frappe.ValidationError("Default company is not configured for this site")

	frappe.defaults.set_global_default("cust_master_name", "Naming Series")

	disabled_programs = []
	for row in frappe.get_all(
		"Loyalty Program",
		fields=["name", "company", "auto_opt_in"],
		filters={"auto_opt_in": 1},
	):
		if (row.company or "").startswith("_Test Company") and row.name.startswith("Test "):
			frappe.db.set_value("Loyalty Program", row.name, "auto_opt_in", 0, update_modified=False)
			disabled_programs.append(row.name)

	company_programs = frappe.get_all(
		"Loyalty Program",
		fields=["name", "auto_opt_in"],
		filters={"company": company},
		order_by="creation asc",
	)

	created_program = None
	if not company_programs:
		program_name = program_name or f"{company} Loyalty"
		cost_center = frappe.db.get_value("Company", company, "cost_center") or frappe.db.get_value(
			"Cost Center", {"company": company, "is_group": 0}, "name"
		)
		program = frappe.get_doc(
			{
				"doctype": "Loyalty Program",
				"loyalty_program_name": program_name,
				"loyalty_program_type": "Single Tier Program",
				"from_date": today(),
				"company": company,
				"auto_opt_in": 1,
				"conversion_factor": conversion_factor,
				"expiry_duration": expiry_duration,
				"cost_center": cost_center,
				"collection_rules": [
					{
						"tier_name": "Bronze",
						"collection_factor": collection_factor,
						"min_spent": 0,
					}
				],
			}
		)
		program.flags.ignore_permissions = True
		program.insert()
		created_program = program.name
		company_programs = [{"name": program.name, "auto_opt_in": 1}]
	elif len(company_programs) == 1 and not company_programs[0].auto_opt_in:
		frappe.db.set_value(
			"Loyalty Program", company_programs[0].name, "auto_opt_in", 1, update_modified=False
		)

	backfilled_customers = 0
	if len(company_programs) == 1:
		program_to_assign = created_program or company_programs[0]["name"]
		for customer in frappe.get_all(
			"Customer",
			fields=["name", "customer_group", "territory", "loyalty_program"],
			filters={"loyalty_program": ["in", ["", None]]},
		):
			programs = get_applicable_loyalty_programs(customer, company=company)
			if programs == [program_to_assign]:
				frappe.db.set_value(
					"Customer", customer.name, "loyalty_program", program_to_assign, update_modified=False
				)
				backfilled_customers += 1

	frappe.db.commit()
	return {
		"company": company,
		"naming": frappe.defaults.get_global_default("cust_master_name"),
		"disabled_test_programs": disabled_programs,
		"backfilled_customers": backfilled_customers,
		"program_name": created_program or (company_programs[0]["name"] if len(company_programs) == 1 else None),
	}


CONGRUENCE_LOYALTY_PROGRAM = "Congruence Loyalty"


def ensure_congruence_loyalty_program():
	"""Seed the brand-wide default loyalty program ``Congruence Loyalty``.

	Idempotent; runs on every ``after_migrate`` so it is always present in every
	environment. The program's ``company`` is deliberately left BLANK so it
	applies to ALL companies — ``get_applicable_loyalty_programs`` treats a
	company-less program as universal (see its company filter) — making it the
	fallback used whenever a customer/company has no company-specific program.

	Also self-heals customers that point at a *deleted* loyalty program
	(dangling link), which would otherwise fail Sales Invoice insert with
	"Could not find Loyalty Program ...".
	"""
	name = CONGRUENCE_LOYALTY_PROGRAM

	if not frappe.db.exists("Loyalty Program", name):
		doc = frappe.get_doc({
			"doctype": "Loyalty Program",
			"loyalty_program_name": name,
			"loyalty_program_type": "Single Tier Program",
			"from_date": "2020-01-01",
			"auto_opt_in": 1,
			"conversion_factor": 1,
			"expiry_duration": 365,
			"collection_rules": [
				{"tier_name": "Member", "collection_factor": 100, "min_spent": 0}
			],
		})
		doc.flags.ignore_permissions = True
		doc.insert()

	# ERPNext auto-fills company with the site default company on insert; force
	# it blank so the program is universal, and keep it auto-opt-in.
	current = frappe.db.get_value(
		"Loyalty Program", name, ["company", "auto_opt_in"], as_dict=True
	)
	updates = {}
	if current.company:
		updates["company"] = None
	if not current.auto_opt_in:
		updates["auto_opt_in"] = 1
	if updates:
		frappe.db.set_value("Loyalty Program", name, updates)

	# Heal customers pointing at a now-deleted program → the common default.
	dangling = frappe.db.sql_list(
		"""
		SELECT c.name
		FROM `tabCustomer` c
		LEFT JOIN `tabLoyalty Program` lp ON lp.name = c.loyalty_program
		WHERE c.loyalty_program IS NOT NULL AND c.loyalty_program != ''
		  AND lp.name IS NULL
		"""
	)
	for customer in dangling:
		frappe.db.set_value("Customer", customer, "loyalty_program", name, update_modified=False)

	# Enrol any real customer that still has no loyalty program.
	# Test / regression fixtures (customer_group starts with '_Test') are
	# left untouched so unit tests keep their isolated setups.
	unenrolled = frappe.db.sql_list(
		"""
		SELECT name
		FROM `tabCustomer`
		WHERE disabled = 0
		  AND (loyalty_program IS NULL OR loyalty_program = '')
		  AND (customer_group IS NULL OR customer_group NOT LIKE '\\_Test%%')
		  AND (customer_name IS NULL OR customer_name NOT LIKE '\\_Test%%')
		"""
	)
	for customer in unenrolled:
		frappe.db.set_value("Customer", customer, "loyalty_program", name, update_modified=False)

	frappe.db.commit()
	return {
		"program": name,
		"healed_dangling_customers": len(dangling),
		"backfilled_unenrolled_customers": len(unenrolled),
	}


# ── Cross-company loyalty (market-standard) ──────────────────────────────────


def get_ch_loyalty_info(customer: str, company: str = None) -> dict:
	"""Return loyalty balance, tier, and conversion info for a customer.

	Balance is aggregated across ALL loyalty programs for this customer —
	no company or program filter — so points earned at GoFix are visible
	when the customer bills at GoGizmo (brand-level shared wallet).

	The conversion_factor (redemption value per point) is taken from the
	ACTIVE COMPANY's loyalty program so different companies can offer
	different redemption rates (e.g. GoFix: 1 pt = ₹1, GoGizmo: 1 pt = ₹0.5).
	"""
	today_date = today()

	# Cross-company, cross-program balance — no program filter
	result = frappe.db.sql(
		"""
		SELECT
			IFNULL(SUM(loyalty_points), 0)  AS points,
			IFNULL(SUM(purchase_amount), 0) AS total_spent
		FROM `tabLoyalty Point Entry`
		WHERE customer = %s
		  AND expiry_date >= %s
		  AND posting_date <= %s
		""",
		(customer, today_date, today_date),
		as_dict=True,
	)

	points     = cint(result[0].points)     if result else 0
	total_spent = flt(result[0].total_spent) if result else 0

	# Resolve the active loyalty program:
	# 1. Active company's program (correct earning/redemption rate for this store)
	# 2. Fall back to customer's assigned program
	loyalty_program = None
	if company:
		loyalty_program = frappe.db.get_value(
			"Loyalty Program",
			{"company": company, "auto_opt_in": 1},
			"name",
		)
	if not loyalty_program:
		loyalty_program = frappe.db.get_value("Customer", customer, "loyalty_program")

	if not loyalty_program:
		return {"loyalty_program": None, "points": 0, "conversion_factor": 0, "currency_value": 0, "tier_name": ""}

	lp_doc = frappe.get_cached_doc("Loyalty Program", loyalty_program)
	conversion_factor = flt(lp_doc.conversion_factor) or 1

	# Tier is based on total cross-company spend against this program's thresholds
	tier_name = ""
	if lp_doc and lp_doc.collection_rules:
		for tier in sorted(lp_doc.collection_rules, key=lambda r: flt(r.min_spent)):
			if total_spent >= flt(tier.min_spent):
				tier_name = tier.tier_name

	return {
		"loyalty_program": loyalty_program,
		"points": points,
		"conversion_factor": conversion_factor,
		"currency_value": flt(points * conversion_factor),
		"tier_name": tier_name,
		"total_spent": total_spent,
	}


def reset_loyalty_expiry_on_purchase(customer: str, posting_date: str) -> None:
	"""Extend expiry of all active loyalty entries when the customer makes a purchase.

	Market standard: each purchase resets the expiry clock so active buyers
	never lose points mid-cycle. Works across all programs/companies for the
	same customer.
	"""
	# Derive expiry_duration from whichever program has the customer's entries
	program_name = frappe.db.get_value("Customer", customer, "loyalty_program") or \
		frappe.db.get_value("Loyalty Point Entry", {"customer": customer}, "loyalty_program")
	if not program_name:
		return

	expiry_duration = cint(frappe.db.get_value("Loyalty Program", program_name, "expiry_duration"))
	if not expiry_duration:
		return

	new_expiry = add_days(posting_date, expiry_duration)

	# Extend ALL active entries for this customer across all programs
	frappe.db.sql(
		"""
		UPDATE `tabLoyalty Point Entry`
		SET expiry_date = %s, modified = NOW()
		WHERE customer = %s
		  AND expiry_date >= %s
		""",
		(new_expiry, customer, today()),
	)


