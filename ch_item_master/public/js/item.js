// Copyright (c) 2026, GoStack and contributors
// CH Item Master — Item form client script
//
// Flow (uses ERPNext's native variant system):
//   1. User selects CH Model — auto-fills category, sub-category, item group,
//      HSN, and sets has_variants=1 with specs as Item Attributes
//   2. User fills all other tabs (inventory, accounting, sales, tax, etc.)
//   3. Saves → this becomes a TEMPLATE item
//   4. ERPNext's native "Create > Multiple Variants" button appears
//   5. User picks attribute values → ERPNext creates variants that inherit
//      everything from the template

frappe.ui.form.on('Item', {

    setup(frm) {
        // Model is the primary field — show brand and sub_category in search results
        frm.set_query('ch_model', () => {
            let filters = { disabled: 0 };
            if (frm.doc.ch_sub_category) {
                filters.sub_category = frm.doc.ch_sub_category;
            }
            return {
                filters,
                // Include brand in the search description so the user can
                // distinguish "Galaxy S25 (Samsung)" from "Galaxy S25 (Other Brand)"
                query: 'ch_item_master.ch_item_master.api.search_models',
            };
        });

        frm.set_query('ch_sub_category', () => ({
            filters: frm.doc.ch_category
                ? { category: frm.doc.ch_category } : {},
        }));

        // Filter spec dropdown in ch_spec_values to only property/attribute specs
        // (excludes variant-driving specs: is_variant=1 AND in_item_name=1)
        frm.set_query('spec', 'ch_spec_values', () => ({
            query: 'ch_item_master.ch_item_master.api.search_specs_for_sub_category',
            filters: { sub_category: frm.doc.ch_sub_category || '', exclude_variant_selectors: 1 },
        }));

        // Model Features (read-only on Item, but set filters for consistency)
        frm.set_query('feature_group', 'ch_model_features', () => ({
            filters: { disabled: 0 },
        }));
        frm.set_query('feature_name', 'ch_model_features', (doc, cdt, cdn) => {
            let row = locals[cdt][cdn];
            return { filters: { feature_group: row.feature_group || '', disabled: 0 } };
        });

        // Override ERPNext's Multiple Variants dialog to show only
        // model-allowed values instead of ALL Item Attribute Values
        _override_multiple_variants_dialog();
    },

    refresh(frm) {
        _toggle_property_specs_section(frm);

        // CH Item Master — Status Dashboard (lifecycle / PLM / approval badges + completeness)
        if (frm.doc.ch_model) {
            _render_ch_status_dashboard(frm);
        }

        // Quick action buttons (Submit/Approve/Reject/PLM/History) for saved CH items
        if (frm.doc.ch_model && !frm.is_new()) {
            _render_ch_quick_actions(frm);
        }

        // Ensure attribute_value column is visible on variant items
        // ERPNext's toggle_attributes does this too, but reinforce it here
        // so the user always sees which value each attribute has
        if (frm.doc.variant_of && frm.fields_dict['attributes']) {
            let grid = frm.fields_dict['attributes'].grid;
            grid.set_column_disp('attribute_value', true);
            grid.toggle_enable('attribute_value', false);
        }

        // Spec rows are model-driven — don't let user add/delete
        if (frm.doc.ch_model && frm.doc.ch_spec_values && frm.doc.ch_spec_values.length) {
            frm.fields_dict['ch_spec_values'].grid.cannot_add_rows = true;
            frm.fields_dict['ch_spec_values'].grid.df.cannot_delete_rows = 1;
        }

        // item_code and item_name are auto-generated server-side for CH items.
        // We suppress their reqd flag and pre-fill placeholders so Frappe's
        // mandatory check never blocks the save silently (beep with no dialog).
        if (frm.is_new() && frm.doc.ch_model) {
            frm.set_df_property('item_code', 'read_only', 1);
            frm.set_df_property('item_code', 'reqd', 0);
            frm.set_df_property('item_code', 'description',
                'Auto-generated on save from Model / Sub-Category');
            if (!frm.doc.item_code) {
                frm.doc.item_code = '__autoname';
            }
            // item_name is also mandatory in ERPNext core — suppress it;
            // the server will generate the real name on before_insert.
            frm.set_df_property('item_name', 'reqd', 0);
            if (!frm.doc.item_name) {
                frm.doc.item_name = '__autoname';
            }
            // stock_uom defaults to 'Nos' if not set — suppress mandatory beep
            frm.set_df_property('stock_uom', 'reqd', 0);
            if (!frm.doc.stock_uom) {
                frm.doc.stock_uom = 'Nos';
                frm.refresh_field('stock_uom');
            }
        }
    },

    // ── Client-side validation — runs before every save ──────────────────────
    // This is the ONLY place where we show user-visible errors for CH fields.
    // frappe.validated = false stops the save without reaching the server.
    validate(frm) {
        // ── SAP/Oracle parity: Serial Number Profile is mandatory ───────────
        // Applies to ALL items, not just CH-managed ones, because the
        // Purchase Receipt / POS / IMEI Tracker pipeline reads this for any
        // serialised item. Prompts the user instead of just rejecting.
        if (cint(frm.doc.has_serial_no) && !frm.doc.ch_serial_kind) {
            frappe.validated = false;
            _prompt_serial_kind(frm);
            return;
        }
        if (!cint(frm.doc.has_serial_no) && frm.doc.ch_serial_kind) {
            // Toggled OFF — clear stale classification so it doesn't drift.
            frm.doc.ch_serial_kind = null;
        }

        if (!frm.doc.ch_model) return; // not a CH-managed item

        let errors = [];

        // 1. Item Group is mandatory
        if (!frm.doc.item_group) {
            errors.push(__('Item Group is required'));
        }

        // 2. Stock UOM is mandatory
        if (!frm.doc.stock_uom) {
            errors.push(__('Default Unit of Measure (Stock UOM) is required'));
        }

        // 3. HSN code must be exactly 6 or 8 digits when provided
        let hsn = (frm.doc.gst_hsn_code || '').trim();
        if (hsn && hsn.length !== 6 && hsn.length !== 8) {
            errors.push(__(
                'HSN/SAC Code must be 6 or 8 digits long (current: {0} digits)',
                [hsn.length]
            ));
        }

        // 4. For simple items (no variants), check that a model is selected
        //    — belt-and-suspenders; ch_model is already visible/required in UI
        if (!frm.doc.ch_sub_category) {
            errors.push(__('Sub Category is required — select a Model first'));
        }

        if (errors.length) {
            frappe.msgprint({
                title: __('Cannot Save — Missing or Invalid Fields'),
                message: '<ul><li>' + errors.join('</li><li>') + '</li></ul>',
                indicator: 'red',
            });
            frappe.validated = false;
        }
    },

    // Ensure item_code and item_name are set to '__autoname' before save
    // so the server's before_insert hook can replace them with the real values.
    before_save(frm) {
        if (frm.is_new() && frm.doc.ch_sub_category) {
            if (!frm.doc.item_code || frm.doc.item_code === '__autoname') {
                frm.doc.item_code = '__autoname';
            }
            if (!frm.doc.item_name || frm.doc.item_name === '__autoname') {
                frm.doc.item_name = '__autoname';
            }
        }
    },

    // ── SAP/Oracle parity: Serial Number Profile classification ─────────────
    // Triggered the instant the user toggles "Has Serial No" on — opens a
    // modal forcing them to pick IMEI vs Barcode at the right moment (item
    // master), by the right person (item creator), not at transaction time.
    has_serial_no(frm) {
        if (cint(frm.doc.has_serial_no) && !frm.doc.ch_serial_kind) {
            _prompt_serial_kind(frm);
        } else if (!cint(frm.doc.has_serial_no) && frm.doc.ch_serial_kind) {
            frm.set_value('ch_serial_kind', null);
        }
    },

    // ── Model is the PRIMARY field ──────────────────────────────────────────
    ch_model(frm) {
        if (!frm.doc.ch_model) {
            frm.set_value('ch_category', '');
            frm.set_value('ch_sub_category', '');
            frm.set_intro('');
            return;
        }

        // Fetch model details and set up template
        frappe.call({
            method: 'ch_item_master.ch_item_master.api.get_model_details',
            args: { model: frm.doc.ch_model },
            callback(r) {
                let d = r.message || {};

                // Auto-fill fields from model/sub-category
                frm.doc.ch_category = d.category || '';
                frm.doc.ch_sub_category = d.sub_category || '';
                if (d.item_group) frm.doc.item_group = d.item_group;
                if (d.hsn_code)   frm.doc.gst_hsn_code = d.hsn_code;

                if (d.has_variants) {
                    // TEMPLATE item — ERPNext variant system
                    frm.doc.has_variants = 1;
                    frm.doc.variant_based_on = 'Item Attribute';

                    // Set only VARIANT specs as Item Attributes
                    frm.clear_table('attributes');
                    let selectors = d.spec_selectors || [];
                    selectors.forEach(function (s) {
                        let row = frm.add_child('attributes');
                        row.attribute = s.spec;
                    });
                } else {
                    // SIMPLE item — no variants (e.g. spares, accessories)
                    frm.doc.has_variants = 0;
                    frm.clear_table('attributes');
                }

                // Pre-populate PROPERTY specs — one row per spec, value left blank
                frm.clear_table('ch_spec_values');
                let property_specs = d.property_specs || [];
                property_specs.forEach(function (ps) {
                    let row = frm.add_child('ch_spec_values');
                    row.spec = ps.spec;
                });

                // Show/hide ch_spec_values section based on property specs
                _toggle_property_specs_section(frm, property_specs.length > 0);

                // Pre-populate MODEL FEATURES (descriptive, read-only)
                frm.clear_table('ch_model_features');
                let features = d.model_features || [];
                features.forEach(function (f) {
                    let row = frm.add_child('ch_model_features');
                    row.feature_group = f.feature_group;
                    row.feature_name = f.feature_name;
                    row.feature_value = f.feature_value;
                });

                frm.refresh_fields();

                // Re-apply mandatory suppression after model load
                // (refresh fires before ch_model callback on new docs)
                if (frm.is_new()) {
                    frm.set_df_property('item_code', 'reqd', 0);
                    frm.set_df_property('item_name', 'reqd', 0);
                    frm.set_df_property('stock_uom', 'reqd', 0);
                    if (!frm.doc.item_code) frm.doc.item_code = '__autoname';
                    if (!frm.doc.item_name) frm.doc.item_name = '__autoname';
                    if (!frm.doc.stock_uom) {
                        frm.doc.stock_uom = 'Nos';
                        frm.refresh_field('stock_uom');
                    }
                }

                if (d.hsn_code) {
                    frm.set_intro(
                        `<strong>HSN:</strong> ${d.hsn_code} &nbsp;|&nbsp; ` +
                        `<strong>GST:</strong> ${d.gst_rate || 0}%`, 'blue'
                    );
                }
            },
        });
    },

    ch_category(frm) {
        if (!frm.doc.ch_model) frm.set_value('ch_sub_category', '');
    },

    ch_sub_category(frm) {
        // no-op — model is the primary driver
    },
});

