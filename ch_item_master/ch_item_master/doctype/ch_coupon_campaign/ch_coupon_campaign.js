// Copyright (c) 2026, GoStack and contributors
// CH Coupon Campaign — form controller

frappe.ui.form.on("CH Coupon Campaign", {
	refresh(frm) {
		// Status indicator colours
		const colours = {
			Draft: "red",
			Active: "green",
			Paused: "orange",
			Completed: "blue",
			Expired: "darkgrey",
			Cancelled: "red",
		};
		frm.page.set_indicator(frm.doc.status, colours[frm.doc.status] || "grey");

		// ── Buttons for submitted campaigns ──────────────────────────────
		if (frm.doc.docstatus === 1 && frm.doc.status === "Active") {
			frm.add_custom_button(
				__("Refresh Stats"),
				() => {
					frm.call("refresh_stats").then(() => frm.reload_doc());
				},
				__("Actions")
			);

			frm.add_custom_button(
				__("Mark All Distributed"),
				() => {
					frappe.confirm(
						__("Mark all Generated codes as Distributed?"),
						() => {
							frm.call({ method: "mark_distributed", args: { channel: "Manual" } }).then(
								(r) => {
									frappe.show_alert({
										message: __("{0} codes marked as distributed", [r.message.updated]),
										indicator: "green",
									});
									frm.reload_doc();
								}
							);
						}
					);
				},
				__("Actions")
			);

			frm.add_custom_button(
				__("Export Codes CSV"),
				() => {
					frm.call("export_codes").then((r) => {
						if (!r.message || !r.message.length) {
							frappe.msgprint(__("No codes to export"));
							return;
						}
						_download_csv(r.message, frm.doc.campaign_name);
					});
				},
				__("Actions")
			);
		}

		// ── Performance summary banner ───────────────────────────────────
		if (frm.doc.docstatus === 1 && frm.doc.total_codes_generated > 0) {
			const html = `
				<div class="campaign-stats-banner" style="display:flex;gap:24px;
					padding:12px 16px;background:var(--subtle-fg);border-radius:8px;
					margin-bottom:16px;flex-wrap:wrap;">
					<div><strong>${frm.doc.total_codes_generated}</strong><br>
						<span class="text-muted">Codes</span></div>
					<div><strong>${frm.doc.total_distributed}</strong><br>
						<span class="text-muted">Distributed</span></div>
					<div><strong style="color:var(--green-600)">${frm.doc.total_redeemed}</strong><br>
						<span class="text-muted">Redeemed</span></div>
					<div><strong>${format_currency(frm.doc.total_discount_given)}</strong><br>
						<span class="text-muted">Discount Given</span></div>
					<div><strong style="color:var(--blue-600)">
						${format_currency(frm.doc.total_revenue_generated)}</strong><br>
						<span class="text-muted">Revenue</span></div>
					<div><strong>${(frm.doc.redemption_rate || 0).toFixed(1)}%</strong><br>
						<span class="text-muted">Redemption Rate</span></div>
				</div>`;
			frm.set_df_property("section_codes", "description", html);
		}
	},

	campaign_type(frm) {
		// Clear irrelevant fields when switching type
		if (frm.doc.campaign_type === "Coupon Code") {
			frm.set_value("voucher_type", "");
			frm.set_value("face_value", 0);
			frm.set_value("voucher_quantity", 0);
		} else if (frm.doc.campaign_type === "Voucher") {
			frm.set_value("discount_type", "");
			frm.set_value("discount_value", 0);
			frm.set_value("coupon_quantity", 0);
		}
	},

	code_generation(frm) {
		if (frm.doc.code_generation === "Single Shared Code") {
			frm.set_value("max_use_per_code", 1);
		}
	},
});

// ── Helpers ─────────────────────────────────────────────────────────────────

function _download_csv(rows, filename) {
	const headers = ["Code", "Type", "Status", "Assigned To", "Customer Name", "Distributed Via"];
	const csv_rows = [headers.join(",")];
	rows.forEach((r) => {
		csv_rows.push(
			[r.code, r.type, r.status, r.assigned_to, r.assigned_to_name, r.distributed_via]
				.map((v) => `"${(v || "").replace(/"/g, '""')}"`)
				.join(",")
		);
	});
	const blob = new Blob([csv_rows.join("\n")], { type: "text/csv" });
	const url = URL.createObjectURL(blob);
	const a = document.createElement("a");
	a.href = url;
	a.download = `${filename.replace(/[^a-zA-Z0-9]/g, "_")}_codes.csv`;
	a.click();
	URL.revokeObjectURL(url);
}
