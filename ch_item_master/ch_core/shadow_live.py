"""Shadow-live pilot mode — one switch, auto-expiring.

During shadow live the business runs real transactions in parallel with the
legacy system, but customers must not be contacted and payment terminals are
not wired yet. This module is the single source of truth for that mode:

* ``is_shadow_live()``            — master gate (enabled AND not past expiry)
* ``master_otp_matches(code)``    — staff-entered master OTP unlocks OTP gates
* ``suppress_customer_comms()``   — customer WhatsApp / SMS / OTP emails skipped
* ``manual_payment_entry()``      — POS bypasses Paytm / Pine Labs terminals

Config (not hardcoded) so go-live is a checkbox flip, and the 'Active Until'
date disarms the mode even if someone forgets.
"""

import frappe
from frappe.utils import getdate, nowdate


def _settings():
	if not frappe.db.exists("DocType", "CH Shadow Live Settings"):
		return None
	try:
		return frappe.get_cached_doc("CH Shadow Live Settings")
	except Exception:
		return None


def is_shadow_live() -> bool:
	conf = _settings()
	if not conf or not conf.enabled:
		return False
	if conf.active_until and getdate(nowdate()) > getdate(conf.active_until):
		return False  # auto-disarmed — expiry passed
	return True


def master_otp_matches(code) -> bool:
	if not is_shadow_live():
		return False
	conf = _settings()
	master = (conf.master_otp or "").strip()
	return bool(master) and str(code or "").strip() == master


def suppress_customer_comms() -> bool:
	return is_shadow_live() and bool(_settings().suppress_customer_messages)


def manual_payment_entry() -> bool:
	return is_shadow_live() and bool(_settings().manual_card_entry)