// ── CH Item Spec Value — populate spec_value options from model ─────────────
frappe.ui.form.on('CH Item Spec Value', {
    spec(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (!row.spec) return;

        // Block duplicate specs
        const dominated_by = (frm.doc.ch_spec_values || []).filter(
            r => r.spec === row.spec && r.name !== row.name
        );
        if (dominated_by.length) {
            frappe.model.set_value(cdt, cdn, 'spec', '');
            frappe.show_alert({ message: __('Specification {0} already added', [row.spec]), indicator: 'orange' });
            return;
        }

        // Clear stale value & load allowed options
        frappe.model.set_value(cdt, cdn, 'spec_value', '');
        _load_property_spec_options(frm, cdt, cdn);
    },

    // When the edit-row dialog opens for an existing row, restore options
    form_render(frm, cdt, cdn) {
        _load_property_spec_options(frm, cdt, cdn);
    },
});

function _load_property_spec_options(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    if (!row.spec || !frm.doc.ch_model) return;

    frappe.call({
        method: 'ch_item_master.ch_item_master.api.get_property_spec_values',
        args: { model: frm.doc.ch_model, spec: row.spec },
        callback(r) {
            const values = r.message || [];
            if (!values.length) return;

            const grid = frm.fields_dict['ch_spec_values'] &&
                frm.fields_dict['ch_spec_values'].grid;
            if (!grid) return;
            const grid_row = grid.grid_rows_by_docname[cdn];
            if (!grid_row) return;

            // ── Edit-dialog field (grid_form) ──────────────────────────
            if (grid_row.grid_form) {
                const ff = grid_row.grid_form.fields_dict['spec_value'];
                if (ff) {
                    // set_data() sets both awesomplete.list AND _data so
                    // get_data() returns the values on the next input/focus event.
                    if (ff.set_data) ff.set_data(values);
                    // Also store on df.options so re-renders keep the list
                    ff.df.options = values.join('\n');
                }
            }

            // ── Inline grid field ──────────────────────────────────────
            const inline_field = grid_row.on_grid_fields_dict &&
                grid_row.on_grid_fields_dict['spec_value'];
            if (inline_field) {
                if (inline_field.set_data) inline_field.set_data(values);
                inline_field.df.options = values.join('\n');
            }
        },
    });
}

