// Copyright (c) 2026, GoStack and contributors
// CH Item Master — CH Model form client script
//
// Mirrors the Angular "Model - Spec Values" tab:
//   1. Sub Category change → auto-populate spec rows (one per spec in sub-category)
//   2. spec_value is an Autocomplete that fetches allowed values for the
//      selected spec via a server-side get_query (standard Frappe pattern).
//   3. Multiple rows with the same spec = multiple allowed values
//   4. Live Item Name Preview in the form header

frappe.provide('ch_model');

// ─────────────────────────────────────────────────────────────────────────────
// Helper: refresh the Item Name Preview field
// ─────────────────────────────────────────────────────────────────────────────
ch_model.update_preview = function (frm) {
	if (!frm.doc.sub_category || !frm.doc.manufacturer || !frm.doc.brand) {
		frm.set_value('item_name_preview', '');
		return;
	}

	// Collect first spec_value row per spec as a sample (for preview)
	let spec_values = [];
	let seen = {};
	(frm.doc.spec_values || []).forEach((r) => {
		if (r.spec && r.spec_value && !seen[r.spec]) {
			spec_values.push({ spec: r.spec, spec_value: r.spec_value });
			seen[r.spec] = true;
		}
	});

	frappe.call({
		method: 'ch_item_master.ch_item_master.api.generate_item_name',
		args: {
			sub_category: frm.doc.sub_category,
			manufacturer: frm.doc.manufacturer,
			brand: frm.doc.brand,
			model: frm.is_new() ? null : frm.docname,
			model_name_override: frm.doc.model_name || '',
			spec_values: JSON.stringify(spec_values),
		},
		callback(r) {
			frm.set_value('item_name_preview', r.message || '');
		},
	});
};

// ─────────────────────────────────────────────────────────────────────────────
// Helper: apply manufacturer filter from sub-category allowed list
// ─────────────────────────────────────────────────────────────────────────────
ch_model._apply_manufacturer_filter = function (frm) {
	if (!frm.doc.sub_category) {
		frm.set_query('manufacturer', () => ({ filters: { name: 'DISABLED' } }));
		return;
	}
	frappe.call({
		method: 'ch_item_master.ch_item_master.api.get_sub_category_manufacturers',
		args: { sub_category: frm.doc.sub_category },
		callback(r) {
			let allowed = r.message || [];
			frm.set_query('manufacturer', () => ({
				// If no manufacturers are configured for the sub-category,
				// fall back to all (no restriction rather than showing nothing).
				filters: allowed.length ? { name: ['in', allowed] } : {},
			}));
		},
	});
};

// ─────────────────────────────────────────────────────────────────────────────
// Helper: refresh allowed brands for the selected manufacturer
// ─────────────────────────────────────────────────────────────────────────────
ch_model._refresh_allowed_brands = function (frm) {

	if (!frm.doc.manufacturer) {
		frm._allowed_brands = [];
		return;
	}
	frappe.call({
		method: 'ch_item_master.ch_item_master.api.get_brands_for_manufacturer',
		args: { manufacturer: frm.doc.manufacturer },
		callback(r) {
			frm._allowed_brands = r.message || [];
		},
	});
};

// ─────────────────────────────────────────────────────────────────────────────
// Helper: pre-populate spec_value dropdown with all allowed values for a spec.
// Called from form_render (row dialog opened) and spec change event.
// Sets awesomplete.list + minChars=0 so ALL values appear on click.
// ─────────────────────────────────────────────────────────────────────────────
ch_model._load_spec_value_options = function (frm, cdt, cdn) {
	let row = frappe.get_doc(cdt, cdn);
	if (!row || !row.spec) return;

	frappe.call({
		method: 'ch_item_master.ch_item_master.api.get_attribute_values',
		args: { spec: row.spec },
		callback(r) {
			let values = r.message || [];
			if (!values.length) return;

			// Apply to the currently open grid-form dialog for this row
			let gf = frm.cur_grid && frm.cur_grid.grid_form;
			if (!gf || !gf.fields_dict) return;
			let field = gf.fields_dict['spec_value'];
			if (!field) return;

			// Store on the docfield so it survives re-renders (set_options()
			// reads df.options when the field is re-created)
			field.df.options = values.join('\n');

			// set_data() sets both awesomplete.list AND the internal _data cache.
			// _data is what get_data() returns on the 'input' event fired by focus.
			// If only awesomplete.list is set directly, _data stays [] and the
			// input-event handler overwrites the list with empty on every focus.
			if (field.set_data) {
				field.set_data(values);
			}
		},
	});
};

