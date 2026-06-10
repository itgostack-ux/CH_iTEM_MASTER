// Copyright (c) 2026, GoStack and contributors
// CH Category — client script


frappe.ui.form.on('CH Category', {
	setup(frm) {
		// Show only leaf-level Item Groups (not parent groups)
		frm.set_query('item_group', () => ({
			filters: { is_group: 0 }
		}));

		// Category Manager: only show users who hold the "CH Category Head" role.
		frm.set_query('category_manager', () => ({
			query: "ch_item_master.ch_item_master.utils.get_users_by_role",
			filters: { role: "CH Category Head" },
		}));
	}

});