function _toggle_property_specs_section(frm, show) {
    // If show is not passed, derive from current ch_spec_values rows
    if (show === undefined) {
        show = frm.doc.ch_spec_values && frm.doc.ch_spec_values.length > 0;
    }
    frm.set_df_property('ch_spec_values_section', 'hidden', show ? 0 : 1);
    frm.set_df_property('ch_spec_values_section', 'label', 'Item Properties (non-variant)');
    frm.set_df_property('ch_spec_values', 'hidden', show ? 0 : 1);
}

// ─────────────────────────────────────────────────────────────────────────────
// Serial Number Profile classification modal — SAP MARC-SERNP / Oracle
// mtl_system_items.serial_number_control_code equivalent.
//
// Opens a non-dismissible modal forcing the user to classify HOW serials
// are sourced for this item, the moment they enable "Has Serial No".
// Suggests "IMEI" when item_group descends from "Mobiles", else "Barcode".
// ─────────────────────────────────────────────────────────────────────────────
function _prompt_serial_kind(frm) {
    // Avoid stacking dialogs if validate fires repeatedly
    if (frm.__serial_kind_dialog_open) return;
    frm.__serial_kind_dialog_open = true;

    // Heuristic suggestion based on Item Group ancestry
    let suggested = 'Barcode';
    let group = (frm.doc.item_group || '').toLowerCase();
    if (group.includes('mobile') || group.includes('phone') ||
        group.includes('tablet') || group.includes('smart watch')) {
        suggested = 'IMEI';
    }

    const d = new frappe.ui.Dialog({
        title: __('Serial Number Kind Required'),
        fields: [
            {
                fieldtype: 'HTML',
                options: `
                    <div class="alert alert-warning" style="margin-bottom:15px">
                        <strong>${__('This item is serial-tracked.')}</strong><br>
                        ${__('How are serial numbers sourced for this item?')}
                        ${__('This is a one-time master-data classification — it cannot be inferred at transaction time.')}
                    </div>
                    <ul style="font-size:13px;line-height:1.6">
                        <li><b>IMEI</b> &mdash; ${__('Real 15-digit manufacturer IMEIs scanned/entered at GRN (mobile phones, cellular watches).')}</li>
                        <li><b>Barcode</b> &mdash; ${__('System-generated barcode serials (accessories, non-cellular devices, peripherals).')}</li>
                    </ul>
                `,
            },
            {
                fieldname: 'ch_serial_kind',
                label: __('Serial Number Kind'),
                fieldtype: 'Select',
                options: '\nIMEI\nBarcode',
                default: suggested,
                reqd: 1,
            },
        ],
        primary_action_label: __('Apply & Save'),
        primary_action(values) {
            if (!values.ch_serial_kind) {
                frappe.msgprint(__('Please select IMEI or Barcode.'));
                return;
            }
            frm.__serial_kind_dialog_open = false;
            d.hide();
            frm.set_value('ch_serial_kind', values.ch_serial_kind).then(() => {
                frm.save();
            });
        },
        secondary_action_label: __('Cancel — Turn Off Serial Tracking'),
        secondary_action() {
            frm.__serial_kind_dialog_open = false;
            d.hide();
            frm.set_value('has_serial_no', 0);
            frm.set_value('ch_serial_kind', null);
        },
    });
    d.$wrapper.find('.modal-header .modal-actions .close').hide(); // force a decision
    d.show();
}

