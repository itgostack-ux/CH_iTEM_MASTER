frappe.ui.form.on("VAS Commission", {
	commission_base_amount: set_amount,
	commission_rate: set_amount,
});

function set_amount(frm) {
	const base = flt(frm.doc.commission_base_amount || 0);
	const rate = flt(frm.doc.commission_rate || 0);
	frm.set_value("commission_amount", flt(base * rate / 100));
}
