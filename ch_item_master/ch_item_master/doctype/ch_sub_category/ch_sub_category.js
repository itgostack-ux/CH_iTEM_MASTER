// Copyright (c) 2026, GoStack and contributors
// CH Sub Category — client script

frappe.ui.form.on('CH Sub Category', {

    refresh(frm) {
        _sync_all_spec_rows(frm);
        _apply_nature_visibility(frm);
        _show_nature_summary(frm);
    },

    item_nature(frm) {
        _apply_nature_visibility(frm);
        _apply_nature_defaults(frm);
        _show_nature_summary(frm);
    },

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

frappe.ui.form.on('CH Sub Category Spec', {
    spec_type(frm, cdt, cdn) {
        _sync_spec_row_from_type(frm, cdt, cdn);
    },
});

function _apply_nature_visibility(frm) {
    const nature = frm.doc.item_nature || 'Variant Template';
    const non_stock = (nature === 'Service' || nature === 'Subscription');
    // Sections handled by JSON depends_on; fine-tune fields not in those sections.
    frm.toggle_display('include_manufacturer_in_name', !non_stock);
    frm.toggle_display('include_brand_in_name', !non_stock);
    frm.toggle_display('include_model_in_name', !non_stock);
}

function _apply_nature_defaults(frm) {
    const nature = frm.doc.item_nature;
    if (!nature) return;
    const uom_defaults = {
        'Service': 'Hour',
        'Subscription': 'Month',
        'Asset / Capital': 'Nos',
        'Simple Auto-Named': 'Nos',
        'Simple Custom-Named': 'Nos',
        'Variant Template': 'Nos',
    };
    if (!frm.doc.default_uom && uom_defaults[nature]) {
        frm.set_value('default_uom', uom_defaults[nature]);
    }
    if (nature === 'Service' || nature === 'Subscription') {
        frm.set_value('is_stock_item_default', 0);
    } else if (!frm.doc.is_stock_item_default) {
        frm.set_value('is_stock_item_default', 1);
    }
    if (nature === 'Asset / Capital' && !frm.doc.serial_required) {
        frm.set_value('serial_required', 1);
    }
}

function _show_nature_summary(frm) {
    const nature = frm.doc.item_nature;
    if (!nature || frm.is_new()) return;
    const summary = {
        'Variant Template': 'Items will be created as <b>variants</b> from CH Models. Each spec combination = one Item.',
        'Simple Auto-Named': 'Items created with <b>auto-generated names</b>. No variants — one Item per row.',
        'Simple Custom-Named': '<b>User-typed Item Names</b> are preserved. Use for produce, loose goods, deli items.',
        'Service': '<b>Non-stock</b> labour / time-based services. Used by GoFix Service Request.',
        'Subscription': '<b>Non-stock</b> time-bound plans. Used by CH Warranty Plan / VAS Ledger.',
        'Asset / Capital': '<b>Stock items with serial-tracking</b>. Demo units, fixed assets, capex.',
    };
    if (summary[nature] && frm.dashboard) {
        frm.dashboard.clear_headline();
        frm.dashboard.set_headline_alert(summary[nature], 'blue');
    }
}

function _sync_all_spec_rows(frm) {
    (frm.doc.specifications || []).forEach((row) => {
        _sync_spec_row_from_type(frm, row.doctype, row.name, true);
    });
    frm.refresh_field('specifications');
}

function _sync_spec_row_from_type(frm, cdt, cdn, skip_refresh = false) {
    const row = locals[cdt] && locals[cdt][cdn];
    if (!row) return;

    if (row.spec_type === 'Variant') {
        row.is_variant = 1;
        return;
    }

    if (row.spec_type === 'Property') {
        row.is_variant = 0;
        // affects_price, in_item_name, name_order remain as configured by the user.
        if (!skip_refresh) {
            frm.refresh_field('specifications');
        }
        return;
    }

    // Legacy rows where spec_type is blank.
    row.spec_type = row.is_variant ? 'Variant' : 'Property';
    if (!skip_refresh) {
        frm.refresh_field('specifications');
    }
}
