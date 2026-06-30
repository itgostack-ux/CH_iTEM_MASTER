// Copyright (c) 2026, Congruence Holdings and contributors
// For license information, please see license.txt

frappe.ui.form.on("CH Store", {
    refresh(frm) {
        if (frm.doc.is_buyback_enabled) {
            frm.dashboard.set_headline_alert(
                '<span class="indicator green">Buyback Enabled</span>'
            );
        }
        frm.set_query("state", () => ({ filters: { disabled: 0 } }));

        if (!frm.is_new()) {
            frm.add_custom_button(__("In-Transit Stock"), () => show_in_transit(frm), __("View"));
        }
    },
});

// Read-only: stock currently in transit TO this store (dispatched on an active
// manifest, not yet received). Transit is company-level (Goods In Transit);
// the destination + ETA come from the manifest.
function show_in_transit(frm) {
    frappe.call({
        method: "ch_item_master.ch_core.bin_transfer.get_store_in_transit",
        args: { store: frm.doc.name },
        freeze: true,
        freeze_message: __("Loading in-transit stock..."),
        callback: (r) => {
            const rows = (r.message || {}).serials || [];
            const d = new frappe.ui.Dialog({
                title: __("In-Transit to {0}", [frm.doc.store_name || frm.doc.name]),
                size: "large",
                fields: [{ fieldname: "html", fieldtype: "HTML" }],
            });
            let body;
            if (!rows.length) {
                body = `<div style="padding:24px;text-align:center;color:#6b7280">
                    ${__("Nothing in transit to this store right now.")}</div>`;
            } else {
                body = `<table class="table table-bordered" style="font-size:13px">
                    <thead><tr style="background:#f9fafb">
                        <th>${__("Item")}</th><th style="width:60px">${__("Qty")}</th>
                        <th>${__("From")}</th><th>${__("Manifest")}</th>
                        <th style="width:110px">${__("Status")}</th><th>${__("ETA")}</th>
                    </tr></thead><tbody>
                    ${rows.map((x) => `<tr>
                        <td><b>${frappe.utils.escape_html(x.item_code || "")}</b>
                            <div style="color:#6b7280;font-size:11px">${frappe.utils.escape_html(x.item_name || "")}${x.serial_no ? " · " + frappe.utils.escape_html(x.serial_no) : ""}</div></td>
                        <td>${frappe.utils.escape_html(String(x.qty || ""))}</td>
                        <td>${frappe.utils.escape_html(x.source || "—")}</td>
                        <td><a href="/app/ch-transfer-manifest/${encodeURIComponent(x.manifest)}" target="_blank">${frappe.utils.escape_html(x.manifest || "")}</a></td>
                        <td><span class="indicator-pill blue">${frappe.utils.escape_html(x.status || "—")}</span></td>
                        <td>${x.eta ? frappe.datetime.str_to_user(x.eta) : "—"}</td>
                    </tr>`).join("")}
                    </tbody></table>
                    <div style="color:#94a3b8;font-size:11px;margin-top:6px">
                        ${__("{0} line(s) in transit · company-level Goods In Transit", [rows.length])}</div>`;
            }
            d.fields_dict.html.$wrapper.html(body);
            d.show();
        },
    });
}
