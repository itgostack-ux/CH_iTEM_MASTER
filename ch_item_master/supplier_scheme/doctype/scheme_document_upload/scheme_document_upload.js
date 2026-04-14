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
				message: __("Extracted {0} scheme part(s). Click 'Resolve Products' to map to your Item Master.", [r.schemes_count]),
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

// ---------------------------------------------------------------------------
// Product Resolution — the NEW step between extraction and scheme creation
// ---------------------------------------------------------------------------

function sdu_resolve_products(frm) {
	let data;
	try {
		data = JSON.parse(frm.doc.extracted_json);
	} catch (e) {
		frappe.msgprint(__("Invalid extracted data"));
		return;
	}

	const brand = frm.doc.brand || data.brand;
	if (!brand) {
		frappe.msgprint(__("Please set the Brand before resolving products"));
		return;
	}

	frappe.dom.freeze(__("Resolving products against Item Master..."));
	frappe.xcall(
		"ch_item_master.supplier_scheme.product_mapper.resolve_scheme_products",
		{ brand: brand, schemes_json: JSON.stringify(data) }
	)
		.then((result) => {
			frappe.dom.unfreeze();
			// Store the resolved data back
			const enriched = Object.assign({}, data, { schemes: result.schemes });
			frm.doc.extracted_json = JSON.stringify(enriched, null, 2);
			frm.dirty();
			frm.save();

			_show_mapping_review_dialog(frm, result, brand);
		})
		.catch((e) => {
			frappe.dom.unfreeze();
			frappe.msgprint({
				title: __("Resolution Failed"),
				message: e.message || __("Check Error Log for details"),
				indicator: "red",
			});
		});
}

