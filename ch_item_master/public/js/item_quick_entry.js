// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

// Custom Quick Entry for Item — model-driven flow.
//
// Replaces ERPNext's generic quick entry (Item Code, Item Group, HSN, UOM)
// with a CH Model–driven dialog. User selects a model; everything else
// (category, sub-category, item group, HSN, has_variants, attributes,
// specs, features) is auto-populated server-side in before_insert.
//
// Naming: frappe.ui.form.ItemQuickEntryForm — Frappe looks for
// frappe.ui.form[<TrimmedDoctype> + "QuickEntryForm"] before falling
// back to the base class.

frappe.provide("frappe.ui.form");

frappe.ui.form.ItemQuickEntryForm = class ItemQuickEntryForm extends frappe.ui.form.QuickEntryForm {
	constructor(doctype, after_insert, init_callback, doc, force) {
		super(doctype, after_insert, init_callback, doc, force);
		this.skip_redirect_on_error = true;
		this._model_details = null;
	}

	// Always show quick entry (Item's meta may have quick_entry=0)
	is_quick_entry() {
		return true;
	}

	// Replace the default mandatory-field detection with our curated fields
	set_meta_and_mandatory_fields() {
		this.meta = frappe.get_meta(this.doctype);
		this.mandatory = this._get_fields();
	}

	_get_fields() {
		return [
			{
				label: __("CH Model"),
				fieldname: "ch_model",
				fieldtype: "Link",
				options: "CH Model",
				reqd: 1,
				get_query: () => ({
					filters: { disabled: 0 },
					query: "ch_item_master.ch_item_master.api.search_models",
				}),
				onchange: () => this._on_model_change(),
			},
			{
				fieldname: "model_info",
				fieldtype: "HTML",
				options: '<div class="ch-model-preview"></div>',
			},
			{
				fieldtype: "Section Break",
			},
			{
				label: __("Default Unit of Measure"),
				fieldname: "stock_uom",
				fieldtype: "Link",
				options: "UOM",
				default: "Nos",
			},
			{
				fieldtype: "Column Break",
			},
			{
				label: __("Maintain Stock"),
				fieldname: "is_stock_item",
				fieldtype: "Check",
				default: 1,
			},
		];
	}

	_on_model_change() {
		let model = this.dialog.get_value("ch_model");
		let html_field = this.dialog.fields_dict.model_info;

		if (!model) {
			html_field.$wrapper.html("");
			this._model_details = null;
			return;
		}

		html_field.$wrapper.html(
			'<div class="text-muted small" style="padding: 12px;">' +
			__("Loading model details...") + "</div>"
		);

		frappe.call({
			method: "ch_item_master.ch_item_master.api.get_model_details",
			args: { model },
			callback: (r) => {
				let d = r.message || {};
				this._model_details = d;

				let variant_badge = d.has_variants
					? '<span class="indicator-pill green">' + __("Template with Variants") + "</span>"
					: '<span class="indicator-pill blue">' + __("Simple Item") + "</span>";

				let esc = frappe.utils.escape_html;
				let specs_html = "";
				if (d.has_variants && d.spec_selectors && d.spec_selectors.length) {
					let specs = d.spec_selectors.map((s) => esc(s.spec)).join(", ");
					specs_html =
						'<div style="margin-top: 6px;">' +
						'<span class="text-muted">' + __("Variant Attributes:") + "</span> " +
						"<b>" + specs + "</b></div>";
				}

				let html = `
					<div style="background: var(--subtle-fg); padding: 12px; border-radius: var(--border-radius); margin: 4px 0;">
						<div style="display: flex; flex-wrap: wrap; gap: 4px 24px;">
							<div><span class="text-muted">${__("Category")}:</span> <b>${esc(d.category || "-")}</b></div>
							<div><span class="text-muted">${__("Sub Category")}:</span> <b>${esc(d.sub_category || "-")}</b></div>
						</div>
						<div style="display: flex; flex-wrap: wrap; gap: 4px 24px; margin-top: 6px;">
							<div><span class="text-muted">${__("Manufacturer")}:</span> <b>${esc(d.manufacturer || "-")}</b></div>
							<div><span class="text-muted">${__("Brand")}:</span> <b>${esc(d.brand || "-")}</b></div>
						</div>
						<div style="display: flex; flex-wrap: wrap; gap: 4px 24px; margin-top: 6px;">
							<div><span class="text-muted">${__("Item Group")}:</span> <b>${esc(d.item_group || "-")}</b></div>
							<div><span class="text-muted">${__("HSN")}:</span> <b>${esc(d.hsn_code || "-")}</b></div>
							<div><span class="text-muted">${__("GST")}:</span> <b>${d.gst_rate || 0}%</b></div>
						</div>
						${specs_html}
						<div style="margin-top: 8px;">${variant_badge}</div>
					</div>
				`;
				html_field.$wrapper.html(html);
			},
		});
	}

	// Populate all derived fields on the doc before save
	insert() {
		let doc = this.dialog.doc;
		let d = this._model_details;

		// Placeholder for auto-generated item_code (server-side before_insert)
		if (!doc.item_code) {
			doc.item_code = "__autoname";
		}

		if (d) {
			// Hierarchy
			doc.ch_category = d.category || "";
			doc.ch_sub_category = d.sub_category || "";

			// Mandatory fields derived from model
			if (d.item_group) doc.item_group = d.item_group;
			if (d.hsn_code) doc.gst_hsn_code = d.hsn_code;

			// Variant setup
			doc.has_variants = d.has_variants ? 1 : 0;
			if (d.has_variants) {
				doc.variant_based_on = "Item Attribute";
				// Build attributes child table
				doc.attributes = (d.spec_selectors || []).map((s, i) => ({
					idx: i + 1,
					attribute: s.spec,
					doctype: "Item Variant Attribute",
					__islocal: 1,
				}));
			}
		}

		return super.insert();
	}
};
