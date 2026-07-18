# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CHShadowLiveSettings(Document):
	def validate(self):
		if self.enabled and not (self.master_otp or "").strip():
			frappe.throw(_("Set a Master OTP before enabling Shadow Live mode."))
		if self.enabled and not self.active_until:
			frappe.throw(_("Set 'Active Until' — shadow live must have a hard expiry."))
