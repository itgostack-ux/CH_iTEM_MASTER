frappe.ui.form.on('CH Store Zone', {
	setup(frm) {
		frm.set_query('source_warehouse', () => ({
			query: 'ch_item_master.ch_core.location_hierarchy.hub_warehouse_query',
			filters: {
				company: frm.doc.company || undefined,
				city: frm.doc.city || undefined,
				zone: frm.doc.name || undefined,
			},
		}));
	},

	company(frm) {
		// Re-apply when company changes so filter stays in sync
		frm.set_query('source_warehouse', () => ({
			query: 'ch_item_master.ch_core.location_hierarchy.hub_warehouse_query',
			filters: {
				company: frm.doc.company || undefined,
				city: frm.doc.city || undefined,
				zone: frm.doc.name || undefined,
			},
		}));
		if (frm.doc.source_warehouse) {
			frm.set_value('source_warehouse', '');
		}
	},

	city(frm) {
		if (frm.doc.source_warehouse) {
			frm.set_value('source_warehouse', '');
		}
	},
});
