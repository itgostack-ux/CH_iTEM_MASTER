# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""Canonicalise Item Tax Template names to "{title} - {company abbr}" with the
abbreviation appearing exactly once.

Two historical drifts are healed:

1. **Doubled abbr** — the gst_template provisioner used to bake the company
   abbr into the *title* ("GST 3% - GF"); ItemTaxTemplate.autoname then
   appended " - {abbr}" again, yielding names like "GST 3% - GF - GF".
   The provisioner now writes clean titles; here we strip the abbr suffix
   from existing titles.

2. **Stale abbr** — templates named under an old company abbreviation
   (e.g. "GST 5% - BMPL") survive a Company.abbr change (BMPL→BM) untouched,
   because ERPNext does not rename them. We rename to the current abbr.

Detection is by shape against each template's own company, so it works for
every present and future company — no hardcoded abbr list.
`frappe.rename_doc` rewrites all Link references (Item.taxes, GST HSN
Code.taxes, transaction item rows, …). If a template already exists at the
canonical name, the drifted one is merged into it, so no reference is left
dangling. Idempotent — canonical rows match their own target and are skipped.
"""

import frappe


def execute():
	abbr_by_company = dict(
		frappe.get_all("Company", fields=["name", "abbr"], as_list=True)
	)

	templates = frappe.get_all(
		"Item Tax Template",
		fields=["name", "title", "company"],
	)

	renamed = merged = retitled = 0
	for tpl in templates:
		abbr = abbr_by_company.get(tpl.company)
		if not abbr:
			continue

		# Strip one-or-more trailing " - {abbr}" from the title (doubled case).
		suffix = f" - {abbr}"
		clean_title = tpl.title or ""
		while clean_title.endswith(suffix) and clean_title != suffix.strip():
			clean_title = clean_title[: -len(suffix)].rstrip()
		if not clean_title:
			continue

		canonical = f"{clean_title}{suffix}"

		try:
			if tpl.name != canonical:
				if frappe.db.exists("Item Tax Template", canonical):
					# A canonical twin already exists — repoint every reference
					# of the drifted one at it and delete the drifted one.
					frappe.rename_doc(
						"Item Tax Template", tpl.name, canonical,
						merge=True, force=True,
					)
					merged += 1
				else:
					frappe.rename_doc(
						"Item Tax Template", tpl.name, canonical, force=True,
					)
					renamed += 1

			if clean_title != tpl.title and frappe.db.exists(
				"Item Tax Template", canonical
			):
				frappe.db.set_value(
					"Item Tax Template", canonical, "title", clean_title,
					update_modified=False,
				)
				retitled += 1
		except Exception:
			frappe.log_error(
				title=f"ITT name canonicalise failed: {tpl.name}",
				message=frappe.get_traceback(),
			)

	if renamed or merged or retitled:
		frappe.db.commit()
		print(
			f"Item Tax Template canonicalise: {renamed} renamed, "
			f"{merged} merged, {retitled} titles cleaned."
		)
