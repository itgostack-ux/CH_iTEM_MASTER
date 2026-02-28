// Copyright (c) 2026, GoStack and contributors
// CH Sub Category — client script

frappe.ui.form.on('CH Sub Category', {

    // ── Client-side UX validation ──────────────────────────────────────
    // Pure local checks that save a round-trip. All business-rule
    // validation lives server-side in ch_sub_category.py via frappe.throw().
    validate(frm) {
        // HSN code must be exactly 6 or 8 digits (India Compliance requirement)
        let hsn = (frm.doc.hsn_code || '').trim();
        if (hsn && hsn.length !== 6 && hsn.length !== 8) {
            frappe.msgprint({
                title: __('Invalid HSN Code'),
                message: __(
                    'HSN/SAC Code <b>{0}</b> must be exactly 6 or 8 digits long '
                    + '(current: {1} digits). India Compliance will reject items '
                    + 'with any other HSN length.',
                    [hsn, hsn.length]
                ),
                indicator: 'red',
            });
            frappe.validated = false;
        }
    },
});

