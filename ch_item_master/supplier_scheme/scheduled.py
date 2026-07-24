"""Scheduled tasks for the Supplier Scheme module."""

import frappe
from frappe import _
from frappe.utils import getdate, nowdate, add_days

from ch_item_master.config import get_enabled_role_emails, get_int_setting, get_role_setting


def _scheduler_batch_limit() -> int:
	return min(get_int_setting("supplier_scheme_scheduler_batch_limit", 200, minimum=1), 2000)


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
		order_by="valid_to asc, name asc",
		limit=_scheduler_batch_limit(),
	)

	if expired:
		placeholders = ", ".join(["%s"] * len(expired))
		frappe.db.sql(
			f"""
				UPDATE `tabSupplier Scheme Circular`
				SET `status` = 'Closed', `modified` = %s, `modified_by` = %s
				WHERE `name` IN ({placeholders})
				  AND `docstatus` = 1
				  AND `status` = 'Active'
				  AND `valid_to` < %s
			""",
			(
				frappe.utils.now(),
				frappe.session.user,
				*expired,
				today,
			),
		)
		frappe.logger("supplier_scheme").info(
			f"Auto-closed {len(expired)} expired supplier schemes"
		)
	return {"closed": len(expired), "has_more": len(expired) == _scheduler_batch_limit()}


def send_expiry_claim_reminders():
	"""Daily job — notify purchase managers of active schemes expiring in ≤15 days
	that have zero claim amount (i.e. no achievement entries yet).
	Only sends once per scheme every 7 days to avoid spam.
	"""
	today = nowdate()
	window_end = add_days(today, 15)

	batch_limit = _scheduler_batch_limit()
	expiring = frappe.db.sql("""
		SELECT name, scheme_name, brand, company, valid_to,
		       DATEDIFF(valid_to, %(today)s) as days_left,
		       COALESCE(last_expiry_reminder, '2000-01-01') as last_reminder
		FROM `tabSupplier Scheme Circular`
		WHERE docstatus = 1
		  AND status = 'Active'
		  AND valid_to BETWEEN %(today)s AND %(window_end)s
		  AND (total_claim_amount IS NULL OR total_claim_amount = 0)
		  AND (last_expiry_reminder IS NULL
		       OR last_expiry_reminder < DATE_SUB(CURDATE(), INTERVAL 7 DAY))
		ORDER BY valid_to ASC, name ASC
		LIMIT %(batch_limit)s
	""", {"today": today, "window_end": window_end, "batch_limit": batch_limit}, as_dict=True)

	if not expiring:
		return

	manager_roles = get_role_setting(
		"supplier_scheme_approval_roles",
		("Purchase Manager", "Scheme Manager", "System Manager"),
	)
	by_company = {}
	for scheme in expiring:
		if scheme.company:
			by_company.setdefault(scheme.company, []).append(scheme)

	sent_names = []
	total_recipients = 0
	for company, schemes in by_company.items():
		recipients = get_enabled_role_emails(manager_roles, company=company)
		if not recipients:
			frappe.logger("supplier_scheme").warning(
				f"Expiry reminder skipped for {company}: no scoped manager emails found"
			)
			continue

		rows_html = "".join(
			f"<tr>"
			f"<td style='padding:6px 12px'><a href='{frappe.utils.get_url_to_form('Supplier Scheme Circular', s.name)}'>{s.name}</a></td>"
			f"<td style='padding:6px 12px'>{s.scheme_name or ''}</td>"
			f"<td style='padding:6px 12px'>{s.brand or ''}</td>"
			f"<td style='padding:6px 12px;font-weight:bold;color:#ef4444'>{s.days_left} days</td>"
			f"</tr>"
			for s in schemes
		)
		active_schemes_url = f"{frappe.utils.get_url()}/app/supplier-scheme-circular?status=Active&company={frappe.utils.quoted(company)}"
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

		try:
			frappe.sendmail(
				recipients=recipients,
				subject=_("⚠ {count} Scheme(s) Expiring Soon With No Claims").format(count=len(schemes)),
				message=body,
				delayed=True,
			)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Supplier Scheme: expiry reminder send failed for {company}",
			)
			continue
		sent_names.extend(scheme.name for scheme in schemes)
		total_recipients += len(recipients)

	if sent_names:
		placeholders = ", ".join(["%s"] * len(sent_names))
		frappe.db.sql(
			f"""
				UPDATE `tabSupplier Scheme Circular`
				SET `last_expiry_reminder` = %s
				WHERE `name` IN ({placeholders})
			""",
			(today, *sent_names),
		)
		frappe.logger("supplier_scheme").info(
			f"Sent expiry reminders for {len(sent_names)} schemes to {total_recipients} scoped managers"
		)
	return {
		"reminded": len(sent_names),
		"recipients": total_recipients,
		"has_more": len(expiring) == batch_limit,
	}