// ─────────────────────────────────────────────────────────────────────────────
// Override ERPNext's "Create > Multiple Variants" dialog
//
// ERPNext shows ALL Item Attribute Values. When a CH Model is linked,
// we replace the value-fetching logic to show only model-mapped values.
// ─────────────────────────────────────────────────────────────────────────────
function _override_multiple_variants_dialog() {
    if (erpnext.item._ch_overridden) return;  // Only patch once
    erpnext.item._ch_overridden = true;

    // ── Override Single Variant dialog ──────────────────────────────────
    const _original_single = erpnext.item.show_single_variant_dialog;

    erpnext.item.show_single_variant_dialog = function (frm) {
        if (!frm.doc.ch_model) {
            return _original_single.call(this, frm);
        }

        frappe.call({
            method: 'ch_item_master.ch_item_master.api.get_model_attribute_values',
            args: { model: frm.doc.ch_model },
            callback: function (r) {
                let model_values = r.message || {};
                let fields = [];

                (frm.doc.attributes || []).forEach(function (row) {
                    if (row.disabled) return;

                    if (row.numeric_values) {
                        fields.push({
                            label: row.attribute,
                            fieldname: row.attribute,
                            fieldtype: 'Float',
                            reqd: 0,
                            description: 'Min: ' + row.from_range +
                                ', Max: ' + row.to_range +
                                ', Increment: ' + row.increment,
                        });
                    } else {
                        let values = model_values[row.attribute] || [];
                        let options = [''].concat(values);
                        fields.push({
                            label: row.attribute,
                            fieldname: row.attribute,
                            fieldtype: 'Select',
                            options: options.join('\n'),
                            reqd: 0,
                        });
                    }
                });

                if (frm.doc.image) {
                    fields.push({
                        fieldtype: 'Check',
                        label: __('Create a variant with the template image.'),
                        fieldname: 'use_template_image',
                        default: 0,
                    });
                }

                var d = new frappe.ui.Dialog({
                    title: __('Create Variant'),
                    fields: fields,
                });

                d.set_primary_action(__('Create'), function () {
                    var args = d.get_values();
                    if (!args) return;
                    frappe.call({
                        method: 'erpnext.controllers.item_variant.get_variant',
                        btn: d.get_primary_btn(),
                        args: { template: frm.doc.name, args: d.get_values() },
                        callback: function (r) {
                            if (r.message) {
                                var variant = r.message;
                                frappe.msgprint_dialog = frappe.msgprint(
                                    __('Item Variant {0} already exists with same attributes', [
                                        repl('<a href="/desk/item/%(item_encoded)s" class="strong variant-click">%(item)s</a>', {
                                            item_encoded: encodeURIComponent(variant),
                                            item: variant,
                                        }),
                                    ])
                                );
                                frappe.msgprint_dialog.hide_on_page_refresh = true;
                                frappe.msgprint_dialog.$wrapper.find('.variant-click').on('click', function () {
                                    d.hide();
                                });
                            } else {
                                d.hide();
                                frappe.call({
                                    method: 'erpnext.controllers.item_variant.create_variant',
                                    args: {
                                        item: frm.doc.name,
                                        args: d.get_values(),
                                        use_template_image: args.use_template_image,
                                    },
                                    callback: function (r) {
                                        var doclist = frappe.model.sync(r.message);
                                        frappe.set_route('Form', doclist[0].doctype, doclist[0].name);
                                    },
                                });
                            }
                        },
                    });
                });

                d.show();
            },
        });
    };

    // ── Override Multiple Variants dialog ───────────────────────────────
    const _original = erpnext.item.show_multiple_variants_dialog;

    erpnext.item.show_multiple_variants_dialog = function (frm) {
        // If no CH Model, fall back to ERPNext default
        if (!frm.doc.ch_model) {
            return _original.call(this, frm);
        }

        // Fetch model-allowed values
        frappe.call({
            method: 'ch_item_master.ch_item_master.api.get_model_attribute_values',
            args: { model: frm.doc.ch_model },
            callback: function (r) {
                let model_values = r.message || {};

                // Build attr_val_fields in attribute order from the template
                let attr_val_fields = {};
                (frm.doc.attributes || []).forEach(function (d) {
                    if (!d.disabled && model_values[d.attribute]) {
                        attr_val_fields[d.attribute] = model_values[d.attribute];
                    }
                });

                if (!Object.keys(attr_val_fields).length) {
                    frappe.msgprint(__('No attribute values mapped for this model. Please add values in the CH Model form.'));
                    return;
                }

                _show_ch_variant_dialog(frm, attr_val_fields);
            },
        });
    };
}

