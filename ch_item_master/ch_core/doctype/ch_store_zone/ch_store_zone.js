frappe.ui.form.on('CH Store Zone', {
	setup(frm) {
		frm.set_query('source_warehouse', () => ({
			filters: {
				company: frm.doc.company || undefined,
				is_group: 1,
			},
		}));
	},

	company(frm) {
		// Re-apply when company changes so filter stays in sync
		frm.set_query('source_warehouse', () => ({
			filters: {
				company: frm.doc.company || undefined,
				is_group: 1,
			},
		}));
		if (frm.doc.source_warehouse) {
			frm.set_value('source_warehouse', '');
		}
	},
});
