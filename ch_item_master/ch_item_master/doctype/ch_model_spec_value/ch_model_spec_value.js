// Copyright (c) 2026, GoStack and contributors
// CH Model Spec Value -- child table client script
//
// NOTE: Frappe does NOT load this file when the child table is embedded in a
// parent form (e.g. CH Model). All child-table event handlers live in the
// parent doctype's JS (ch_model.js). This file only runs if the user
// navigates directly to /app/ch-model-spec-value, which is unusual.

frappe.ui.form.on("CH Model Spec Value", {
	// placeholder — all real logic is in ch_model.js
});
