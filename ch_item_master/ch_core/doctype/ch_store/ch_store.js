// Copyright (c) 2026, Congruence Holdings and contributors
// For license information, please see license.txt

frappe.ui.form.on("CH Store", {
    refresh(frm) {
        // Show store capabilities badge
        if (frm.doc.is_buyback_enabled) {
            frm.dashboard.set_headline_alert(
                '<span class="indicator green">Buyback Enabled</span>'
            );
        }
    },
});