function _show_ch_variant_dialog(frm, attr_val_fields) {
    let fields = [];
    let me = erpnext.item;

    Object.keys(attr_val_fields).forEach((name, i) => {
        if (i % 3 === 0) {
            fields.push({ fieldtype: 'Section Break' });
        }
        fields.push({ fieldtype: 'Column Break', label: name });
        attr_val_fields[name].forEach((value) => {
            fields.push({
                fieldtype: 'Check',
                label: value,
                fieldname: value,
                default: 0,
                onchange: function () {
                    _update_variant_count(me, attr_val_fields);
                },
            });
        });
    });

    me.multiple_variant_dialog = new frappe.ui.Dialog({
        title: __('Select Attribute Values'),
        fields: [
            frm.doc.image
                ? {
                    fieldtype: 'Check',
                    label: __('Create a variant with the template image.'),
                    fieldname: 'use_template_image',
                    default: 0,
                  }
                : null,
            {
                fieldtype: 'HTML',
                fieldname: 'help',
                options: `<label class="control-label">
                    ${__('Select at least one value from each of the attributes.')}
                </label>`,
            },
        ].concat(fields).filter(Boolean),
    });

    me.multiple_variant_dialog.set_primary_action(__('Create Variants'), () => {
        let selected = _get_selected(me, attr_val_fields);
        let use_template_image = me.multiple_variant_dialog.get_value('use_template_image');

        me.multiple_variant_dialog.hide();
        frappe.call({
            method: 'erpnext.controllers.item_variant.enqueue_multiple_variant_creation',
            args: {
                item: frm.doc.name,
                args: selected,
                use_template_image: use_template_image,
            },
            callback: function (r) {
                if (r.message === 'queued') {
                    frappe.show_alert({
                        message: __('Variant creation has been queued.'),
                        indicator: 'orange',
                    });
                } else {
                    frappe.show_alert({
                        message: __('{0} variants created.', [r.message]),
                        indicator: 'green',
                    });
                }
            },
        });
    });

    $(me.multiple_variant_dialog.$wrapper.find('.form-column'))
        .find('.frappe-control').css('margin-bottom', '0px');

    me.multiple_variant_dialog.disable_primary_action();
    me.multiple_variant_dialog.clear();
    me.multiple_variant_dialog.show();
}

function _get_selected(me, attr_val_fields) {
    let selected = {};
    me.multiple_variant_dialog.$wrapper.find('.form-column').each((i, col) => {
        if (i === 0) return;
        let attr_name = $(col).find('.column-label').html().trim();
        selected[attr_name] = [];
        $(col).find('.checkbox input').each((j, opt) => {
            if ($(opt).is(':checked')) {
                selected[attr_name].push($(opt).attr('data-fieldname'));
            }
        });
    });
    return selected;
}

function _update_variant_count(me, attr_val_fields) {
    let selected = _get_selected(me, attr_val_fields);
    let lengths = Object.keys(selected).map(k => selected[k].length);

    if (lengths.includes(0)) {
        me.multiple_variant_dialog.get_primary_btn().html(__('Create Variants'));
        me.multiple_variant_dialog.disable_primary_action();
    } else {
        let count = lengths.reduce((a, b) => a * b, 1);
        let msg = count === 1
            ? __('Make {0} Variant', [count])
            : __('Make {0} Variants', [count]);
        me.multiple_variant_dialog.get_primary_btn().html(msg);
        me.multiple_variant_dialog.enable_primary_action();
    }
}


// ─────────────────────────────────────────────────────────────────────────────
//  CH Item Master — Status Dashboard & Quick Actions (UI modernization)
// ─────────────────────────────────────────────────────────────────────────────

// Color maps for status indicators
const _LIFECYCLE_COLORS = {
    'Draft':          'gray',
    'Pending Review': 'orange',
    'Active':         'green',
    'Obsolete':       'red',
    'Blocked':        'red',
};

const _PLM_COLORS = {
    'NPI':               'gray',
    'Under Review':      'orange',
    'Sample Testing':    'yellow',
    'Approved':          'blue',
    'Active Production': 'green',
    'End of Life':       'orange',
    'Discontinued':      'red',
};

const _APPROVAL_COLORS = {
    'Draft':                'gray',
    'Submitted for Review': 'orange',
    'Approved':             'green',
    'Rejected':             'red',
};

function _lifecycle_color(status) {
    return _LIFECYCLE_COLORS[status] || 'gray';
}

function _plm_color(status) {
    return _PLM_COLORS[status] || 'gray';
}

function _approval_color(status) {
    return _APPROVAL_COLORS[status] || 'gray';
}

/**
 * Calculate a completeness score (0–100) based on key Item fields.
 * Weighted: required fields count double.
 */
