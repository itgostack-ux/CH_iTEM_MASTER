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
				filters: allowed.length ? { name: ['in', allowed] } : { name: 'DISABLED' },
			}));
		},
	});
};

// ─────────────────────────────────────────────────────────────────────────────
// CH Model form events
// ─────────────────────────────────────────────────────────────────────────────
frappe.ui.form.on('CH Model', {
	setup(frm) {
		// Brand must belong to selected manufacturer.
		// When no manufacturer is chosen yet, return impossible filter so
		// the dropdown is empty (not showing every brand in the system).
		frm.set_query('brand', () => {
			if (!frm.doc.manufacturer) {
				return { filters: { name: 'DISABLED' } };
			}
			return { filters: { ch_manufacturer: frm.doc.manufacturer } };
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

		// Autocomplete get_query for spec_value in child table:
		// When the user types in the Value field, fetch matching attribute
		// values for the spec (Item Attribute) of the current row.
		frm.fields_dict['spec_values'].grid.get_field('spec_value').get_query = function () {
			let row = frm.cur_grid && frm.cur_grid.doc;
			let spec = (row && row.spec) || '';
			return {
				query: 'ch_item_master.ch_item_master.api.get_attribute_values',
				params: { spec: spec },
			};
		};
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

		if (!frm.is_new()) {
			frm.add_custom_button('Refresh Name Preview', () => ch_model.update_preview(frm));

			// Show existing items for this model
			frm.add_custom_button(__('Show Items'), () => {
				frappe.set_route('List', 'Item', { ch_model: frm.doc.name });
			}, __('View'));

			// ── Generate All Items (bulk variant creation) ───────────────
			if (frm.doc.is_active) {
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
								new_doc.is_active = 1;
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
	// Row dialog opened: hook the Awesomplete dropdown to show a
	// "+ Create a new [Spec] value" entry at the bottom — exactly like
	// Frappe's native Link field does.
	form_render(frm, cdt, cdn) {
		let row = frappe.get_doc(cdt, cdn);
		if (!row || !row.spec) return;

		let gf = frm.cur_grid && frm.cur_grid.grid_form;
		if (!gf) return;
		let field = gf.fields_dict['spec_value'];
		if (!field || !field.awesomplete) return;

		let spec = row.spec;
		let url = `/app/item-attribute/${encodeURIComponent(spec)}`;

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

	// Spec changed → clear spec_value so user must re-select
	spec(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, 'spec_value', '');
	},

	// Spec value changed → update preview
	spec_value(frm) {
		ch_model.update_preview(frm);
	},
});
