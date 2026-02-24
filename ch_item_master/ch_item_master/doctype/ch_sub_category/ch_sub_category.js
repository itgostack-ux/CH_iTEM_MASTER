// Copyright (c) 2026, GoStack and contributors
// CH Sub Category — client script

frappe.ui.form.on('CH Sub Category', {

    // ── Client-side validation ─────────────────────────────────────────────
    // frappe.throw() from the server only produces a beep in some versions
    // without showing a visible dialog.  Validate critical fields here so the
    // user always gets a clear, readable error message.
    validate(frm) {
        let errors = [];

        // HSN code must be exactly 6 or 8 digits (India Compliance requirement)
        let hsn = (frm.doc.hsn_code || '').trim();
        if (hsn && hsn.length !== 6 && hsn.length !== 8) {
            errors.push(__(
                'HSN/SAC Code <b>{0}</b> must be exactly 6 or 8 digits long '
                + '(current: {1} digits). India Compliance will reject items '
                + 'with any other HSN length.',
                [hsn, hsn.length]
            ));
        }

        if (errors.length) {
            frappe.msgprint({
                title: __('Invalid Fields'),
                message: '<ul><li>' + errors.join('</li><li>') + '</li></ul>',
                indicator: 'red',
            });
            frappe.validated = false;
        }
    },
});
