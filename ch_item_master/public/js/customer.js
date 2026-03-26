// Customer form — live validation for phone, PAN, and Aadhaar fields.

const PAN_RE = /^[A-Z]{5}[0-9]{4}[A-Z]$/;
const AADHAAR_RE = /^[2-9]\d{11}$/;
const PHONE_RE = /^[6-9]\d{9}$/;

function normalize_phone(raw) {
	let s = (raw || "").replace(/[\s\-().]/g, "");
	if (s.startsWith("+91")) s = s.slice(3);
	else if (s.startsWith("0091")) s = s.slice(4);
	else if (s.startsWith("91") && s.length === 12) s = s.slice(2);
	if (s.startsWith("0") && s.length === 11) s = s.slice(1);
	return s;
}

frappe.ui.form.on("Customer", {
	mobile_no(frm) {
		_validate_phone(frm, "mobile_no", "Mobile Number");
	},
	ch_alternate_phone(frm) {
		_validate_phone(frm, "ch_alternate_phone", "Alternate Phone");
	},
	ch_whatsapp_number(frm) {
		_validate_phone(frm, "ch_whatsapp_number", "WhatsApp Number");
	},

	ch_id_type(frm) {
		_validate_id_number(frm);
	},
	ch_id_number(frm) {
		_validate_id_number(frm);
	},

	ch_aadhaar_number(frm) {
		const val = (frm.doc.ch_aadhaar_number || "").replace(/[\s\-]/g, "");
		if (val && !AADHAAR_RE.test(val)) {
			frappe.show_alert({
				message: __("Invalid Aadhaar. Must be 12 digits, not starting with 0 or 1."),
				indicator: "orange",
			});
		}
	},
});

function _validate_phone(frm, fieldname, label) {
	const raw = frm.doc[fieldname];
	if (!raw) return;
	const digits = normalize_phone(raw);
	if (!PHONE_RE.test(digits)) {
		frappe.show_alert({
			message: __('{0} must be a valid 10-digit Indian mobile number (starting with 6-9).', [label]),
			indicator: "orange",
		});
	}
}

function _validate_id_number(frm) {
	const id_type = frm.doc.ch_id_type;
	const id_number = (frm.doc.ch_id_number || "").trim();
	if (!id_type || !id_number) return;

	if (id_type === "PAN Card") {
		if (!PAN_RE.test(id_number.toUpperCase())) {
			frappe.show_alert({
				message: __("Invalid PAN. Format: ABCDE1234F (5 letters + 4 digits + 1 letter)."),
				indicator: "orange",
			});
		}
	} else if (id_type === "Aadhar Card") {
		const clean = id_number.replace(/[\s\-]/g, "");
		if (!AADHAAR_RE.test(clean)) {
			frappe.show_alert({
				message: __("Invalid Aadhaar. Must be 12 digits, not starting with 0 or 1."),
				indicator: "orange",
			});
		}
	}
}
