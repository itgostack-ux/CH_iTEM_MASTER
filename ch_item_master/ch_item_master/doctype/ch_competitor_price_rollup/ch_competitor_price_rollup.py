# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CHCompetitorPriceRollup(Document):
	"""Derived band for one item × condition profile.

	Every field is recomputed by
	``ch_item_master.ch_item_master.competitor_pricing.rollup``; nothing here
	is hand-maintained. The doctype exists so the band is queryable and
	reportable alongside our own prices, not because it holds original data.
	"""

	pass