function _calc_completeness(doc) {
    const required = [
        'ch_model', 'ch_category', 'ch_sub_category', 'item_group',
        'brand', 'stock_uom',
    ];
    const optional = [
        'gst_hsn_code', 'country_of_origin', 'description', 'image',
        'ch_standard_cost', 'ch_minimum_selling_price', 'ch_gtin',
    ];

    let req_filled = required.filter(f => doc[f] && doc[f] !== '__autoname').length;
    let opt_filled = optional.filter(f => doc[f]).length;

    // Spec values: bonus if all spec rows are filled
    let specs = doc.ch_spec_values || [];
    let specs_filled = specs.length > 0 && specs.every(r => r.spec_value);
    if (specs_filled) opt_filled += 1;

    // Weighted score: required fields = 2 pts each, optional = 1 pt each
    const max_score = required.length * 2 + optional.length + 1;
    const score = (req_filled * 2 + opt_filled) / max_score * 100;
    return Math.round(score);
}

/**
 * Render colored status indicator pills on the form dashboard.
 * Shows Lifecycle, PLM, Approval statuses and a completeness score.
 */
function _render_ch_status_dashboard(frm) {
    // Clear existing CH-added indicators before re-rendering
    frm.dashboard.clear_headline();

    const lifecycle = frm.doc.ch_lifecycle_status || 'Draft';
    frm.dashboard.add_indicator(
        __('Lifecycle: {0}', [__(lifecycle)]),
        _lifecycle_color(lifecycle)
    );

    if (frm.doc.ch_plm_status) {
        frm.dashboard.add_indicator(
            __('PLM: {0}', [__(frm.doc.ch_plm_status)]),
            _plm_color(frm.doc.ch_plm_status)
        );
    }

    if (frm.doc.ch_approval_status) {
        frm.dashboard.add_indicator(
            __('Approval: {0}', [__(frm.doc.ch_approval_status)]),
            _approval_color(frm.doc.ch_approval_status)
        );
    }

    // Completeness score
    const score = _calc_completeness(frm.doc);
    const score_color = score >= 80 ? 'green' : score >= 50 ? 'orange' : 'red';
    frm.dashboard.add_indicator(
        __('Complete: {0}%', [score]),
        score_color
    );

    // Sales Readiness — SAP "material status" pattern. Tells users at a glance
    // whether the item can actually be added to a Sales Invoice today.
    if (!frm.is_new()) {
        _render_ch_sales_readiness(frm);
    }
}

/**
 * Render context-aware quick action buttons.
 * Grouped under "CH Actions" in the form's custom buttons area.
 */
function _render_ch_quick_actions(frm) {
    const approval = frm.doc.ch_approval_status || 'Draft';
    const roles = frappe.user_roles || [];

    const is_approver = roles.includes('CH Master Approver') ||
        roles.includes('System Manager');

    // ── Approval workflow ──────────────────────────────────────────────────
    if (approval === 'Draft' || approval === 'Rejected') {
        frm.add_custom_button(__('Submit for Review'), function () {
            _ch_submit_for_review(frm);
        }, __('CH Actions'));
    }

    if (approval === 'Submitted for Review' && is_approver) {
        frm.add_custom_button(__('Approve'), function () {
            _ch_approve(frm);
        }, __('CH Actions'));

        frm.add_custom_button(__('Reject'), function () {
            _ch_reject(frm);
        }, __('CH Actions'));
    }

    // ── PLM transition ─────────────────────────────────────────────────────
    const plm_next = _plm_next_states(frm.doc.ch_plm_status);
    if (plm_next.length) {
        plm_next.forEach(function (next_state) {
            frm.add_custom_button(__('PLM → {0}', [__(next_state)]), function () {
                _ch_plm_advance(frm, next_state);
            }, __('CH Actions'));
        });
    }

    // ── Version History ────────────────────────────────────────────────────
    frm.add_custom_button(__('Version History'), function () {
        _show_version_history_dialog(frm);
    }, __('CH Actions'));
}

/** Returns the allowed next PLM states from a given state. */
function _plm_next_states(current) {
    const transitions = {
        'NPI':               ['Under Review'],
        'Under Review':      ['Sample Testing', 'NPI'],
        'Sample Testing':    ['Approved', 'Under Review'],
        'Approved':          ['Active Production', 'Under Review'],
        'Active Production': ['End of Life'],
        'End of Life':       ['Discontinued'],
        'Discontinued':      [],
    };
    return transitions[current] || [];
}

/** Prompt for remarks and call submit_for_approval. */
function _ch_submit_for_review(frm) {
    frappe.prompt(
        [{ fieldname: 'remarks', fieldtype: 'Small Text', label: __('Remarks (optional)') }],
        function (values) {
            frappe.call({
                method: 'ch_item_master.ch_item_master.tier_c.submit_for_approval',
                args: { item_code: frm.doc.name, remarks: values.remarks || '' },
                freeze: true,
                freeze_message: __('Submitting for review…'),
                callback: function (r) {
                    if (!r.exc) {
                        frappe.show_alert({ message: __('Submitted for Review'), indicator: 'green' });
                        frm.reload_doc();
                    }
                },
            });
        },
        __('Submit for Review'),
        __('Submit')
    );
}

