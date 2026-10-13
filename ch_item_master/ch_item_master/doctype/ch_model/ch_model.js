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
//
// The whole value list is loaded (Colour has 1,400+ values), not just the
// first 50, and the dropdown is allowed to render all of them. Typing then
// filters the list in the browser — Frappe's Autocomplete does that itself.
// ─────────────────────────────────────────────────────────────────────────────
ch_model._load_spec_value_options = function (frm, cdt, cdn) {
	let row = frappe.get_doc(cdt, cdn);
	if (!row || !row.spec) return;

	let gf = frm.cur_grid && frm.cur_grid.grid_form;
	if (!gf || !gf.fields_dict) return;
	let field = gf.fields_dict['spec_value'];
	if (!field) return;

	const spec = row.spec;
	$(field.input).off('input.ch_search');

	ch_model._fetch_all_values(spec).then((all_values) => {
		// The row may have been closed or pointed at another spec meanwhile.
		if (row.spec !== spec || !document.body.contains(field.input)) return;

		if (all_values && all_values.length) {
			ch_model._set_dropdown_values(field, all_values);
			return;
		}

		// Fallback (user cannot read Items, or nothing came back): first 50
		// values from our own API, searching on the server while typing.
		ch_model._fetch_and_set_values(field, spec, '');
		$(field.input).on('input.ch_search', frappe.utils.debounce(function () {
			let typed = $(this).val() || '';
			ch_model._fetch_and_set_values(field, spec, typed);
		}, 300));
	});
};

// Every value of an Item Attribute, sorted A-Z. Resolves null when the full
// list is not available to this user. Uses ERPNext's own attribute-value
// lookup (the one its variant dialogs use), which has no row limit;
// ch_item_master's get_attribute_values stops at 50.
ch_model._fetch_all_values = function (spec) {
	return new Promise((resolve) => {
		if (!frappe.model.can_read('Item')) {
			resolve(null);
			return;
		}
		frappe.call({
			method: 'erpnext.stock.doctype.item.item.get_item_attribute',
			args: { parent: spec },
			callback(r) {
				let values = [...new Set(
					(r.message || []).map((d) => d.attribute_value).filter(Boolean)
				)];
				values.sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' }));
				resolve(values);
			},
			error() {
				resolve(null);
			},
		});
	});
};

// Put values into the dropdown. One line per value: label and value are the
// same text, so no description is passed (it used to repeat the name on a
// second line, halving how many values fit on screen).
ch_model._set_dropdown_values = function (field, values) {
	let formatted = values.map((v) => ({ label: v, value: v }));

	if (field.set_data) {
		field.set_data(formatted);
	}

	if (field.awesomplete && formatted.length) {
		field.awesomplete.list = formatted;
		// Frappe caps the dropdown at 99 rows; show every value instead.
		field.awesomplete.maxItems = Math.max(formatted.length, 99);
		// Closest matches first while typing (see _relevance_sorter).
		field.awesomplete.sort = ch_model._relevance_sorter(field);
		// Show the full allowed-value list on focus/click without
		// forcing the user to type first (this helper's documented
		// intent). Without minChars=0 the dropdown stayed hidden until
		// a character was typed, so the values looked unselectable.
		field.awesomplete.minChars = 0;
		field.awesomplete.evaluate();
	}
};

// Order the dropdown by how closely each value matches what was typed, so
// typing "black" lists:
//   1. the value itself                      — Black
//   2. values that start with it             — Black & Blue, Black Beauty, ...
//   3. values where a later word starts with it — Absolute Black, Ace Black, ...
//   4. any other match
// Inside each group the list's own A-Z order is kept, and with nothing typed
// the whole list stays A-Z.
ch_model._match_rank = function (text, typed) {
	if (text === typed) return 0;
	if (text.startsWith(typed)) return 1;
	if (text.split(/[^a-z0-9]+/).some((word) => word.startsWith(typed))) return 2;
	return 3;
};

