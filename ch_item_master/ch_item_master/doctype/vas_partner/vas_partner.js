frappe.ui.form.on("VAS Partner", {
	refresh(frm) {
		if (frm.doc.status === "Inactive") {
			frm.dashboard.set_headline_alert(__("Partner is inactive"), "orange");
		}
	},
});
