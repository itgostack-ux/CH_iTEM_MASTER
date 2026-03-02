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

        // Override ERPNext's Multiple Variants dialog to show only
        // model-allowed values instead of ALL Item Attribute Values
        _override_multiple_variants_dialog();
    },

    refresh(frm) {
        _toggle_property_specs_section(frm);

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

        // For template items (has_variants=1), make item_code read-only
        // since it's auto-generated
        if (frm.is_new() && frm.doc.ch_model) {
            frm.set_df_property('item_code', 'read_only', 1);
            frm.set_df_property('item_code', 'description',
                'Auto-generated when saved');
        }
    },

    // ── Client-side validation — runs before every save ──────────────────────
    // This is the ONLY place where we show user-visible errors for CH fields.
    // frappe.validated = false stops the save without reaching the server.
    validate(frm) {
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

    // Prevent client-side mandatory error for item_code;
    // the real code is generated server-side in before_insert.
    before_save(frm) {
        if (frm.is_new() && frm.doc.ch_sub_category && !frm.doc.item_code) {
            frm.doc.item_code = '__autoname';
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

                frm.refresh_fields();

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

            const grid = frm.fields_dict['ch_spec_values'] &&
                frm.fields_dict['ch_spec_values'].grid;
            if (!grid) return;
            const grid_row = grid.grid_rows_by_docname[cdn];
            if (!grid_row) return;

            // Set on the edit-dialog field (grid_form) using set_data()
            // which directly updates awesomplete.list
            if (grid_row.grid_form) {
                const ff = grid_row.grid_form.fields_dict['spec_value'];
                if (ff && ff.set_data) {
                    ff.set_data(values);
                }
            }

            // Also set on the inline grid field
            const inline_field = grid_row.on_grid_fields_dict['spec_value'];
            if (inline_field && inline_field.set_data) {
                inline_field.set_data(values);
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
                                        repl('<a href="/app/item/%(item_encoded)s" class="strong variant-click">%(item)s</a>', {
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
