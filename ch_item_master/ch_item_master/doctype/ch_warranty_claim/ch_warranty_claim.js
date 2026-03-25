// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.ui.form.on("CH Warranty Claim", {
	refresh(frm) {
		_setup_action_buttons(frm);
		_setup_dashboard(frm);
	},

	serial_no(frm) {
		if (!frm.doc.serial_no) return;

		// Auto-lookup device + warranty info from serial
		frappe.call({
			method: "ch_item_master.ch_item_master.warranty_api.check_warranty",
			args: { serial_no: frm.doc.serial_no, company: frm.doc.company },
			callback(r) {
				if (!r.message) return;
				let data = r.message;

				// Populate device info from lifecycle
				if (data.serial_lifecycle) {
					let lc = data.serial_lifecycle;
					frm.set_value("item_code", lc.item_code || "");
					frm.set_value("imei_number", lc.imei_number || "");
					frm.set_value("customer", lc.customer || "");
					frm.set_value("customer_name", lc.customer_name || "");
				}

				// Populate warranty info
				frm.set_value("warranty_status", data.warranty_status || "No Warranty");
				if (data.covering_plan) {
					let plan = data.covering_plan;
					frm.set_value("sold_plan", plan.name || "");
					frm.set_value("warranty_plan", plan.warranty_plan || "");
					frm.set_value("plan_type", plan.plan_type || "");
					frm.set_value("warranty_start_date", plan.start_date || "");
					frm.set_value("warranty_end_date", plan.end_date || "");
					frm.set_value("claims_used", plan.claims_used || 0);
					frm.set_value("max_claims", plan.max_claims || 0);
					frm.set_value("deductible_amount", plan.deductible_amount || 0);
				}

				// Set coverage type indicator
				if (data.warranty_covered) {
					frm.dashboard.set_headline_alert(
						`<span class="indicator-pill green">Under Warranty — ${data.covering_plan?.plan_type || ""}</span>`
					);
				} else {
					frm.dashboard.set_headline_alert(
						`<span class="indicator-pill red">${data.warranty_status || "No Warranty"}</span>`
					);
				}
			}
		});
	}
});


function _setup_action_buttons(frm) {
	if (frm.doc.docstatus !== 1) return;

	// Approve / Reject buttons (for GoGizmo Head)
	if (frm.doc.claim_status === "Pending Approval") {
		frm.add_custom_button(__("Approve"), () => {
			const gogizmo_amt = frm.doc.gogizmo_share || 0;
			frappe.prompt(
				[
					{
						fieldtype: "Currency",
						fieldname: "approved_amount",
						label: __("Approved Amount (GoGizmo Pays)"),
						default: gogizmo_amt,
						description: __("Estimated: ₹{0}. Reduce for partial approval — difference shifts to customer.", [gogizmo_amt]),
						reqd: 1,
					},
					{
						fieldtype: "Small Text",
						fieldname: "remarks",
						label: __("Approval Remarks"),
					},
				],
				(values) => {
					frm.call("approve", {
						remarks: values.remarks,
						approved_amount: values.approved_amount,
					}).then(() => frm.reload_doc());
				},
				__("Approve Warranty Claim")
			);
		}, __("Actions")).addClass("btn-primary");

		frm.add_custom_button(__("Reject"), () => {
			frappe.prompt(
				{ fieldtype: "Small Text", fieldname: "reason", label: "Rejection Reason", reqd: 1 },
				(values) => {
					frm.call("reject", { reason: values.reason }).then(() => frm.reload_doc());
				},
				__("Reject Warranty Claim")
			);
		}, __("Actions")).addClass("btn-danger");
	}

	// Mark Repair Complete (for GoFix)
	if (["Ticket Created", "In Repair"].includes(frm.doc.claim_status)) {
		frm.add_custom_button(__("Mark Repair Complete"), () => {
			frappe.prompt(
				{ fieldtype: "Small Text", fieldname: "remarks", label: "Completion Remarks" },
				(values) => {
					frm.call("mark_repair_complete", { remarks: values.remarks }).then(() => frm.reload_doc());
				},
				__("Complete Repair")
			);
		}, __("Actions")).addClass("btn-success");
	}

	// Close Claim
	if (["Repair Complete", "Approved", "Rejected"].includes(frm.doc.claim_status)) {
		frm.add_custom_button(__("Close Claim"), () => {
			frappe.prompt(
				{ fieldtype: "Small Text", fieldname: "remarks", label: "Closing Remarks" },
				(values) => {
					frm.call("close_claim", { remarks: values.remarks }).then(() => frm.reload_doc());
				},
				__("Close Warranty Claim")
			);
		}, __("Actions"));
	}
}


function _setup_dashboard(frm) {
	if (!frm.doc.service_request) return;

	frm.dashboard.add_indicator(
		__("GoFix Ticket: {0}", [frm.doc.service_request]),
		frm.doc.repair_status === "Completed" ? "green" : "orange"
	);
}
