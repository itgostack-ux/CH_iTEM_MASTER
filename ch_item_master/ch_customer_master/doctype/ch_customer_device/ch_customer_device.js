// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.ui.form.on("CH Customer Device", {
	setup(frm) {
		// Filter serial_no to only serials matching the selected item_code
		frm.set_query("serial_no", () => {
			const filters = {};
			if (frm.doc.item_code) {
				filters.item_code = frm.doc.item_code;
			}
			return { filters };
		});
	},

	item_code(frm) {
		// When item_code changes:
		// 1. Clear serial_no if it belongs to a different item
		if (frm.doc.serial_no && frm.doc.item_code) {
			frappe.db.get_value("Serial No", frm.doc.serial_no, "item_code").then(({ message }) => {
				if (message && message.item_code !== frm.doc.item_code) {
					frm.set_value("serial_no", "");
				}
			});
		}

		// 2. Fetch colour, storage and warranty months from Item Variant Attributes
		if (frm.doc.item_code) {
			frappe.call({
				method: "frappe.client.get_list",
				args: {
					doctype: "Item Variant Attribute",
					filters: {
						parent: frm.doc.item_code,
						attribute: ["in", ["Colour", "Storage", "RAM"]],
					},
					fields: ["attribute", "attribute_value"],
				},
				callback(r) {
					if (!r.message) return;
					const attrMap = {};
					(r.message || []).forEach(a => {
						if (a.attribute_value) attrMap[a.attribute] = a.attribute_value;
					});
					if (attrMap["Colour"]) frm.set_value("color", attrMap["Colour"]);
					if (attrMap["Storage"]) frm.set_value("storage_capacity", attrMap["Storage"]);
				},
			});

			// Fetch default warranty months from item
			frappe.db.get_value("Item", frm.doc.item_code, "ch_default_warranty_months").then(({ message }) => {
				if (message && message.ch_default_warranty_months && !frm.doc.warranty_months) {
					frm.set_value("warranty_months", message.ch_default_warranty_months);
				}
			});
		}
	},

	serial_no(frm) {
		// When serial_no is selected, auto-populate item_code if not already set
		if (frm.doc.serial_no && !frm.doc.item_code) {
			frappe.db.get_value("Serial No", frm.doc.serial_no, "item_code").then(({ message }) => {
				if (message && message.item_code) {
					frm.set_value("item_code", message.item_code);
					// item_code trigger above will then fetch colour/storage/warranty
				}
			});
		}
	},
});
