// Standalone helpers — NOT inside the form event map
function sdu_extract_scheme(frm) {
	frappe.dom.freeze(__("AI is reading the document..."));
	frappe.xcall(
		"ch_item_master.supplier_scheme.doctype.scheme_document_upload.scheme_document_upload.extract_scheme_data",
		{ upload_name: frm.doc.name }
	)
		.then((r) => {
			frappe.dom.unfreeze();
			frm.reload_doc();
			frappe.show_alert({
				message: __("Extracted {0} scheme part(s). Click 'Review & Create Schemes' to proceed.", [r.schemes_count]),
				indicator: "green",
			});
		})
		.catch((e) => {
			frappe.dom.unfreeze();
			frm.reload_doc();
			frappe.msgprint({
				title: __("Extraction Failed"),
				message: e.message || __("Check Error Log for details"),
				indicator: "red",
			});
		});
}

function sdu_show_review_dialog(frm) {
		let data;
		try {
			data = JSON.parse(frm.doc.extracted_json);
		} catch (e) {
			frappe.msgprint(__("Invalid extracted data"));
			return;
		}

		const schemes = data.schemes || [];
		if (!schemes.length) {
			frappe.msgprint(__("No schemes found in extracted data"));
			return;
		}

		// Build review HTML
		let html = `<div class="scheme-review">`;

		// Top-level info
		html += `<div class="scheme-review-header" style="background:#f8f9fa;padding:12px;border-radius:8px;margin-bottom:16px">
			<div class="row">
				<div class="col-sm-4"><b>Brand:</b> ${frappe.utils.escape_html(data.brand || "—")}</div>
				<div class="col-sm-4"><b>Circular:</b> ${frappe.utils.escape_html(data.circular_number || "—")}</div>
				<div class="col-sm-4"><b>Period:</b> ${data.valid_from || "—"} to ${data.valid_to || "—"}</div>
			</div>
			<div class="row mt-2">
				<div class="col-sm-4"><b>Settlement:</b> ${data.settlement_type || "Credit Note"}</div>
				<div class="col-sm-4"><b>TDS:</b> ${data.tds_applicable ? data.tds_percent + "%" : "No"}</div>
			</div>
		</div>`;

		// Each scheme part
		schemes.forEach((s, i) => {
			const rules = s.rules || [];
			html += `<div class="scheme-part" style="border:1px solid #d1d8dd;border-radius:8px;padding:14px;margin-bottom:12px">
				<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
					<h5 style="margin:0">
						<input type="checkbox" class="scheme-check" data-idx="${i}" checked style="margin-right:8px">
						Part ${i + 1}: ${frappe.utils.escape_html(s.scheme_name)}
					</h5>
					<span class="badge" style="background:#e3f2fd;color:#1565c0">${s.rule_type}</span>
				</div>
				<div class="text-muted" style="font-size:12px;margin-bottom:8px">
					Payout: ${s.payout_basis} | Achievement: ${s.achievement_basis}
					${s.stackable ? ' | <span class="text-warning">Stackable</span>' : ""}
					${s.notes ? " | " + frappe.utils.escape_html(s.notes) : ""}
				</div>`;

			if (rules.length) {
				html += `<table class="table table-sm table-bordered" style="font-size:12px;margin:0">
					<thead><tr style="background:#f5f7fa">
						<th>Series / Model</th>
						<th style="text-align:right">Qty Range</th>
						<th style="text-align:right">Payout/Unit</th>
						<th style="text-align:right">RRP Band</th>
						<th>Slab</th>
						<th>Payout</th>
						<th>Notes</th>
					</tr></thead><tbody>`;

				rules.forEach((r) => {
					const model_label = r.series + (r.model_variant ? ` (${r.model_variant})` : "");
					const qty_label = r.qty_from || r.qty_to
						? `${r.qty_from || 0}–${r.qty_to || "∞"}`
						: "—";
					const rrp_label = r.rrp_from || r.rrp_to
						? `₹${(r.rrp_from||0).toLocaleString()}–₹${(r.rrp_to||0).toLocaleString()}`
						: "—";
					html += `<tr>
						<td>${frappe.utils.escape_html(model_label)}</td>
						<td style="text-align:right">${qty_label}</td>
						<td style="text-align:right">₹${(r.payout_per_unit||0).toLocaleString()}</td>
						<td style="text-align:right">${rrp_label}</td>
						<td>${r.include_in_slab ? "✓" : "—"}</td>
						<td>${r.eligible_for_payout ? "✓" : "—"}</td>
						<td>${frappe.utils.escape_html(r.remarks || "")}</td>
					</tr>`;
				});

				html += `</tbody></table>`;
			}

			html += `</div>`;
		});

		// Terms
		if (data.terms && data.terms.length) {
			html += `<details style="margin-top:8px">
				<summary class="text-muted" style="cursor:pointer;font-size:12px">
					Terms & Conditions (${data.terms.length})
				</summary>
				<ol style="font-size:12px;margin-top:8px">
					${data.terms.map(t => `<li>${frappe.utils.escape_html(t)}</li>`).join("")}
				</ol>
			</details>`;
		}

		html += `</div>`;

		const dialog = new frappe.ui.Dialog({
			title: __("Review Extracted Schemes"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "review_html",
					options: html,
				},
			],
			size: "extra-large",
			primary_action_label: __("Create Selected Schemes"),
			primary_action() {
				// Get checked scheme indices
				const checked = [];
				dialog.$wrapper.find(".scheme-check:checked").each(function () {
					checked.push($(this).data("idx"));
				});

				if (!checked.length) {
					frappe.msgprint(__("Select at least one scheme part"));
					return;
				}

				// Filter schemes
				const selected_data = Object.assign({}, data);
				selected_data.schemes = checked.map(idx => data.schemes[idx]);

				dialog.hide();
				frappe.dom.freeze(__("Creating {0} scheme(s)...", [selected_data.schemes.length]));

				frappe.xcall(
					"ch_item_master.supplier_scheme.doctype.scheme_document_upload.scheme_document_upload.create_schemes_from_upload",
					{
						upload_name: frm.doc.name,
						schemes_json: JSON.stringify(selected_data),
					}
				)
					.then((r) => {
						frappe.dom.unfreeze();
						frm.reload_doc();
						if (r.created && r.created.length) {
							const links = r.created
								.map(s => `<a href="/app/supplier-scheme-circular/${s}">${s}</a>`)
								.join(", ");
							frappe.msgprint({
								title: __("Schemes Created"),
								message: __("Created {0} scheme(s): {1}", [r.count, links])
									+ (r.failed ? `<br>${__("{0} failed", [r.failed])}` : ""),
								indicator: "green",
							});
						}
					})
					.catch((e) => {
						frappe.dom.unfreeze();
						frm.reload_doc();
						frappe.msgprint({
							title: __("Error"),
							message: e.message || __("Failed to create schemes"),
							indicator: "red",
						});
					});
			},
		});

		dialog.show();
		dialog.$wrapper.find(".modal-dialog").css("max-width", "950px");
}

