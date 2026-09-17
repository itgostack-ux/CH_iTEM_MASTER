# Copyright (c) 2026, GoStack and contributors
"""Heal document naming series that have fallen behind the documents.

`tabSeries` holds one counter per naming prefix. A database restored from
another site brings the documents but not always a counter that matches them,
so the next insert re-uses a name that already exists::

    DuplicateEntryError: ('POS Kiosk Token', 'KTK-2026-00002',
        IntegrityError(1062, "Duplicate entry 'KTK-2026-00002' for key 'PRIMARY'"))

Nothing recovers from that on its own: every insert picks the same next number
and fails again. On this estate it had silenced six series, two of them at the
counter — POS Kiosk Token (counter 1, highest 77) and Buyback Assessment
(counter 1, highest 2). A walk-in token is mandatory before a Service Request,
so front desk and buyback intake were both unusable.

`id_sequences.sync_all_numeric_id_series` already heals numeric *field*
sequences (device IDs and the like) on after_migrate. This is the same idea for
document *names*, which that one does not cover.

Counters only ever move forward (GREATEST), so this is safe to re-run and can
never hand back a name that is already taken.
"""

import frappe

#: Names that end in digits but are not series-allocated — a hash, a code, a
#: timestamp. Updating a counter from these would push it into the millions.
_SKIP_DOCTYPES = {
	"File", "Version", "Comment", "Access Log", "Notification Log",
	"Workflow Action", "Data Import Log", "Activity Log", "Error Log",
	"Scheduled Job Log", "Route History", "View Log", "Energy Point Log",
	"Document Follow", "GST HSN Code", "CH Pincode", "Prepared Report",
}


def _series_counters() -> dict:
	return {row[0]: int(row[1] or 0) for row in frappe.db.sql("SELECT name, `current` FROM tabSeries")}


def repair_naming_series(dry_run: bool = False) -> dict:
	"""Move every lagging counter up to the highest name already issued."""
	counters = _series_counters()
	if not counters:
		return {"checked": 0, "repaired": 0, "details": []}

	tables = {row[0][3:] for row in frappe.db.sql("SHOW TABLES") if row[0].startswith("tab")}
	doctypes = [
		d for d in frappe.get_all("DocType", filters={"istable": 0, "issingle": 0}, pluck="name")
		if d in tables and d not in _SKIP_DOCTYPES
	]

	details, repaired, checked = [], 0, 0
	for doctype in doctypes:
		try:
			# Split each name into prefix + trailing digits in SQL; pulling
			# every name into Python costs minutes on a real dataset.
			rows = frappe.db.sql(
				f"""
				SELECT LEFT(`name`, CHAR_LENGTH(`name`) - CHAR_LENGTH(REGEXP_SUBSTR(`name`, '[0-9]+$')))
					   AS prefix,
					   MAX(CAST(REGEXP_SUBSTR(`name`, '[0-9]+$') AS UNSIGNED)) AS highest
				  FROM `tab{doctype}`
				 WHERE `name` REGEXP '[0-9]+$'
				 GROUP BY prefix
				""", as_dict=True)
		except Exception:
			# A doctype whose table is mid-migration must not stop the sweep.
			continue

		for row in rows:
			prefix, highest = row.get("prefix"), int(row.get("highest") or 0)
			if not prefix or prefix not in counters:
				continue
			checked += 1
			if highest <= counters[prefix]:
				continue
			details.append({
				"doctype": doctype, "prefix": prefix,
				"was": counters[prefix], "now": highest,
			})
			repaired += 1
			if not dry_run:
				frappe.db.sql(
					"UPDATE `tabSeries` SET `current` = GREATEST(`current`, %s) WHERE `name` = %s",
					(highest, prefix))
				counters[prefix] = highest

	if repaired and not dry_run:
		frappe.logger().info(f"naming_series_repair: advanced {repaired} counter(s)")
	return {"checked": checked, "repaired": repaired, "details": details}


def after_migrate() -> dict:
	"""Hook entry point — a restore is always followed by a migrate."""
	try:
		return repair_naming_series()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "naming series repair failed")
		return {"checked": 0, "repaired": 0, "details": []}
