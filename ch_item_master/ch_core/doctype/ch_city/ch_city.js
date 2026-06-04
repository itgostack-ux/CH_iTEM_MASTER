frappe.ui.form.on("CH City", {
    refresh(frm) {
        frm.set_query("state", () => ({ filters: { disabled: 0 } }));
    },
});