frappe.ui.form.on("Scheme Document Upload", {
	refresh(frm) {
		if (frm.doc.document_file && frm.doc.status === "Pending") {
			frm.add_custom_button(
				__("Extract with AI"),
				() => sdu_extract_scheme(frm),
				__("Actions")
			);
		}

		if (frm.doc.status === "Extracted" && frm.doc.extracted_json) {
			frm.add_custom_button(
				__("Review & Create Schemes"),
				() => sdu_show_review_dialog(frm),
				__("Actions")
			);
			frm.add_custom_button(
				__("Re-extract"),
				() => sdu_extract_scheme(frm),
				__("Actions")
			);
		}

		if (frm.doc.created_schemes) {
			const schemes = frm.doc.created_schemes.split(",").map(s => s.trim());
			const links = schemes
				.map(s => `<a href="/app/supplier-scheme-circular/${s}">${s}</a>`)
				.join(", ");
			frm.set_intro(
				__("Created {0} scheme(s): {1}", [schemes.length, links]),
				"green"
			);
		}

		if (frm.doc.status === "Failed") {
			frm.set_intro(__("Extraction failed. Check the Extraction Log."), "red");
		}
	},

	document_file(frm) {
		if (frm.doc.document_file && frm.doc.status === "Pending") {
			frm.save().then(() => {
				frappe.confirm(
					__("Document uploaded. Extract scheme data with AI now?"),
					() => sdu_extract_scheme(frm)
				);
			});
		}
	},
});