// ─────────────────────────────────────────────────────────────────────────────
// CH Model form events
// ─────────────────────────────────────────────────────────────────────────────
frappe.ui.form.on('CH Model', {
	setup(frm) {
		// Brand filter — brands that list the selected manufacturer
		// in their ch_manufacturers child table.
		// Uses a cached list refreshed when manufacturer changes.
		frm._allowed_brands = [];
		frm.set_query('brand', () => {
			if (!frm.doc.manufacturer || !frm._allowed_brands.length) {
				return { filters: { name: 'DISABLED' } };
			}
			return { filters: { name: ['in', frm._allowed_brands] } };
		});

		// Spec dropdown: only show specs configured for the current sub-category.
		// This prevents selecting invalid specs like 'Size' on a sub-category
		// that only defines 'Colour'.
		frm.fields_dict['spec_values'].grid.get_field('spec').get_query = function () {
			return {
				query: 'ch_item_master.ch_item_master.api.search_specs_for_sub_category',
				filters: { sub_category: frm.doc.sub_category || '' },
			};
		};

		// spec_value options are injected via _load_spec_value_options called
		// from form_render / spec events. No set_query here — Frappe's
		// execute_query_if_exists does NOT forward `filters`, only `params`,
		// so a set_query with filters would fire the server with no spec arg
		// and wipe the pre-loaded list with an empty result.

		// Model Features: filter feature_group to enabled groups only
		frm.set_query('feature_group', 'model_features', () => ({
			filters: { disabled: 0 },
		}));

		// Model Features: filter feature_name to features in the selected group
		frm.set_query('feature_name', 'model_features', (doc, cdt, cdn) => {
			let row = locals[cdt][cdn];
			return {
				filters: {
					feature_group: row.feature_group || '',
					disabled: 0,
				},
			};
		});
	},

	// ── Client-side validation — fires BEFORE check_mandatory ────────────────
	validate(frm) {
		// frappe.ui.form.close_grid_form() closes the edit-row dialog AND calls
		// frappe.dom.unfreeze() — without this, the "dark grid-form" freeze
		// overlay stays active and our msgprint dialog appears hidden behind it
		// (the user hears the OS "beep" from a click on the frozen page but
		// sees no dialog).  When no row editor is open this is a no-op.
		frappe.ui.form.close_grid_form();

		// Check top-level mandatory fields
		let missing = [];
		if (!frm.doc.sub_category) missing.push(__('Sub Category'));
		if (!frm.doc.manufacturer) missing.push(__('Manufacturer'));
		if (!frm.doc.brand) missing.push(__('Brand'));
		if (!frm.doc.model_name) missing.push(__('Model Name'));

		if (missing.length) {
			frappe.msgprint({
				title: __('Missing Required Fields'),
				message: __('Please fill the following fields before saving:') +
					'<br><ul><li>' + missing.join('</li><li>') + '</li></ul>',
				indicator: 'red',
			});
			frappe.validated = false;
			// Return a resolved Promise so script_manager does NOT fall through
			// to frappe.after_server_call() (which would wait for in-flight AJAX)
			return Promise.resolve();
		}

		// Check spec rows: each row with a Specification must have a Value
		let incomplete_specs = (frm.doc.spec_values || []).filter(r => r.spec && !r.spec_value);
		if (incomplete_specs.length) {
			let specs = [...new Set(incomplete_specs.map(r => r.spec))];
			frappe.msgprint({
				title: __('Missing Spec Values'),
				message: __('Please fill a Value for each Specification row. Missing values for:') +
					'<br><ul><li>' + specs.join('</li><li>') + '</li></ul>' +
					'<br>' + __('Add at least one value per specification (e.g. Colour: Black).'),
				indicator: 'orange',
			});
			frappe.validated = false;
		}
		return Promise.resolve();
	},

	sub_category(frm) {
		// Clear downstream fields
		frm.set_value('manufacturer', '');
		frm.set_value('brand', '');
		frm.set_value('item_name_preview', '');

		if (!frm.doc.sub_category) {
			frm.clear_table('spec_values');
			frm.refresh_field('spec_values');
			// Reset manufacturer filter — show nothing
			frm.set_query('manufacturer', () => ({ filters: { name: 'DISABLED' } }));
			return;
		}

		// Filter manufacturer to only allowed ones for this sub-category
		ch_model._apply_manufacturer_filter(frm);

		// Auto-populate spec rows from sub-category specs (only if table is empty)
		if (frm.doc.spec_values && frm.doc.spec_values.length > 0) {
			return;
		}

		frappe.db
			.get_list('CH Sub Category Spec', {
				filters: { parent: frm.doc.sub_category },
				fields: ['spec', 'name_order'],
				order_by: 'name_order asc, idx asc',
				limit: 100,
			})
			.then((specs) => {
				if (!specs || specs.length === 0) return;
				frm.clear_table('spec_values');
				specs.forEach((s) => {
					let row = frm.add_child('spec_values');
					row.spec = s.spec;
				});
				frm.refresh_field('spec_values');
				frappe.msgprint({
					message: `${specs.length} specification(s) pre-loaded from Sub Category.<br>Add one row per allowed value for each spec (e.g. Color: Black, then Color: White...).`,
					indicator: 'blue',
					title: 'Specs Pre-loaded',
				});
			});
	},

	manufacturer(frm) {
		frm.set_value('brand', '');
		frm.set_value('item_name_preview', '');
		// Refresh allowed brands for the newly selected manufacturer
		ch_model._refresh_allowed_brands(frm);
	},

	brand(frm) {
		ch_model.update_preview(frm);
	},

	model_name(frm) {
		ch_model.update_preview(frm);
	},

	refresh(frm) {
		// Apply manufacturer filter on form load (not just sub_category change)
		// so that re-opening an existing model still restricts the dropdown.
		if (frm.doc.sub_category) {
			ch_model._apply_manufacturer_filter(frm);
		}
		// Load allowed brands for current manufacturer
		if (frm.doc.manufacturer) {
			ch_model._refresh_allowed_brands(frm);
		}

		if (!frm.is_new()) {
			frm.add_custom_button('Refresh Name Preview', () => ch_model.update_preview(frm));

			// Show existing items for this model
			frm.add_custom_button(__('Show Items'), () => {
				frappe.set_route('List', 'Item', { ch_model: frm.doc.name });
			}, __('View'));

			// ── Generate All Items (bulk variant creation) ───────────────
			if (!frm.doc.disabled) {
				frm.add_custom_button(__('Generate All Items'), () => {
					// Count combinations for confirmation
					let specs = (frm.doc.spec_values || []);
					let grouped = {};
					specs.forEach(r => {
						if (r.spec && r.spec_value) {
							if (!grouped[r.spec]) grouped[r.spec] = new Set();
							grouped[r.spec].add(r.spec_value);
						}
					});
					let specNames = Object.keys(grouped);
					if (!specNames.length) {
						frappe.msgprint(__('No spec values defined. Add values in the Spec Values table first.'));
						return;
					}
					let total = 1;
					let details = [];
					specNames.forEach(s => {
						let count = grouped[s].size;
						total *= count;
						details.push(`${s}: ${count} value(s)`);
					});

					frappe.confirm(
						__('This will generate up to <b>{0}</b> item variant(s):<br><br>{1}<br><br>Existing variants will be skipped. Continue?',
							[total, details.join('<br>')]),
						() => {
							frappe.call({
								method: 'ch_item_master.ch_item_master.api.generate_items_from_model',
								args: { model: frm.doc.name },
								freeze: true,
								freeze_message: __('Generating items...'),
								callback(r) {
									let d = r.message || {};
									let msg = __(
										'<b>Template:</b> {0}<br>' +
										'<b>Total combinations:</b> {1}<br>' +
										'<b>Created:</b> {2}<br>' +
										'<b>Skipped (already exist):</b> {3}',
										[d.template, d.total_combinations, d.created, d.skipped]
									);
									if (d.errors && d.errors.length) {
										msg += '<br><br><b>Errors:</b><br>' +
											d.errors.map(e => frappe.utils.escape_html(e)).join('<br>');
									}
									frappe.msgprint({
										title: __('Item Generation Complete'),
										message: msg,
										indicator: d.errors && d.errors.length ? 'orange' : 'green',
									});
								},
							});
						}
					);
				}, __('Actions'));
			}

			// ── Clone Model ──────────────────────────────────────────────
			frm.add_custom_button(__('Clone Model'), () => {
				let d = new frappe.ui.Dialog({
					title: __('Clone Model to Another Sub Category'),
					fields: [
						{
							fieldname: 'new_sub_category',
							fieldtype: 'Link',
							label: __('Target Sub Category'),
							options: 'CH Sub Category',
							reqd: 1,
							get_query() {
								return { filters: { name: ['!=', frm.doc.sub_category] } };
							},
						},
						{
							fieldname: 'new_model_name',
							fieldtype: 'Data',
							label: __('New Model Name'),
							default: frm.doc.model_name,
							reqd: 1,
						},
					],
					primary_action_label: __('Clone'),
					primary_action(values) {
						frappe.call({
							method: 'frappe.client.get_list',
							args: {
								doctype: 'CH Sub Category Spec',
								filters: { parent: values.new_sub_category, parenttype: 'CH Sub Category' },
								fields: ['spec'],
								limit_page_length: 100,
							},
							callback(r) {
								let target_specs = new Set((r.message || []).map(s => s.spec));
								let new_doc = frappe.model.copy_doc(frm.doc);
								new_doc.sub_category = values.new_sub_category;
								new_doc.model_name = values.new_model_name;
								new_doc.disabled = 0;
								// Clear manufacturer/brand — may not be valid for new sub-category
								new_doc.manufacturer = '';
								new_doc.brand = '';
								new_doc.item_name_preview = '';
								// Keep only spec values that exist in the target sub-category
								new_doc.spec_values = (new_doc.spec_values || []).filter(
									sv => target_specs.has(sv.spec)
								);
								frappe.set_route('Form', 'CH Model', new_doc.name);
								d.hide();
								frappe.show_alert({
									message: __('Model cloned. Review manufacturer, brand, and spec values before saving.'),
									indicator: 'blue',
								});
							},
						});
					},
				});
				d.show();
			}, __('Actions'));
		}
		ch_model.update_preview(frm);
	},

});

