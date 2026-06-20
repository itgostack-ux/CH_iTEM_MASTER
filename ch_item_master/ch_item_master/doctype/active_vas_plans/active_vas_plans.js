// Copyright (c) 2026, GoStack and contributors
// Active VAS Plans — client script

frappe.ui.form.on('Active VAS Plans', {
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

		// External customer-device banner — parity with Oracle Service Contracts
		// "Covered Level = Customer Item Instance" and MS Dynamics Customer Asset
		// "Source = Customer-Owned". Makes it visually obvious that the IMEI on
		// this plan was provided by the customer and is NOT an inventory serial
		// — this plan does not write to any tabSerial No or CH Stock Bin row.
		if (frm.doc.is_external_device) {
			frm.dashboard.add_comment(
				__('External Customer Device — IMEI <b>{0}</b> was provided by the customer ({1}). This plan covers a device that was not sold by us; no inventory serial / stock bin is linked.',
					[frm.doc.serial_no || '—', frm.doc.external_device_source || __('external source')]),
				'blue', true
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
								doctype: 'Active VAS Plans',
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

		// Dynamic field label / description so the cashier looking at this
		// record knows the IMEI's provenance.
		if (frm.fields_dict.serial_no) {
			if (frm.doc.is_external_device) {
				frm.set_df_property('serial_no', 'label', __('Customer Device IMEI'));
				frm.set_df_property('serial_no', 'description',
					__('Customer-provided IMEI captured at sale time. Not bound to any inventory Serial No / Stock Bin.'));
			} else {
				frm.set_df_property('serial_no', 'label', __('Covered Device IMEI / Serial'));
				frm.set_df_property('serial_no', 'description',
					__('Serial / IMEI of the device covered by this plan (links to inventory Serial No).'));
			}
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
