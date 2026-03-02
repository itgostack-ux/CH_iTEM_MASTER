// Copyright (c) 2026, GoStack and contributors
// CH Category — client script

frappe.ui.form.on('CH Category', {
	setup(frm) {
		// Show only leaf-level Item Groups (not parent groups)
		frm.set_query('item_group', () => ({
			filters: { is_group: 0 }
		}));
	}
});