/** Prompt for remarks and call approve_item. */
function _ch_approve(frm) {
    frappe.prompt(
        [{ fieldname: 'remarks', fieldtype: 'Small Text', label: __('Approval Remarks (optional)') }],
        function (values) {
            frappe.call({
                method: 'ch_item_master.ch_item_master.tier_c.approve_item',
                args: { item_code: frm.doc.name, remarks: values.remarks || '' },
                freeze: true,
                freeze_message: __('Approving item…'),
                callback: function (r) {
                    if (!r.exc) {
                        frappe.show_alert({ message: __('Item Approved'), indicator: 'green' });
                        frm.reload_doc();
                    }
                },
            });
        },
        __('Approve Item'),
        __('Approve')
    );
}

/** Prompt for rejection reason and call reject_item. */
function _ch_reject(frm) {
    frappe.prompt(
        [{ fieldname: 'remarks', fieldtype: 'Small Text', label: __('Rejection Reason'), reqd: 1 }],
        function (values) {
            frappe.call({
                method: 'ch_item_master.ch_item_master.tier_c.reject_item',
                args: { item_code: frm.doc.name, remarks: values.remarks },
                freeze: true,
                freeze_message: __('Rejecting item…'),
                callback: function (r) {
                    if (!r.exc) {
                        frappe.show_alert({ message: __('Item Rejected'), indicator: 'red' });
                        frm.reload_doc();
                    }
                },
            });
        },
        __('Reject Item'),
        __('Reject')
    );
}

/** Advance the PLM state machine to the next state. */
function _ch_plm_advance(frm, next_state) {
    frappe.confirm(
        __('Move PLM status from <b>{0}</b> to <b>{1}</b>?',
            [frm.doc.ch_plm_status, next_state]),
        function () {
            // Set the field and save — validate_plm_transition runs on before_save
            frappe.model.set_value(frm.doctype, frm.docname, 'ch_plm_status', next_state);
            frm.save(null, function () {
                frappe.show_alert({ message: __('PLM status updated to {0}', [next_state]), indicator: 'blue' });
            });
        }
    );
}

/**
 * Show a timeline dialog with all item versions.
 * Displays version number, date, changed by, lifecycle/PLM status, cost.
 */
