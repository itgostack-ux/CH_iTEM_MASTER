# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""Sample VAS configuration, so the coverage rules can be exercised.

Two structures decide when a plan's cover starts and neither is populated on
this estate: ``CH Coverage Rule`` (what a plan covers, per issue category) and
``Item.gofix_part_warranty_days`` (how long a fitted part is warranted). Both
are read by live code — a plan with no coverage rules starts immediately, and
a part with no term never shortens cover — so the absence is not a crash, it is
a quiet default. This fills them with plausible values so the behaviour can be
seen and tested.

Run deliberately, never on migrate::

    bench --site erpnext.local execute \\
        ch_item_master.scripts.seed_vas_sample_data.run

It is idempotent: a plan that already has coverage rules is left alone, a part
that already carries a term keeps it, and a second run reports zero of both.
Nothing is overwritten, so running it against a site where real configuration
has arrived changes nothing. Pass ``force=True`` only on a scratch site.

A modelling limit worth stating, because the sample data cannot hide it:
``Issue Category`` records *what* failed, not *why*. An extended warranty covers
a screen that failed from a manufacturing defect but not one that was dropped,
and both are "Screen & Display". Coverage rules therefore approximate the
extended-warranty/damage split rather than express it exactly; a plan that needs
the distinction needs a cause field, not more rules.
"""

import frappe
from frappe.utils import cint

# What each shape of plan covers, by issue category. Modelled on the plans this
# market actually sells rather than on the doctype's vocabulary.
_DAMAGE = ("Physical Damage", "Water Damage", "Screen & Display")
_COMPONENTS = (
	"Battery", "Charging & Power", "Speaker & Mic", "Camera",
	"Sensors & Biometrics", "Network & Connectivity", "Buttons & Keys",
	"Board Diagnosis",
)

# Part warranty by what the part is, in days. Matched on the item name because
# spares carry no component taxonomy of their own; that is good enough for
# sample data and is exactly why this is not a migrate-time seeder.
_PART_TERMS = (
	(("display", "screen", "lcd", "oled", "touch"), 180),
	(("battery",), 365),
	(("charging", "connector", "flex", "port"), 90),
	(("camera",), 180),
	(("speaker", "mic", "receiver", "buzzer"), 90),
)


def _rules_for(plan) -> dict[str, bool]:
	"""The covered/not-covered map this plan should declare."""
	scope = (plan.coverage_scope or "").strip()
	plan_type = (plan.plan_type or "").strip()

	if scope == "Screen Only":
		return {"Screen & Display": True}

	if plan_type == "Extended Warranty":
		# Defects, not damage. Stated both ways round: the categories it does
		# cover, and the damage ones it explicitly does not, because a rule that
		# is merely absent reads as an omission rather than a decision.
		covered = dict.fromkeys(_COMPONENTS, True)
		covered.update({"Physical Damage": False, "Water Damage": False})
		return covered

	if plan_type in ("Protection Plan", "Value Added Service"):
		return dict.fromkeys(_DAMAGE, True)

	if scope == "Full Device":
		return dict.fromkeys(tuple(_COMPONENTS) + _DAMAGE, True)

	return {}


def seed_coverage_rules(force: bool = False) -> dict:
	"""Give each active plan a coverage map it does not already have."""
	known = set(frappe.get_all("Issue Category", pluck="name"))
	touched, skipped, unknown = 0, 0, set()

	for name in frappe.get_all("CH Warranty Plan", filters={"status": "Active"}, pluck="name"):
		plan = frappe.get_doc("CH Warranty Plan", name)
		if plan.get("coverage_rules") and not force:
			skipped += 1
			continue

		wanted = _rules_for(plan)
		if not wanted:
			skipped += 1
			continue

		plan.set("coverage_rules", [])
		for category, covered in wanted.items():
			if category not in known:
				unknown.add(category)
				continue
			plan.append("coverage_rules", {
				"issue_type": category,
				"covered": 1 if covered else 0,
				"coverage_percent": 100 if covered else 0,
			})
		if not plan.get("coverage_rules"):
			skipped += 1
			continue
		plan.flags.ignore_permissions = True
		plan.save(ignore_permissions=True)
		touched += 1

	return {"plans_configured": touched, "plans_skipped": skipped,
	        "unknown_categories": sorted(unknown)}


def seed_part_warranties(force: bool = False, item_group: str = "Spares") -> dict:
	"""Put a supplier term on spares whose name says what they are.

	Confined to one item group, because matching on name alone is not safe: the
	catalogue holds handsets with names like "... 200MP Camera", and a first cut
	of this seeder put a part warranty on 126 phones. The group is a parameter
	rather than a constant so a catalogue that files spares elsewhere can say so.

	Unbounded on purpose. An earlier version capped each keyword, which made a
	second run pick up the next batch instead of doing nothing — re-runnable,
	but not idempotent, which is not the same promise.
	"""
	if not frappe.db.exists("Item Group", item_group):
		return {"parts_configured": 0,
		        "parts_skipped_reason": f"no Item Group named {item_group!r}"}

	touched = 0
	for keywords, days in _PART_TERMS:
		for keyword in keywords:
			rows = frappe.db.sql(
				"""SELECT name FROM `tabItem`
				   WHERE is_stock_item = 1 AND IFNULL(disabled, 0) = 0
				     AND item_group = %(group)s
				     AND LOWER(item_name) LIKE %(kw)s
				     AND (%(force)s = 1 OR IFNULL(gofix_part_warranty_days, 0) = 0)""",
				{"kw": f"%{keyword.lower()}%", "force": 1 if force else 0,
				 "group": item_group},
				pluck=True,
			)
			for item in rows:
				frappe.db.set_value("Item", item, "gofix_part_warranty_days", days,
				                    update_modified=False)
				touched += 1
	return {"parts_configured": touched}


def run(force: bool = False) -> dict:
	"""Seed both, and report what changed."""
	force = bool(cint(force))
	out = {}
	out.update(seed_coverage_rules(force=force))
	out.update(seed_part_warranties(force=force))
	frappe.db.commit()
	out["coverage_rule_rows"] = frappe.db.count("CH Coverage Rule")
	out["parts_with_terms"] = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabItem` WHERE IFNULL(gofix_part_warranty_days, 0) > 0")[0][0]
	print(frappe.as_json(out))
	return out