ch_model._relevance_sorter = function (field) {
	let cache = { typed: null, ranks: new Map() };
	const rank = (suggestion, typed) => {
		const text = String(suggestion.label || suggestion.value || suggestion).trim().toLowerCase();
		if (!cache.ranks.has(text)) {
			cache.ranks.set(text, ch_model._match_rank(text, typed));
		}
		return cache.ranks.get(text);
	};
	return function (a, b) {
		const typed = (field.input.value || '').trim().toLowerCase();
		if (!typed) return 0;
		if (cache.typed !== typed) {
			cache = { typed: typed, ranks: new Map() };
		}
		// Equal rank returns 0: the sort is stable, so A-Z order is kept.
		return rank(a, typed) - rank(b, typed);
	};
};

ch_model._fetch_and_set_values = function (field, spec, txt) {
	frappe.call({
		method: 'ch_item_master.ch_item_master.api.get_attribute_values',
		args: {
			spec: spec,
			txt: txt
		},
		async: true,
		callback(r) {
			ch_model._set_dropdown_values(field, r.message || []);
		},
	});
};

// ─────────────────────────────────────────────────────────────────────────────
// Helper: bigger "Editing Row" popup for this form's child tables.
//
// Frappe sizes the row popup at 80% of the window, capped at the page width,
// and only as tall as its fields — for the two-field Spec Values row that is
// a short strip whose Specification / Value dropdowns have to scroll inside
// it. Inside, the fields sit in a centred 900px column at half width, and a
// dropdown is at most 300px tall.
//
// Here the popup is wider and taller, the fields use its full width, and a
// dropdown may fill the popup's height. Change the values below to adjust.
// The rules are scoped by a class on this form's own grids, so no other
// form's row popup is affected.
// ─────────────────────────────────────────────────────────────────────────────
ch_model.ROW_POPUP = {
	width: '92vw',       // share of the window width
	max_width: '1500px', // upper limit on very wide screens
	min_height: '75vh',  // height of the area holding the fields (Frappe's own limit is 80vh)
	// Room kept above an open dropdown for the field labels and inputs; the
	// dropdown gets the rest of min_height.
	dropdown_offset: '200px',
};

ch_model._enlarge_row_popup = function (frm) {
	if (!document.getElementById('ch-model-row-popup-style')) {
		const p = ch_model.ROW_POPUP;
		const popup = '.ch-model-big-row-popup .grid-row-open .form-in-grid';
		$(`<style id="ch-model-row-popup-style">
			${popup} {
				width: ${p.width} !important;
				max-width: ${p.max_width} !important;
			}
			${popup} .grid-form-body {
				min-height: ${p.min_height};
			}
			${popup} .section-body {
				max-width: none;
				margin-left: 0;
				margin-right: 0;
			}
			${popup} .frappe-control.input-max-width {
				max-width: 100%;
			}
			${popup} .awesomplete > [role="listbox"] {
				max-height: max(220px, calc(${p.min_height} - ${p.dropdown_offset}));
			}
		</style>`).appendTo('head');
	}
	['spec_values', 'model_features'].forEach((fieldname) => {
		const field = frm.fields_dict[fieldname];
		if (field && field.grid) {
			field.grid.wrapper.addClass('ch-model-big-row-popup');
		}
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

		// Auto-fetch Brand when the manufacturer maps to exactly one brand
		// (lookup logic lives in ch_model.py).
		const manufacturer = frm.doc.manufacturer;
		if (!manufacturer) return;
		frappe.call({
			method: 'ch_item_master.ch_item_master.doctype.ch_model.ch_model.get_brand_for_manufacturer',
			args: { manufacturer },
			callback(r) {
				// Ignore stale replies and never overwrite a brand picked meanwhile.
				if (r.message && frm.doc.manufacturer === manufacturer && !frm.doc.brand) {
					frm.set_value('brand', r.message);
				}
			},
		});
	},

	brand(frm) {
		ch_model.update_preview(frm);
	},

	model_name(frm) {
		ch_model.update_preview(frm);
	},

	refresh(frm) {
		ch_model._enlarge_row_popup(frm);

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
				frappe.set_route('Form', 'Item Attribute', spec);
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
