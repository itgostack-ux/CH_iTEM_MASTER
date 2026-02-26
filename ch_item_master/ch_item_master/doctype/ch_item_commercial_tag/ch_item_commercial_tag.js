// Copyright (c) 2026, GoStack and contributors
// CH Item Commercial Tag — client script

frappe.ui.form.on('CH Item Commercial Tag', {
	setup(frm) {
		frm.set_query('item_code', () => ({
			filters: { disabled: 0 }
		}));
	},

	refresh(frm) {
		// Status indicator
		let colors = { 'Active': 'green', 'Expired': 'grey' };
		if (frm.doc.status && !frm.is_new()) {
			let msg = frm.doc.status === 'Active'
				? __('Tag is <strong>Active</strong>')
				: __('Tag has <strong>Expired</strong>');
			frm.dashboard.set_headline(msg, colors[frm.doc.status] || 'grey');
		}

		// Quick expire button
		if (frm.doc.status === 'Active' && !frm.is_new()) {
			frm.add_custom_button(__('Expire Now'), () => {
				frm.set_value('effective_to', frappe.datetime.nowdate());
				frm.set_value('status', 'Expired');
				frm.save();
			});
		}

		// Quick re-activate button for expired tags (extend end date)
		if (frm.doc.status === 'Expired' && !frm.is_new()) {
			frm.add_custom_button(__('Re-activate'), () => {
				let d = new frappe.ui.Dialog({
					title: __('Re-activate Tag'),
					fields: [
						{
							fieldname: 'new_end_date',
							fieldtype: 'Date',
							label: __('New Effective To'),
							default: frappe.datetime.add_days(frappe.datetime.nowdate(), 30),
							reqd: 1,
						}
					],
					primary_action_label: __('Re-activate'),
					primary_action(values) {
						frm.set_value('effective_to', values.new_end_date);
						frm.set_value('status', 'Active');
						frm.save();
						d.hide();
					}
				});
				d.show();
			});
		}
	},

	item_code(frm) {
		if (frm.doc.item_code) {
			frappe.db.get_value('Item', frm.doc.item_code, 'item_name').then(r => {
				if (r && r.message) {
					frm.set_value('item_name', r.message.item_name);
				}
			});
		} else {
			frm.set_value('item_name', '');
		}
	},

	tag(frm) {
		// Set a sensible default reason based on tag type
		if (frm.doc.tag && !frm.doc.reason) {
			let reasons = {
				'EOL': 'End of Life — product discontinued by manufacturer',
				'NEW': 'Newly launched product',
				'PROMO FOCUS': 'Promotional focus item for current campaign',
				'RESTRICTED': 'Restricted availability',
			};
			if (reasons[frm.doc.tag]) {
				frm.set_value('reason', reasons[frm.doc.tag]);
			}
		}
	},
});
