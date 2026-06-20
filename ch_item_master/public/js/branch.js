frappe.ui.form.on("Branch", {
	setup(frm) {
		frm.set_query("ch_branch_address", () => ({
			filters: { disabled: 0 },
		}));
	},

	refresh(frm) {
		_set_branch_address_preview(frm);
	},

	ch_branch_address(frm) {
		_set_branch_address_preview(frm);
	},
});

function _set_branch_address_preview(frm) {
	if (!frm.doc.ch_branch_address) {
		if (frm.doc.ch_branch_address_display) {
			frm.set_value("ch_branch_address_display", "");
		}
		return;
	}

	frappe.call({
		method: "ch_item_master.ch_core.branch.get_branch_address_display",
		args: { address_name: frm.doc.ch_branch_address },
		callback(r) {
			frm.set_value("ch_branch_address_display", r.message || "");
		},
	});
}
