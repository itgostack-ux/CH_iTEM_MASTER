frappe.ui.form.on("Supplier Scheme Circular", {
	refresh(frm) {
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			// Show upload button on draft circulars too
			frm.add_custom_button(__("Upload Scheme Document"), () => {
				frappe.new_doc("Scheme Document Upload");
			});
		}
	},

	validate(frm) {
		// Give clear error messages instead of silent beeps
		if (!frm.doc.rules || !frm.doc.rules.length) {
			frappe.msgprint({
				title: __("Missing Rules"),
				message: __("Please add at least one Scheme Rule in the 'Scheme Rules' section below."),
				indicator: "orange",
			});
			frappe.validated = false;
			// Scroll to rules section
			frm.scroll_to_field("rules");
			return;
		}

		// Check each rule row has required fields
		for (let i = 0; i < frm.doc.rules.length; i++) {
			const rule = frm.doc.rules[i];
			if (!rule.rule_name) {
				frappe.msgprint({
					title: __("Incomplete Rule"),
					message: __("Row {0}: Rule Name is required in the Scheme Rules table.", [i + 1]),
					indicator: "orange",
				});
				frappe.validated = false;
				frm.scroll_to_field("rules");
				return;
			}
		}
	},
});

frappe.ui.form.on("Supplier Scheme Rule", {
	rules_add(frm, cdt, cdn) {
		// Set sensible defaults for new rule rows
		const row = locals[cdt][cdn];
		if (!row.rule_type) row.rule_type = "Quantity Slab";
		if (!row.payout_basis) row.payout_basis = "Per Unit";
		if (!row.achievement_basis) row.achievement_basis = "Invoice Date";
		frm.refresh_field("rules");
	},
});
