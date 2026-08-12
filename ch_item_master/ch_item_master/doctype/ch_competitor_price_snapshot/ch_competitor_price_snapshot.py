# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""Append-only log of one competitor price observation.

Snapshots are never edited. A price we collected on a given day is evidence,
and evidence that can be revised after the fact cannot be used to explain why
a buy price was set the way it was. Corrections are made by capturing a newer
snapshot, which naturally wins the rollup because it is more recent.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

#: Fields a user may still touch after insert — everything else is frozen.
_MUTABLE_AFTER_INSERT = {"valid_until"}


class CHCompetitorPriceSnapshot(Document):
	def validate(self):
		if not self.captured_at:
			self.captured_at = now_datetime()
		self._freeze_after_insert()

	def _freeze_after_insert(self):
		if self.is_new():
			return

		before = self.get_doc_before_save()
		if not before:
			return

		changed = [
			df.fieldname
			for df in self.meta.fields
			if df.fieldname not in _MUTABLE_AFTER_INSERT
			and df.fieldtype not in ("Section Break", "Column Break", "HTML")
			and (self.get(df.fieldname) or "") != (before.get(df.fieldname) or "")
		]
		if changed:
			frappe.throw(
				_("Competitor snapshots are an audit record and cannot be edited "
				  "({0}). Capture a new snapshot instead — the rollup always takes "
				  "the most recent one.").format(", ".join(changed[:5])),
				title=_("Snapshot Is Immutable"),
			)

	def on_update(self):
		"""Keep the derived band in step with the evidence behind it."""
		from ch_item_master.ch_item_master.competitor_pricing.rollup import (
			recompute_rollup_for,
		)

		recompute_rollup_for(self.item_code, self.condition_profile)