function _show_mapping_review_dialog(frm, result, brand) {
	const stats = result.stats || {};
	const new_mappings = result.new_mappings || [];
	const schemes = result.schemes || [];

	// Build stats banner
	let html = `<div style="margin-bottom:16px">
		<div class="row" style="background:#f8f9fa;padding:12px;border-radius:8px">
			<div class="col-sm-3"><b>Total Products:</b> ${stats.total || 0}</div>
			<div class="col-sm-3"><span style="color:green"><b>✓ Resolved:</b></span> ${stats.resolved || 0}</div>
			<div class="col-sm-3"><span style="color:#e67e22"><b>⚡ AI Suggested:</b></span> ${stats.ai_suggested || 0}</div>
			<div class="col-sm-3"><span style="color:red"><b>✗ Unresolved:</b></span> ${stats.unresolved || 0}</div>
		</div>
	</div>`;

	// Product mapping table
	html += `<div style="max-height:500px;overflow-y:auto">
		<table class="table table-sm table-bordered" style="font-size:12px">
		<thead><tr style="background:#f5f7fa;position:sticky;top:0">
			<th style="width:30px"></th>
			<th>Supplier Product</th>
			<th>Series</th>
			<th>Variant</th>
			<th>Status</th>
			<th>Match Level</th>
			<th>Mapped To</th>
			<th>Confidence</th>
		</tr></thead><tbody>`;

	let row_idx = 0;
	for (const scheme of schemes) {
		for (const line of (scheme.rules || [])) {
			const product_key = [line.series || "", line.model_variant || ""].filter(Boolean).join(" ");
			const status = line._mapping_status || "unresolved";
			const match_level = line._match_level || "";
			const mapped_to = line._item_code || line._model || line._item_group || "";
			const confidence = line._confidence || "";
			const source = line._mapping_source || "";

			let status_badge = "";
			if (status === "resolved") {
				status_badge = `<span class="badge" style="background:#d4edda;color:#155724">✓ Mapped</span>`;
			} else if (status === "ai_suggested") {
				status_badge = `<span class="badge" style="background:#fff3cd;color:#856404">⚡ AI</span>`;
			} else {
				status_badge = `<span class="badge" style="background:#f8d7da;color:#721c24">✗ Unmapped</span>`;
			}

			const confidence_display = confidence
				? `<span style="color:${confidence >= 85 ? 'green' : confidence >= 60 ? '#e67e22' : 'red'}">${confidence}%</span>`
				: "—";

			const is_editable = status !== "resolved";
			const checkbox = is_editable
				? `<input type="checkbox" class="mapping-confirm" data-idx="${row_idx}" ${status === "ai_suggested" && confidence >= 85 ? "checked" : ""}>`
				: `<input type="checkbox" checked disabled>`;

			html += `<tr data-idx="${row_idx}" style="${status === 'unresolved' ? 'background:#fff8f8' : ''}">
				<td style="text-align:center">${checkbox}</td>
				<td><b>${frappe.utils.escape_html(product_key || "—")}</b></td>
				<td>${frappe.utils.escape_html(line.series || "—")}</td>
				<td>${frappe.utils.escape_html(line.model_variant || "—")}</td>
				<td>${status_badge}</td>
				<td>${match_level || "—"}</td>
				<td>${frappe.utils.escape_html(mapped_to || "—")}</td>
				<td>${confidence_display}</td>
			</tr>`;
			row_idx++;
		}
	}

	html += `</tbody></table></div>`;

	// New AI mappings section
	if (new_mappings.length) {
		html += `<div style="margin-top:16px;padding:12px;background:#fff8e1;border-radius:8px">
			<h5 style="margin:0 0 8px 0">⚡ ${new_mappings.length} New AI-Suggested Mapping(s)</h5>
			<p style="font-size:12px;color:#666;margin-bottom:8px">
				Check the ones you want to save. These will be remembered for future scheme imports.
			</p>
			<table class="table table-sm" style="font-size:12px;margin:0">
			<thead><tr style="background:#fff3cd">
				<th style="width:30px"><input type="checkbox" id="check-all-new" checked></th>
				<th>Product Name</th>
				<th>→</th>
				<th>Maps To</th>
				<th>Confidence</th>
				<th>AI Reasoning</th>
			</tr></thead><tbody>`;

		new_mappings.forEach((m, i) => {
			const target = m.item_code || m.model || m.item_group || "—";
			html += `<tr>
				<td><input type="checkbox" class="new-mapping-check" data-idx="${i}" checked></td>
				<td>${frappe.utils.escape_html(m.supplier_product_name)}</td>
				<td>→</td>
				<td><b>${frappe.utils.escape_html(target)}</b> <span class="text-muted">(${m.match_level})</span></td>
				<td>${m.confidence_score || 0}%</td>
				<td style="max-width:200px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis"
				    title="${frappe.utils.escape_html(m.ai_reasoning || '')}">
					${frappe.utils.escape_html(m.ai_reasoning || "—")}
				</td>
			</tr>`;
		});

		html += `</tbody></table></div>`;
	}

	if (stats.unresolved > 0) {
		html += `<div style="margin-top:12px;padding:10px;background:#f8d7da;border-radius:8px;font-size:12px">
			<b>⚠ ${stats.unresolved} product(s) could not be mapped.</b>
			They will be created without item links. You can manually map them later
			from the Scheme Product Map list or edit the Scheme Circular directly.
		</div>`;
	}

	const dialog = new frappe.ui.Dialog({
		title: __("Product Mapping Review"),
		fields: [{ fieldtype: "HTML", fieldname: "mapping_html", options: html }],
		size: "extra-large",
		primary_action_label: __("Save Mappings & Continue"),
		primary_action() {
			// Collect confirmed new mappings
			const confirmed = [];
			dialog.$wrapper.find(".new-mapping-check:checked").each(function () {
				const idx = $(this).data("idx");
				confirmed.push(new_mappings[idx]);
			});

			if (confirmed.length) {
				frappe.dom.freeze(__("Saving {0} mapping(s)...", [confirmed.length]));
				frappe.xcall(
					"ch_item_master.supplier_scheme.product_mapper.save_mappings",
					{ mappings_json: JSON.stringify(confirmed) }
				)
					.then((r) => {
						frappe.dom.unfreeze();
						frappe.show_alert({
							message: __("Saved {0} product mapping(s)", [r.saved]),
							indicator: "green",
						});
						dialog.hide();
						// Now show the standard review dialog
						sdu_show_review_dialog(frm);
					})
					.catch((e) => {
						frappe.dom.unfreeze();
						frappe.msgprint(__("Error saving mappings: ") + (e.message || ""));
					});
			} else {
				dialog.hide();
				sdu_show_review_dialog(frm);
			}
		},
		secondary_action_label: __("Skip to Create Schemes"),
		secondary_action() {
			dialog.hide();
			sdu_show_review_dialog(frm);
		},
	});

	dialog.show();
	dialog.$wrapper.find(".modal-dialog").css("max-width", "1100px");

	// Check-all handler for new mappings
	dialog.$wrapper.find("#check-all-new").on("change", function () {
		const checked = $(this).prop("checked");
		dialog.$wrapper.find(".new-mapping-check").prop("checked", checked);
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
			const already_created = s._created_doc || null;
			const part_style = already_created
				? "border:1px solid #c3e6cb;border-radius:8px;padding:14px;margin-bottom:12px;background:#f8fff9;opacity:0.85"
				: "border:1px solid #d1d8dd;border-radius:8px;padding:14px;margin-bottom:12px";
			html += `<div class="scheme-part" style="${part_style}">
				<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
					<h5 style="margin:0">
						${
							already_created
								? `<input type="checkbox" class="scheme-check" data-idx="${i}" disabled style="margin-right:8px">`
								: `<input type="checkbox" class="scheme-check" data-idx="${i}" checked style="margin-right:8px">`
						}
						Part ${i + 1}: ${frappe.utils.escape_html(s.scheme_name)}
					</h5>
					<span style="display:flex;gap:6px;align-items:center">
						${already_created
							? `<a href="/desk/supplier-scheme-circular/${already_created}" target="_blank" class="badge" style="background:#d4edda;color:#155724">✓ ${already_created}</a>`
							: ""}
						<span class="badge" style="background:#e3f2fd;color:#1565c0">${s.rule_type}</span>
					</span>
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
						<th>Mapped To</th>
						<th style="text-align:right">Qty Range</th>
						<th style="text-align:right">Payout/Unit</th>
						<th style="text-align:right">RRP Band</th>
						<th>Slab</th>
						<th>Payout</th>
						<th>Notes</th>
					</tr></thead><tbody>`;

				rules.forEach((r) => {
					const model_label = r.series + (r.model_variant ? ` (${r.model_variant})` : "");
					const mapped = r._item_code || r._model || r._item_group || "";
					const mapped_badge = mapped
						? `<span class="badge" style="background:#d4edda;color:#155724;font-size:10px">${frappe.utils.escape_html(mapped)}</span>`
						: `<span class="text-muted" style="font-size:10px">—</span>`;
					const qty_label = r.qty_from || r.qty_to
						? `${r.qty_from || 0}–${r.qty_to || "∞"}`
						: "—";
					const rrp_label = r.rrp_from || r.rrp_to
						? `₹${(r.rrp_from||0).toLocaleString()}–₹${(r.rrp_to||0).toLocaleString()}`
						: "—";
					html += `<tr>
						<td>${frappe.utils.escape_html(model_label)}</td>
						<td>${mapped_badge}</td>
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
						const links = (r.created || [])
							.map(s => `<a href="/desk/supplier-scheme-circular/${s}">${s}</a>`)
							.join(", ");
						const failed_names = r.failed_names || [];
						let msg = __("Created {0} scheme(s): {1}", [r.count || 0, links || "—"]);
						if (failed_names.length) {
							msg += `<br><span style="color:#e67e22">⚠ ${failed_names.length} failed: ${failed_names.map(n => frappe.utils.escape_html(n)).join(", ")}</span>`
								+ `<br><small>Use <b>Create Remaining Schemes</b> to retry.</small>`;
						}
						frappe.msgprint({
							title: failed_names.length ? __("Partially Created") : __("Schemes Created"),
							message: msg,
							indicator: failed_names.length ? "orange" : "green",
						});
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
				__("Resolve Products"),
				() => sdu_resolve_products(frm),
				__("Actions")
			);
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

		if (frm.doc.status === "Partial" && frm.doc.extracted_json) {
			frm.add_custom_button(
				__("Create Remaining Schemes"),
				() => sdu_show_review_dialog(frm),
				__("Actions")
			);
		}

		if (frm.doc.created_schemes) {
			const schemes = frm.doc.created_schemes.split(",").map(s => s.trim());
			const links = schemes
				.map(s => `<a href="/desk/supplier-scheme-circular/${s}">${s}</a>`)
				.join(", ");
			const partial = frm.doc.status === "Partial";
			frm.set_intro(
				__(partial
					? "Partially created — {0} scheme(s): {1}. Use <b>Create Remaining Schemes</b> to finish."
					: "Created {0} scheme(s): {1}",
					[schemes.length, links]),
				partial ? "orange" : "green"
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
