// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt
// CH Approval Delegation — client script

frappe.ui.form.on("CH Approval Delegation", {
	setup(frm) {
		// Both ends of a delegation must be CH Master Approvers — only people
		// who can approve exceptions can delegate or receive that authority.
		const approver_query = () => ({
			query: "ch_item_master.ch_item_master.utils.get_users_by_role",
			filters: { role: "CH Master Approver" },
		});
		frm.set_query("delegator", approver_query);
		frm.set_query("delegate", approver_query);
	},
});
