// Copyright (c) 2026, GoStack and contributors
// CH Item Price — client script

frappe.ui.form.on('CH Item Price', {
	setup(frm) {
		// Filter item_code to only show items (not disabled)
		frm.set_query('item_code', () => ({
			filters: { disabled: 0 }
		}));

		// Filter channel to active channels only
		frm.set_query('channel', () => ({
			filters: { is_active: 1 }
		}));
	},

	refresh(frm) {
		// Status indicator
		if (frm.doc.status === 'Active') {
			frm.dashboard.set_headline(
				__('This price is currently <strong>Active</strong>'),
				'green'
			);
		} else if (frm.doc.status === 'Scheduled') {
			frm.dashboard.set_headline(
				__('This price is <strong>Scheduled</strong> — starts {0}',
					[frappe.datetime.str_to_user(frm.doc.effective_from)]),
				'blue'
			);
		} else if (frm.doc.status === 'Expired') {
			frm.dashboard.set_headline(
				__('This price has <strong>Expired</strong>'),
				'grey'
			);
		}

		// Approval buttons — only show for Draft prices
		if (frm.doc.status === 'Draft' && !frm.is_new()) {
			frm.add_custom_button(__('Approve'), () => {
				frappe.confirm(
					__('Approve this price record? It will be synced to ERPNext immediately.'),
					() => {
						frm.call('approve').then(() => frm.reload_doc());
					}
				);
			}, __('Actions'));

			frm.add_custom_button(__('Reject'), () => {
				frm.call('reject').then(() => frm.reload_doc());
			}, __('Actions'));
		}

		// Quick expire button for active prices
		if (frm.doc.status === 'Active' && !frm.is_new()) {
			frm.add_custom_button(__('Expire Now'), () => {
				frappe.confirm(
					__('Set this price to Expired? It will stop applying to transactions.'),
					() => {
						frm.set_value('effective_to', frappe.datetime.nowdate());
						frm.set_value('status', 'Expired');
						frm.save();
					}
				);
			}, __('Actions'));
		}

		// Clone button
		if (!frm.is_new()) {
			frm.add_custom_button(__('Clone Price'), () => {
				let new_doc = frappe.model.copy_doc(frm.doc);
				new_doc.status = 'Draft';
				new_doc.approved_by = '';
				new_doc.approved_at = '';
				new_doc.erp_item_price = '';
				new_doc.effective_from = frappe.datetime.nowdate();
				new_doc.effective_to = '';
				frappe.set_route('Form', 'CH Item Price', new_doc.name);
			}, __('Actions'));
		}
	},

	item_code(frm) {
		// Auto-fetch item_name
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

	mrp(frm) {
		_suggest_selling_price(frm);
	},

	mop(frm) {
		_suggest_selling_price(frm);
	},
});

function _suggest_selling_price(frm) {
	// If selling_price is empty and MOP is set, default to MOP
	if (!frm.doc.selling_price && frm.doc.mop) {
		frm.set_value('selling_price', frm.doc.mop);
	}
}
