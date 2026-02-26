// Copyright (c) 2026, GoStack and contributors
// CH Serial Lifecycle — Client Script

frappe.ui.form.on("CH Serial Lifecycle", {
	refresh(frm) {
		if (!frm.is_new()) {
			render_lifecycle_timeline(frm);
			add_transition_buttons(frm);
			add_scan_button(frm);
		}
	},
});

function render_lifecycle_timeline(frm) {
	// Visual status pipeline
	const statuses = [
		"Received", "In Stock", "Displayed", "Sold",
		"Returned", "In Service", "Refurbished", "Buyback", "Scrapped"
	];
	const colors = {
		Received: "#3182ce", "In Stock": "#38a169", Displayed: "#00b5d8",
		Sold: "#805ad5", Returned: "#dd6b20", "In Service": "#d69e2e",
		Refurbished: "#63b3ed", Buyback: "#ed64a6", Scrapped: "#e53e3e",
		Lost: "#e53e3e",
	};

	let current = frm.doc.lifecycle_status;
	let pipeline = statuses.map(s => {
		let is_current = s === current;
		let bg = is_current ? colors[s] : "#e2e8f0";
		let color = is_current ? "#fff" : "#718096";
		return `<span style="display:inline-block;padding:4px 10px;margin:2px;
			border-radius:12px;font-size:11px;font-weight:${is_current ? 700 : 400};
			background:${bg};color:${color}">${s}</span>`;
	}).join(" → ");

	frm.set_df_property("section_status", "description", pipeline);
}

function add_transition_buttons(frm) {
	const VALID_TRANSITIONS = {
		"Received": ["In Stock", "Returned", "Scrapped"],
		"In Stock": ["Displayed", "Sold", "In Service", "Scrapped", "Lost"],
		"Displayed": ["In Stock", "Sold", "Scrapped", "Lost"],
		"Sold": ["Returned", "In Service"],
		"Returned": ["In Stock", "In Service", "Refurbished", "Buyback", "Scrapped"],
		"In Service": ["In Stock", "Refurbished", "Scrapped", "Returned"],
		"Refurbished": ["In Stock", "Buyback", "Scrapped"],
		"Buyback": ["In Stock", "Refurbished", "Scrapped"],
	};

	let allowed = VALID_TRANSITIONS[frm.doc.lifecycle_status] || [];
	allowed.forEach(status => {
		frm.add_custom_button(__(status), () => {
			frappe.prompt([
				{
					fieldname: "remarks",
					fieldtype: "Small Text",
					label: __("Remarks (optional)"),
				},
			], (values) => {
				frappe.call({
					method: "ch_item_master.ch_item_master.doctype.ch_serial_lifecycle.ch_serial_lifecycle.update_lifecycle_status",
					args: {
						serial_no: frm.doc.name,
						new_status: status,
						company: frm.doc.current_company,
						warehouse: frm.doc.current_warehouse,
						remarks: values.remarks,
					},
					callback() {
						frm.reload_doc();
						frappe.show_alert({
							message: __("Status changed to {0}", [status]),
							indicator: "green",
						});
					},
				});
			}, __("Move to " + status), __("Confirm"));
		}, __("Change Status"));
	});
}

function add_scan_button(frm) {
	frm.add_custom_button(__("Scan IMEI / Serial"), () => {
		frappe.prompt([
			{
				fieldname: "serial_input",
				fieldtype: "Data",
				label: __("Enter Serial / IMEI Number"),
				reqd: 1,
			},
		], (values) => {
			frappe.call({
				method: "ch_item_master.ch_item_master.doctype.ch_serial_lifecycle.ch_serial_lifecycle.scan_serial",
				args: { serial_no: values.serial_input },
				callback(r) {
					if (r.message) {
						frappe.set_route("Form", "CH Serial Lifecycle", r.message.serial_no);
					}
				},
			});
		}, __("Scan Serial"), __("Lookup"));
	}, __("Tools"));
}
