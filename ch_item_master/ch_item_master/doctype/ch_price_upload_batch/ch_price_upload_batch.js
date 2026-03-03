// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.ui.form.on("CH Price Upload Batch", {
	refresh(frm) {
		// ── Status indicator ──────────────────────────────────────────────
		const indicator_map = {
			'Draft':              'orange',
			'Pending Approval':   'blue',
			'Approved':           'green',
			'Applying':           'yellow',
			'Applied':            'green',
			'Partially Applied':  'orange',
			'Rejected':           'red',
			'Cancelled':          'grey',
		};
		frm.page.set_indicator(frm.doc.status, indicator_map[frm.doc.status] || 'grey');

		// ── Action buttons based on status ───────────────────────────────
		if (frm.doc.status === 'Draft' && !frm.is_new()) {
			frm.add_custom_button(__('Submit for Approval'), () => {
				frappe.confirm(
					__('Submit this batch for manager approval? No prices will change until approved.'),
					() => {
						frm.call('submit_for_approval').then(() => frm.reload_doc());
					}
				);
			}, null).addClass('btn-primary');
		}

		if (frm.doc.status === 'Pending Approval') {
			if (frappe.user_roles.includes('System Manager') || frappe.user_roles.includes('CH Master Manager')) {
				frm.add_custom_button(__('Approve & Apply'), () => {
					frappe.confirm(
						__('Approve this batch? All {0} changes will be applied immediately.', [frm.doc.total_changes]),
						() => {
							frm.call('approve_and_apply').then(() => frm.reload_doc());
						}
					);
				}, null).addClass('btn-primary');

				frm.add_custom_button(__('Reject'), () => {
					const d = new frappe.ui.Dialog({
						title: __('Reject Price Upload'),
						fields: [
							{
								fieldtype: 'Small Text',
								fieldname: 'reason',
								label: __('Rejection Reason'),
								reqd: 1,
							}
						],
						primary_action_label: __('Reject'),
						primary_action(values) {
							d.hide();
							frm.call('reject_batch', { reason: values.reason }).then(() => frm.reload_doc());
						},
					});
					d.show();
				}).addClass('btn-danger');
			}
		}

		// ── Summary cards ────────────────────────────────────────────────
		if (!frm.is_new() && frm.doc.total_changes > 0) {
			const s = frm.doc;
			let html = `<div class="row" style="margin-bottom:10px">`;

			const card = (label, val, color) => `
				<div class="col-sm-3">
					<div style="padding:10px;border-radius:6px;background:var(--bg-light-gray);text-align:center">
						<div style="font-size:22px;font-weight:600;color:var(--${color})">${val}</div>
						<div style="font-size:12px;color:var(--text-muted)">${label}</div>
					</div>
				</div>`;

			html += card('Total Changes', s.total_changes, 'text-color');
			html += card('Selling Prices', s.selling_price_changes, 'blue-500');
			html += card('Buyback Prices', s.buyback_price_changes, 'orange-500');
			html += card('Tags', s.tag_changes, 'green-500');
			html += `</div>`;

			if (['Applied', 'Partially Applied'].includes(s.status)) {
				html += `<div class="row">`;
				html += card('Applied', s.applied_count, 'green-500');
				html += card('Skipped', s.skipped_count, 'text-muted');
				html += card('Errors', s.error_count, 'red-500');
				html += `</div>`;
			}

			frm.set_df_property('items_section', 'description', html);
		}

		// ── Disable editing on non-Draft batches ─────────────────────────
		if (frm.doc.status !== 'Draft') {
			frm.disable_save();
		}
	},
});
