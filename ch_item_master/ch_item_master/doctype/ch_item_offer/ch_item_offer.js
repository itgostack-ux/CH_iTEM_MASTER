// Copyright (c) 2026, GoStack and contributors
// CH Item Offer — client script

frappe.ui.form.on('CH Item Offer', {
	setup(frm) {
		frm.set_query('item_code', () => ({
			filters: { disabled: 0 }
		}));

		frm.set_query('channel', () => ({
			filters: { disabled: 0 }
		}));

		frm.set_query('target_item_group', () => ({
			filters: { is_group: 0 }
		}));
	},

	refresh(frm) {
		// Status indicators
		let status_colors = {
			'Active': 'green', 'Scheduled': 'blue', 'Expired': 'grey',
			'Draft': 'orange', 'Cancelled': 'red',
		};
		let color = status_colors[frm.doc.status] || 'grey';
		if (frm.doc.status && !frm.is_new()) {
			frm.dashboard.set_headline(
				__('Status: <strong>{0}</strong> | Approval: <strong>{1}</strong>',
					[frm.doc.status, frm.doc.approval_status || 'Pending Approval']),
				color
			);
		}

		// Approval buttons
		if (!frm.is_new() && frm.doc.approval_status === 'Pending Approval') {
			frm.add_custom_button(__('Approve'), () => {
				frappe.confirm(
					__('Approve this offer? A Pricing Rule will be created in ERPNext.'),
					() => frm.call('approve').then(() => frm.reload_doc())
				);
			}, __('Actions'));

			frm.add_custom_button(__('Reject'), () => {
				frm.call('reject').then(() => frm.reload_doc());
			}, __('Actions'));
		}

		// Quick expire button
		if (frm.doc.status === 'Active' && !frm.is_new()) {
			frm.add_custom_button(__('Expire Now'), () => {
				frappe.confirm(
					__('Expire this offer now?'),
					() => {
						frm.set_value('end_date', frappe.datetime.now_datetime());
						frm.set_value('status', 'Expired');
						frm.save();
					}
				);
			}, __('Actions'));
		}

		// View linked Pricing Rule
		if (frm.doc.erp_pricing_rule) {
			frm.add_custom_button(__('View Pricing Rule'), () => {
				frappe.set_route('Form', 'Pricing Rule', frm.doc.erp_pricing_rule);
			}, __('View'));
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

	offer_level(frm) {
		// Clear item-level fields when switching to Bill
		if (frm.doc.offer_level === 'Bill') {
			frm.set_value('item_code', '');
			frm.set_value('item_name', '');
			frm.set_value('target_item_group', '');
			frm.set_value('target_brand', '');
		}
	},

	value_type(frm) {
		// If switching to Percentage, cap at 100
		if (frm.doc.value_type === 'Percentage' && frm.doc.value > 100) {
			frm.set_value('value', 100);
			frappe.show_alert({message: __('Value capped at 100%'), indicator: 'orange'});
		}
	},
});
