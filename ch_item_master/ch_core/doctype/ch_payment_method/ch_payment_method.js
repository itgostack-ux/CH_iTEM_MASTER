// Copyright (c) 2026, Congruence Holdings and contributors
// For license information, please see license.txt

frappe.ui.form.on("CH Payment Method", {
    method_type(frm) {
        // Auto-toggle requirement checkboxes based on type
        if (frm.doc.method_type === "Bank") {
            frm.set_value("requires_bank_details", 1);
            frm.set_value("requires_upi_id", 0);
        } else if (frm.doc.method_type === "UPI") {
            frm.set_value("requires_upi_id", 1);
            frm.set_value("requires_bank_details", 0);
        } else if (frm.doc.method_type === "Cash") {
            frm.set_value("requires_bank_details", 0);
            frm.set_value("requires_upi_id", 0);
        }
    },
});
