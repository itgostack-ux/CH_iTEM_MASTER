"""Scheduled tasks for the Supplier Scheme module."""

import frappe
from frappe import _
from frappe.utils import getdate, nowdate, add_days


def auto_close_expired_schemes():
	"""Auto-close schemes whose valid_to has passed."""
	today = getdate(nowdate())
	expired = frappe.get_all(
		"Supplier Scheme Circular",
		filters={
			"docstatus": 1,
			"status": "Active",
			"valid_to": ("<", today),
		},
		pluck="name",
	)

	for scheme_name in expired:
		frappe.db.set_value("Supplier Scheme Circular", scheme_name, "status", "Closed")
		frappe.logger("supplier_scheme").info(f"Auto-closed expired scheme {scheme_name}")

	if expired:
		frappe.db.commit()


def send_expiry_claim_reminders():
	"""Daily job — notify purchase managers of active schemes expiring in ≤15 days
	that have zero claim amount (i.e. no achievement entries yet).
	Only sends once per scheme every 7 days to avoid spam.
	"""
	today = nowdate()
	window_end = add_days(today, 15)

	expiring = frappe.db.sql("""
		SELECT name, scheme_name, brand, valid_to,
		       DATEDIFF(valid_to, %(today)s) as days_left,
		       COALESCE(last_expiry_reminder, '2000-01-01') as last_reminder
		FROM `tabSupplier Scheme Circular`
		WHERE docstatus = 1
		  AND status = 'Active'
		  AND valid_to BETWEEN %(today)s AND %(window_end)s
		  AND (total_claim_amount IS NULL OR total_claim_amount = 0)
		  AND (last_expiry_reminder IS NULL
		       OR last_expiry_reminder < DATE_SUB(CURDATE(), INTERVAL 7 DAY))
	""", {"today": today, "window_end": window_end}, as_dict=True)

	if not expiring:
		return

	# Get approver/manager emails
	manager_emails = frappe.db.sql("""
		SELECT DISTINCT u.email
		FROM `tabUser` u
		JOIN `tabHas Role` hr ON hr.parent = u.name
		WHERE hr.role IN ('Purchase Manager','Scheme Manager','System Manager')
		  AND u.enabled = 1 AND u.email != ''
	""", as_list=True)

	if not manager_emails:
		return

	recipients = [row[0] for row in manager_emails]

	rows_html = "".join(
		f"<tr>"
		f"<td style='padding:6px 12px'><a href='{frappe.utils.get_url_to_form("Supplier Scheme Circular", s.name)}'>{s.name}</a></td>"
		f"<td style='padding:6px 12px'>{s.scheme_name or ''}</td>"
		f"<td style='padding:6px 12px'>{s.brand or ''}</td>"
		f"<td style='padding:6px 12px;font-weight:bold;color:#ef4444'>{s.days_left} days</td>"
		f"</tr>"
		for s in expiring
	)

	active_schemes_url = f"{frappe.utils.get_url()}/app/supplier-scheme-circular?status=Active"

	body = _("""
		<div style='font-family:Segoe UI,Arial,sans-serif;max-width:760px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden'>
		<div style='background:#0f172a;color:#ffffff;padding:12px 16px;font-weight:600'>Congruence Holdings — Supplier Scheme Alert</div>
		<div style='padding:16px'>
		<p>The following schemes are expiring soon but have <strong>no claims recorded yet</strong>.
		Please update achievement data before closure.</p>
		<table border='1' cellspacing='0' cellpadding='0' style='border-collapse:collapse;font-size:14px;width:100%'>
			<thead style='background:#f1f5f9'>
				<tr>
					<th style='padding:6px 12px'>Scheme</th>
					<th style='padding:6px 12px'>Name</th>
					<th style='padding:6px 12px'>Brand</th>
					<th style='padding:6px 12px'>Expires In</th>
				</tr>
			</thead>
			<tbody>{rows}</tbody>
		</table>
		<p style='margin-top:16px'><a href='{active_schemes_url}' style='background:#0b57d0;color:#ffffff;text-decoration:none;padding:10px 14px;border-radius:6px;display:inline-block;font-weight:600'>Open Active Schemes</a></p>
		</div></div>
	""").format(rows=rows_html)

	frappe.sendmail(
		recipients=recipients,
		subject=_("⚠ {count} Scheme(s) Expiring Soon With No Claims").format(count=len(expiring)),
		message=body,
		delayed=False,
	)

	# Mark last_expiry_reminder to prevent duplicate emails
	for s in expiring:
		frappe.db.set_value(
			"Supplier Scheme Circular", s.name,
			"last_expiry_reminder", today,
			update_modified=False,
		)
	frappe.db.commit()
	frappe.logger("supplier_scheme").info(
		f"Sent expiry reminders for {len(expiring)} schemes to {len(recipients)} managers"
	)
