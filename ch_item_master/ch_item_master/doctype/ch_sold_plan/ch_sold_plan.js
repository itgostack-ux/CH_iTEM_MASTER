// Copyright (c) 2026, GoStack and contributors
// CH Sold Plan — client script

frappe.ui.form.on('CH Sold Plan', {
	setup(frm) {
		frm.set_query('warranty_plan', () => ({
			filters: { status: 'Active' }
		}));

		frm.set_query('item_code', () => ({
			filters: { disabled: 0 }
		}));
	},

	refresh(frm) {
		// Status indicator
		let colors = {
			'Active': 'green',
			'Expired': 'grey',
			'Claimed': 'orange',
			'Void': 'red',
			'Cancelled': 'red'
		};
		if (frm.doc.status && !frm.is_new()) {
			frm.dashboard.set_headline(
				__('<strong>{0}</strong>', [frm.doc.status]),
				colors[frm.doc.status] || 'grey'
			);
		}

		// Claims progress
		if (frm.doc.max_claims && frm.doc.max_claims > 0) {
			let pct = ((frm.doc.claims_used || 0) / frm.doc.max_claims * 100).toFixed(0);
			let color = pct >= 100 ? 'red' : (pct >= 75 ? 'orange' : 'green');
			frm.dashboard.add_comment(
				__('Claims: {0} / {1} ({2}%)',
					[frm.doc.claims_used || 0, frm.doc.max_claims, pct]),
				color, true
			);
		}

		// Void button for warranty managers
		if (frm.doc.docstatus === 1 && frm.doc.status === 'Active') {
			frm.add_custom_button(__('Void Plan'), () => {
				frappe.confirm(
					__('Are you sure you want to void this warranty plan? This cannot be undone.'),
					() => {
						frappe.call({
							method: 'frappe.client.set_value',
							args: {
								doctype: 'CH Sold Plan',
								name: frm.doc.name,
								fieldname: 'status',
								value: 'Void'
							},
							callback: () => frm.reload_doc()
						});
					}
				);
			}, __('Actions'));
		}
	},

	warranty_plan(frm) {
		// Auto-compute end_date from start_date + duration
		if (frm.doc.warranty_plan && frm.doc.start_date) {
			frappe.db.get_value('CH Warranty Plan', frm.doc.warranty_plan,
				'duration_months').then(r => {
				if (r && r.message && r.message.duration_months) {
					let end = frappe.datetime.add_months(
						frm.doc.start_date, r.message.duration_months
					);
					frm.set_value('end_date', end);
				}
			});
		}
	},

	start_date(frm) {
		// Re-compute end_date if plan is set
		if (frm.doc.warranty_plan && frm.doc.start_date) {
			frm.trigger('warranty_plan');
		}
	},
});
