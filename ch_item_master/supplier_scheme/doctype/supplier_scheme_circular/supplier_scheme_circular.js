frappe.ui.form.on("Supplier Scheme Circular", {
	refresh(frm) {
		// Status indicator colour
		const status_color = {
			"Draft": "gray",
			"Pending Approval": "orange",
			"Active": "green",
			"Closed": "blue",
			"Cancelled": "red",
		};
		const color = status_color[frm.doc.status] || "gray";
		frm.page.set_indicator(frm.doc.status, color);

		// ---------- MAKER: Submit for Review ----------
		if (frm.doc.docstatus === 0 && frm.doc.status === "Draft" && !frm.is_new()) {
			frm.add_custom_button(__("Submit for Review"), () => {
				frappe.confirm(
					__("Submit this scheme for manager approval? You won't be able to edit it until it is reviewed."),
					() => frm.call("submit_for_review").then(() => frm.reload_doc())
				);
			}).addClass("btn-warning");
		}

		// ---------- CHECKER: Approve / Reject ----------
		const is_approver = frappe.user.has_role(["Purchase Manager", "Scheme Manager", "System Manager"]);
		if (frm.doc.docstatus === 0 && frm.doc.status === "Pending Approval" && is_approver) {
			frm.add_custom_button(__("Approve"), () => {
				frappe.confirm(
					__("Approve and activate this scheme? This cannot be undone without cancellation."),
					() => frm.call("approve_scheme").then(() => frm.reload_doc())
				);
			}, __("Review Actions")).addClass("btn-success");

			frm.add_custom_button(__("Reject"), () => {
				frappe.prompt(
					[{
						fieldname: "reason",
						fieldtype: "Small Text",
						label: __("Rejection Reason"),
						reqd: 1,
						description: __("This will be stored on the scheme and visible to the team."),
					}],
					({ reason }) => {
						frm.call("reject_scheme", { reason }).then(() => frm.reload_doc());
					},
					__("Reject Scheme"),
					__("Reject")
				);
			}, __("Review Actions")).addClass("btn-danger");
		}

		// ---------- Upload Scheme Document (Draft only) ----------
		if (frm.doc.docstatus === 0 && frm.doc.status === "Draft" && !frm.is_new()) {
			frm.add_custom_button(__("Upload Scheme Document"), () => {
				frappe.new_doc("Scheme Document Upload");
			});
		}

		// Make review fields read-only for non-approvers
		if (!is_approver) {
			frm.set_df_property("review_notes", "read_only", 1);
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

