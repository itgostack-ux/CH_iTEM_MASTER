// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.ui.form.on("CH Exception Request", {
	item_code(frm) {
		if (!frm.doc.item_code || frm.doc.original_value) return;
		// Auto-fetch item name
		if (!frm.doc.item_name) {
			frappe.db.get_value("Item", frm.doc.item_code, "item_name").then(r => {
				if (r.message) frm.set_value("item_name", r.message.item_name);
			});
		}
		// Auto-fetch standard selling price as original_value
		frappe.xcall("ch_item_master.ch_item_master.exception_api.get_item_original_value", {
			item_code: frm.doc.item_code,
			company: frm.doc.company || frappe.defaults.get_default("company"),
		}).then(r => {
			if (r && flt(r) > 0) {
				frm.set_value("original_value", flt(r));
				frappe.show_alert({ message: __("Original value fetched: ₹{0}", [format_number(r)]), indicator: "blue" });
			}
		});
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0 && frm.doc.status === "Pending") {
			frm.add_custom_button(__("Approve"), () => {
				frappe.prompt(
					[
						{
							fieldname: "resolution_value",
							fieldtype: "Currency",
							label: __("Resolution Value"),
							default: frm.doc.requested_value,
						},
						{
							fieldname: "remarks",
							fieldtype: "Small Text",
							label: __("Remarks"),
						},
					],
					(values) => {
						frappe.xcall(
							"ch_item_master.ch_item_master.exception_api.approve_exception",
							{
								exception_name: frm.doc.name,
								resolution_value: values.resolution_value,
								remarks: values.remarks || "",
							}
						).then(() => {
							frappe.show_alert({ message: __("Exception Approved"), indicator: "green" });
							frm.reload_doc();
						});
					},
					__("Approve Exception")
				);
			}).addClass("btn-primary");

			frm.add_custom_button(__("Reject"), () => {
				frappe.prompt(
					{
						fieldname: "remarks",
						fieldtype: "Small Text",
						label: __("Reason for Rejection"),
						reqd: 1,
					},
					(values) => {
						frappe.xcall(
							"ch_item_master.ch_item_master.exception_api.reject_exception",
							{
								exception_name: frm.doc.name,
								reason: values.remarks,
							}
						).then(() => {
							frappe.show_alert({ message: __("Exception Rejected"), indicator: "red" });
							frm.reload_doc();
						});
					},
					__("Reject Exception")
				);
			}).addClass("btn-danger");
		}

		// Show status indicators
		if (frm.doc.status === "Approved") {
			frm.set_intro(__("This exception has been approved by {0}.", [frm.doc.approver_name || frm.doc.approver]), "green");
		} else if (frm.doc.status === "Rejected") {
			frm.set_intro(__("This exception has been rejected."), "red");
		} else if (frm.doc.status === "Expired") {
			frm.set_intro(__("This exception has expired."), "orange");
		} else if (frm.doc.status === "Auto-Approved") {
			frm.set_intro(__("This exception was auto-approved (within policy threshold)."), "blue");
		}
	},
});
