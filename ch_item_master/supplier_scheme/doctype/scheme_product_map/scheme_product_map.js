frappe.ui.form.on("Scheme Product Map", {
	refresh(frm) {
		if (!frm.is_new() && frm.doc.mapping_source !== "Verified") {
			frm.add_custom_button(
				__("Mark as Verified"),
				() => {
					frm.call("mark_verified").then(() => {
						frm.reload_doc();
						frappe.show_alert({
							message: __("Mapping verified"),
							indicator: "green",
						});
						// Return to scheme if opened from the scheme form
						if (frm.doc.scheme) {
							setTimeout(() => {
								frappe.set_route("Form", "Supplier Scheme Circular", frm.doc.scheme);
							}, 1500);
						}
					});
				}
			);
		}

		// "Back to Scheme" breadcrumb button
		if (!frm.is_new() && frm.doc.scheme) {
			frm.add_custom_button(__("Back to Scheme"), () => {
				frappe.set_route("Form", "Supplier Scheme Circular", frm.doc.scheme);
			});
		}

		// Show item count indicator
		if (frm.doc.mapped_item_count) {
			frm.dashboard.add_indicator(
				__("{0} items matched", [frm.doc.mapped_item_count]),
				frm.doc.mapped_item_count > 0 ? "green" : "orange"
			);
		}
	},

	match_level(frm) {
		// Clear irrelevant fields when match level changes
		if (frm.doc.match_level !== "Item") frm.set_value("item_code", "");
		if (frm.doc.match_level !== "Model") frm.set_value("model", "");
		if (frm.doc.match_level !== "Item Group") frm.set_value("item_group", "");
	},

	brand(frm) {
		// Filter link fields by brand
		_set_brand_filters(frm);
	},

	onload(frm) {
		_set_brand_filters(frm);
		// Auto-fill scheme & brand when opened via "Add Model Mapping" from scheme form
		if (frm.is_new()) {
			const p = frappe.utils.get_url_params();
			if (p.scheme) {
				frm.set_value("scheme", decodeURIComponent(p.scheme));
				frappe.db.get_value("Supplier Scheme Circular", decodeURIComponent(p.scheme), "brand")
					.then(r => {
						if (r && r.message && r.message.brand) {
							frm.set_value("brand", r.message.brand);
						}
					});
			}
		}
	},
});

function _set_brand_filters(frm) {
	const brand = frm.doc.brand;
	if (brand) {
		frm.set_query("item_code", () => ({
			filters: { brand: brand, disabled: 0 },
		}));
		frm.set_query("model", () => ({
			filters: { brand: brand, disabled: 0 },
		}));
	}
}