function _show_version_history_dialog(frm) {
    frappe.call({
        method: 'ch_item_master.ch_item_master.tier_c.get_item_versions',
        args: { item_code: frm.doc.name },
        callback: function (r) {
            const versions = r.message || [];
            if (!versions.length) {
                frappe.msgprint(__('No version history available for this item.'));
                return;
            }

            // Build HTML timeline
            let html = '<div class="ch-version-timeline" style="max-height:480px;overflow-y:auto;padding:8px 0;">';
            versions.forEach(function (v) {
                const date_str = v.snapshot_date
                    ? frappe.datetime.str_to_user(v.snapshot_date)
                    : '—';
                const lc_color = _lifecycle_color(v.ch_lifecycle_status || '');
                const plm_color = _plm_color(v.ch_plm_status || '');

                html += `
                <div style="display:flex;gap:12px;margin-bottom:14px;align-items:flex-start;">
                    <div style="min-width:36px;text-align:center;">
                        <span class="badge badge-pill" style="background:var(--blue-500);color:#fff;font-size:11px;">
                            v${v.version_number}
                        </span>
                    </div>
                    <div style="flex:1;border-left:2px solid var(--border-color);padding-left:12px;">
                        <div style="font-weight:600;font-size:13px;color:var(--heading-color);">
                            ${frappe.utils.escape_html(date_str)}
                            <span style="font-weight:400;color:var(--text-muted);font-size:12px;margin-left:8px;">
                                by ${frappe.utils.escape_html(v.changed_by || '—')}
                            </span>
                        </div>
                        <div style="margin-top:4px;display:flex;flex-wrap:wrap;gap:6px;">
                            ${v.ch_lifecycle_status ? `<span class="indicator-pill ${lc_color}" style="font-size:11px;">${frappe.utils.escape_html(v.ch_lifecycle_status)}</span>` : ''}
                            ${v.ch_plm_status ? `<span class="indicator-pill ${plm_color}" style="font-size:11px;">PLM: ${frappe.utils.escape_html(v.ch_plm_status)}</span>` : ''}
                            ${v.ch_standard_cost ? `<span style="font-size:11px;color:var(--text-muted);">Cost: ${format_currency(v.ch_standard_cost)}</span>` : ''}
                        </div>
                        ${v.remarks ? `<div style="margin-top:4px;font-size:12px;color:var(--text-muted);">${frappe.utils.escape_html(v.remarks)}</div>` : ''}
                    </div>
                </div>`;
            });
            html += '</div>';

            const d = new frappe.ui.Dialog({
                title: __('Version History — {0}', [frm.doc.name]),
                size: 'large',
            });
            d.body.innerHTML = html;
            d.show();
        },
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Sales Readiness — SAP/Oracle/Dynamics-style checklist
// Renders a single dashboard indicator ("Sellable" / "Not Sellable — N issues")
// and a "Sales Readiness" custom button that opens a checklist dialog with
// one-click deep links to fix each gap. Does NOT bypass PLM — it just exposes
// what's blocking the item from being added to a Sales Invoice today.
// ─────────────────────────────────────────────────────────────────────────────

function _render_ch_sales_readiness(frm) {
    const company = frappe.defaults.get_user_default('Company') || frappe.defaults.get_global_default('company');
    if (!company) return;

    frappe.call({
        method: 'ch_item_master.ch_item_master.readiness.get_item_readiness',
        args: { item_code: frm.doc.name, company: company },
        callback: function (r) {
            if (r.exc || !r.message) return;
            const data = r.message;
            const label = data.is_sellable
                ? __('Sellable in {0}', [data.company || company])
                : __('Not Sellable — {0} issue(s)', [data.blockers]);
            const color = data.is_sellable ? 'green' : (data.blockers ? 'red' : 'orange');
            frm.dashboard.add_indicator(label, color);

            // Add the checklist button (idempotent — remove existing first)
            frm.remove_custom_button(__('Sales Readiness'), __('CH Actions'));
            frm.add_custom_button(__('Sales Readiness'), function () {
                _show_ch_sales_readiness_dialog(frm, data);
            }, __('CH Actions'));
        },
    });
}

function _show_ch_sales_readiness_dialog(frm, data) {
    const checks = data.checks || [];
    const sev_color = { blocker: 'red', warning: 'orange', info: 'gray' };

    const rows = checks.map(function (c) {
        const status_html = c.passed
            ? '<span class="indicator-pill green" style="font-size:11px;">OK</span>'
            : `<span class="indicator-pill ${sev_color[c.severity] || 'orange'}" style="font-size:11px;">${frappe.utils.escape_html(c.severity)}</span>`;

        const fix_btn = !c.passed && c.fix && c.fix.type !== 'info'
            ? `<button class="btn btn-xs btn-default ch-readiness-fix" data-key="${frappe.utils.escape_html(c.key)}">${__('Fix')}</button>`
            : '';

        return `
            <tr>
                <td style="width:80px;">${status_html}</td>
                <td><b>${frappe.utils.escape_html(c.label)}</b><br>
                    <span style="font-size:12px;color:var(--text-muted);">${frappe.utils.escape_html(c.message || '')}</span></td>
                <td style="width:60px;text-align:right;">${fix_btn}</td>
            </tr>`;
    }).join('');

    const summary = data.is_sellable
        ? `<div class="alert alert-success" style="margin-bottom:10px;">${__('This item is ready to add to Sales Invoice in {0}.', [data.company])}</div>`
        : `<div class="alert alert-danger" style="margin-bottom:10px;">${__('{0} blocker(s) must be cleared before this item can be sold in {1}.', [data.blockers, data.company])}</div>`;

    const html = `
        ${summary}
        <table class="table table-bordered" style="font-size:13px;">
            <thead><tr>
                <th>${__('Status')}</th>
                <th>${__('Check')}</th>
                <th></th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>
        <div style="font-size:11px;color:var(--text-muted);margin-top:8px;">
            ${__('Following SAP S/4HANA Material Status + Oracle Item Lifecycle pattern. PLM gates are enforced — this dialog only surfaces gaps.')}
        </div>`;

    const d = new frappe.ui.Dialog({
        title: __('Sales Readiness — {0}', [frm.doc.name]),
        size: 'large',
    });
    d.body.innerHTML = html;
    $(d.body).find('.ch-readiness-fix').on('click', function () {
        const key = $(this).data('key');
        const c = checks.find(function (x) { return x.key === key; });
        if (!c || !c.fix) return;
        d.hide();
        _apply_ch_readiness_fix(frm, c);
    });
    d.show();
}

function _apply_ch_readiness_fix(frm, check) {
    const fix = check.fix;
    if (!fix) return;

    if (fix.type === 'field') {
        frappe.set_route('Form', fix.doctype, fix.name).then(function () {
            setTimeout(function () {
                if (cur_frm && cur_frm.doc.name === fix.name) {
                    cur_frm.scroll_to_field(fix.field);
                    if (typeof fix.value !== 'undefined') {
                        cur_frm.set_value(fix.field, fix.value);
                    }
                    frappe.show_alert({
                        message: __('Update "{0}" and save.', [fix.field]),
                        indicator: 'blue',
                    });
                }
            }, 600);
        });
    } else if (fix.type === 'child') {
        frappe.set_route('Form', fix.doctype, fix.name).then(function () {
            setTimeout(function () {
                if (cur_frm && cur_frm.doc.name === fix.name) {
                    cur_frm.scroll_to_field(fix.field);
                    const grid = cur_frm.fields_dict[fix.field] && cur_frm.fields_dict[fix.field].grid;
                    if (grid) {
                        const row = cur_frm.add_child(fix.field);
                        if (fix.company) row.company = fix.company;
                        cur_frm.refresh_field(fix.field);
                        grid.grid_rows[grid.grid_rows.length - 1].toggle_view(true);
                    }
                    frappe.show_alert({
                        message: __('Fill the new {0} row and save.', [fix.field]),
                        indicator: 'blue',
                    });
                }
            }, 600);
        });
    } else if (fix.type === 'new_doc') {
        const defaults = fix.defaults || {};
        frappe.new_doc(fix.doctype, defaults);
    } else if (fix.type === 'action') {
        frappe.confirm(
            __('Run "{0}" now?', [fix.label || check.label]),
            function () {
                frappe.call({
                    method: fix.method,
                    args: fix.args || {},
                    freeze: true,
                    callback: function (r) {
                        if (!r.exc) {
                            frappe.show_alert({ message: __('Done'), indicator: 'green' });
                            frm.reload_doc();
                        }
                    },
                });
            },
        );
    }
}
