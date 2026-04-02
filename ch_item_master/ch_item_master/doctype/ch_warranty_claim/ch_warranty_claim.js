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
	const s = frm.doc.claim_status;

	// ── Approve / Reject / Need More Info (Pending Approval) ──
	if (s === "Pending Approval" || s === "Need More Information") {
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

		frm.add_custom_button(__("Need More Info"), () => {
			frappe.prompt(
				{ fieldtype: "Small Text", fieldname: "remarks", label: "What info is needed?", reqd: 1 },
				(values) => {
					frm.call("need_more_info", { remarks: values.remarks }).then(() => frm.reload_doc());
				},
				__("Request More Information")
			);
		}, __("Actions"));
	}

	// ── Receive Device (walk-in Approved or Picked Up) ──
	if ((s === "Approved" && !frm.doc.pickup_required) || s === "Picked Up") {
		frm.add_custom_button(__("Receive Device"), () => {
			frappe.prompt(
				[
					{ fieldtype: "Select", fieldname: "condition_on_receipt", label: __("Device Condition"),
					  options: "\nGood\nMinor Damage\nMajor Damage\nWrong Device\nEmpty Parcel", reqd: 1 },
					{ fieldtype: "Small Text", fieldname: "accessories_received", label: __("Accessories Received") },
					{ fieldtype: "Check", fieldname: "imei_verified", label: __("IMEI Verified"), default: 1 },
					{ fieldtype: "Small Text", fieldname: "receiving_remarks", label: __("Remarks") },
				],
				(values) => {
					frm.call("mark_device_received", values).then(() => frm.reload_doc());
				},
				__("Receive Device")
			);
		}, __("Actions")).addClass("btn-primary");
	}

	// ── Perform Intake QC (Device Received / QC Pending) ──
	if (["Device Received", "QC Pending"].includes(s)) {
		frm.add_custom_button(__("Perform Intake QC"), () => {
			frappe.prompt(
				[
					{ fieldtype: "Select", fieldname: "qc_result", label: __("QC Result"),
					  options: "\nPassed\nFailed\nNot Repairable", reqd: 1 },
					{ fieldtype: "Small Text", fieldname: "qc_remarks", label: __("QC Remarks") },
					{ fieldtype: "Small Text", fieldname: "qc_result_reason", label: __("Reason (if failed)") },
				],
				(values) => {
					frm.call("perform_intake_qc", values).then(() => frm.reload_doc());
				},
				__("Intake QC")
			);
		}, __("Actions")).addClass("btn-primary");
	}

	// ── Generate Processing Fee (QC Passed, fee not yet set) ──
	if (s === "QC Passed" && !frm.doc.processing_fee_amount) {
		frm.add_custom_button(__("Generate Fee"), () => {
			frappe.prompt(
				[{ fieldtype: "Currency", fieldname: "fee_amount", label: __("Fee Amount (override)"),
				   description: __("Leave blank for auto-calculation") }],
				(values) => {
					frm.call("generate_processing_fee", { fee_amount: values.fee_amount || null }).then(() => frm.reload_doc());
				},
				__("Generate Processing Fee")
			);
		}, __("Actions")).addClass("btn-warning");
	}

	// ── Fee collection actions (Fee Pending / Link Sent) ──
	if (["Fee Pending", "QC Passed"].includes(s)
		&& flt(frm.doc.processing_fee_amount) > 0
		&& !["Paid", "Waived", "Not Required"].includes(frm.doc.processing_fee_status)) {

		frm.add_custom_button(__("Collect Fee"), () => {
			frappe.prompt(
				[
					{ fieldtype: "Currency", fieldname: "paid_amount", label: __("Amount Paid"), reqd: 1,
					  default: frm.doc.processing_fee_amount },
					{ fieldtype: "Select", fieldname: "payment_mode", label: __("Payment Mode"),
					  options: "\nCash\nUPI\nCard\nOnline\nNEFT", reqd: 1 },
					{ fieldtype: "Data", fieldname: "payment_ref", label: __("Transaction Ref") },
					{ fieldtype: "Small Text", fieldname: "remarks", label: __("Remarks") },
				],
				(values) => {
					frm.call("mark_fee_paid", values).then(() => frm.reload_doc());
				},
				__("Collect Processing Fee")
			);
		}, __("Fee")).addClass("btn-success");

		frm.add_custom_button(__("Send Payment Link"), () => {
			frappe.prompt(
				[{ fieldtype: "Select", fieldname: "channel", label: __("Send Via"),
				   options: "WhatsApp\nSMS", default: "WhatsApp", reqd: 1 }],
				(values) => {
					frm.call("send_fee_payment_link", values).then(() => frm.reload_doc());
				},
				__("Send Fee Link")
			);
		}, __("Fee"));

		frm.add_custom_button(__("Waive Fee"), () => {
			frappe.prompt(
				[
					{ fieldtype: "Small Text", fieldname: "waiver_reason", label: __("Waiver Reason"), reqd: 1 },
					{ fieldtype: "Currency", fieldname: "waived_amount", label: __("Amount to Waive"),
					  description: __("Leave blank to waive full amount") },
				],
				(values) => {
					frm.call("waive_processing_fee", values).then(() => frm.reload_doc());
				},
				__("Waive Processing Fee")
			);
		}, __("Fee")).addClass("btn-danger");
	}

	// ── Create GoFix Ticket (all gates passed, no ticket yet) ──
	if (["Fee Paid", "Fee Waived"].includes(s)
		|| (s === "QC Passed" && frm.doc.processing_fee_status === "Not Required")) {
		if (!frm.doc.service_request) {
			frm.add_custom_button(__("Create GoFix Ticket"), () => {
				frappe.confirm(__("Create GoFix repair ticket for this claim?"), () => {
					frm.call("create_repair_ticket", {}).then(() => frm.reload_doc());
				});
			}, __("Actions")).addClass("btn-primary");
		}
	}

	// ── Mark Repair Complete (for GoFix) ──
	if (["Ticket Created", "In Repair"].includes(s)) {
		frm.add_custom_button(__("Mark Repair Complete"), () => {
			frappe.prompt(
				{ fieldtype: "Small Text", fieldname: "remarks", label: "Completion Remarks" },
				(values) => {
					frm.call("mark_repair_complete", { remarks: values.remarks }).then(() => frm.reload_doc());
				},
				__("Complete Repair")
			);
		}, __("Actions")).addClass("btn-success");

		frm.add_custom_button(__("Additional Cost Approval"), () => {
			frappe.prompt(
				[
					{ fieldtype: "Small Text", fieldname: "additional_issue_description",
					  label: __("Additional Issue Found"), reqd: 1 },
					{ fieldtype: "Currency", fieldname: "additional_cost_estimated",
					  label: __("Estimated Additional Cost"), reqd: 1 },
				],
				(values) => {
					frm.call("request_additional_approval", values).then(() => frm.reload_doc());
				},
				__("Request Customer Approval for Additional Cost")
			);
		}, __("Actions")).addClass("btn-warning");
	}

	// ── Resolve Additional Approval ──
	if (s === "Additional Approval Pending") {
		frm.add_custom_button(__("Customer Approved"), () => {
			frappe.prompt(
				{ fieldtype: "Small Text", fieldname: "remarks", label: __("Remarks") },
				(values) => {
					frm.call("resolve_additional_approval", { decision: "Approved", remarks: values.remarks })
						.then(() => frm.reload_doc());
				},
				__("Confirm Customer Approval")
			);
		}, __("Actions")).addClass("btn-success");

		frm.add_custom_button(__("Customer Rejected"), () => {
			frappe.prompt(
				{ fieldtype: "Small Text", fieldname: "remarks", label: __("Remarks") },
				(values) => {
					frm.call("resolve_additional_approval", { decision: "Rejected", remarks: values.remarks })
						.then(() => frm.reload_doc());
				},
				__("Confirm Customer Rejection")
			);
		}, __("Actions")).addClass("btn-danger");
	}

	// ── Final QC (after repair) ──
	if (["Repair Complete", "Final QC Pending"].includes(s)) {
		frm.add_custom_button(__("Final QC"), () => {
			frappe.prompt(
				[
					{ fieldtype: "Select", fieldname: "qc_result", label: __("Final QC Result"),
					  options: "\nPassed\nFailed", reqd: 1 },
					{ fieldtype: "Small Text", fieldname: "qc_remarks", label: __("QC Remarks") },
				],
				(values) => {
					frm.call("perform_final_qc", values).then(() => frm.reload_doc());
				},
				__("Final QC After Repair")
			);
		}, __("Actions")).addClass("btn-primary");
	}

	// ── Close Claim ──
	if (["Repair Complete", "Approved", "Rejected", "Delivered", "QC Failed",
		 "Final QC Passed", "Payment Received", "Not Repairable"].includes(s)) {
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