// ─────────────────────────────────────────────────────────────────────────────
// CH Model Spec Value child table events
// ─────────────────────────────────────────────────────────────────────────────
frappe.ui.form.on('CH Model Spec Value', {
	// Row dialog opened: pre-populate spec_value with all allowed values so
	// clicking the field immediately shows a dropdown (no typing required).
	form_render(frm, cdt, cdn) {
		let row = frappe.get_doc(cdt, cdn);
		if (!row || !row.spec) return;

		// Pre-load all attribute values → fills the dropdown on click
		ch_model._load_spec_value_options(frm, cdt, cdn);

		let gf = frm.cur_grid && frm.cur_grid.grid_form;
		if (!gf) return;
		let field = gf.fields_dict['spec_value'];
		if (!field) return;

		let spec = row.spec;
		let url = `/desk/item-attribute/${encodeURIComponent(spec)}`;

		// Remove any previous listener to avoid duplicates
		$(field.input).off('awesomplete-open.ch_new_val');

		$(field.input).on('awesomplete-open.ch_new_val', function () {
			let $ul = $(field.awesomplete.ul);
			// Remove stale entry first
			$ul.find('.ch-create-new-val').remove();

			$ul.append(
				`<li class="ch-create-new-val" role="option"
				     style="border-top:1px solid #d1d8dd;padding:8px 12px;
				            color:var(--primary);cursor:pointer;font-weight:500;">
				     + Create a new ${frappe.utils.escape_html(spec)} value
				 </li>`
			);

			$ul.find('.ch-create-new-val').on('mousedown', function (e) {
				e.preventDefault();
				e.stopPropagation();
				window.open(url, '_blank');
				field.awesomplete.close();
			});
		});
	},

	// Spec changed → clear spec_value and reload the allowed value dropdown
	spec(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, 'spec_value', '');
		// Small delay so the field re-renders before we inject the new list
		setTimeout(() => ch_model._load_spec_value_options(frm, cdt, cdn), 100);
	},

	// Spec value changed → update preview
	spec_value(frm) {
		ch_model.update_preview(frm);
	},
});

// ── CH Model Feature — clear feature_name when group changes ────────────────
frappe.ui.form.on('CH Model Feature', {
	feature_group(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, 'feature_name', '');
	},
});
