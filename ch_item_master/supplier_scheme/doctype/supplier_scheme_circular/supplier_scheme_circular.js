frappe.ui.form.on("Supplier Scheme Circular", {
	refresh(frm) {
		_update_days_remaining(frm);
		_render_confidence_badge(frm);

		// Achievement vs target (only for saved docs)
		if (!frm.is_new()) {
			_render_achievement_section(frm);
			_render_product_scope(frm);
		}
		const status_color = {
			"Draft": "gray",
			"Pending Approval": "orange",
			"Active": "green",
			"Closed": "blue",
			"Cancelled": "red",
		};
		const color = status_color[frm.doc.status] || "gray";
		frm.page.set_indicator(frm.doc.status, color);

		// ---------- MAKER: Submit for Review ----------
		if (frm.doc.docstatus === 0 && frm.doc.status === "Draft" && !frm.is_new()) {
			frm.add_custom_button(__("Submit for Review"), () => {
				frappe.confirm(
					__("Submit this scheme for manager approval? You won't be able to edit it until it is reviewed."),
					() => frm.call("submit_for_review").then(() => frm.reload_doc())
				);
			}).addClass("btn-warning");
		}

		// ---------- CHECKER: Approve / Reject ----------
		const is_approver = frappe.user.has_role(["Purchase Manager", "Scheme Manager", "System Manager"]);
		if (frm.doc.docstatus === 0 && frm.doc.status === "Pending Approval" && is_approver) {
			frm.add_custom_button(__("Approve"), () => {
				frappe.confirm(
					__("Approve and activate this scheme? This cannot be undone without cancellation."),
					() => frm.call("approve_scheme").then(() => frm.reload_doc())
				);
			}, __("Review Actions")).addClass("btn-success");

			frm.add_custom_button(__("Reject"), () => {
				frappe.prompt(
					[{
						fieldname: "reason",
						fieldtype: "Small Text",
						label: __("Rejection Reason"),
						reqd: 1,
						description: __("This will be stored on the scheme and visible to the team."),
					}],
					({ reason }) => {
						frm.call("reject_scheme", { reason }).then(() => frm.reload_doc());
					},
					__("Reject Scheme"),
					__("Reject")
				);
			}, __("Review Actions")).addClass("btn-danger");
		}

		// ---------- Upload Scheme Document (Draft only) ----------
		if (frm.doc.docstatus === 0 && frm.doc.status === "Draft" && !frm.is_new()) {
			frm.add_custom_button(__("Upload Scheme Document"), () => {
				frappe.new_doc("Scheme Document Upload");
			});
		}

		// Make review fields read-only for non-approvers
		if (!is_approver) {
			frm.set_df_property("review_notes", "read_only", 1);
		}

		// On duplicate, Frappe copies the original status — reset it to Draft
		if (frm.is_new() && frm.doc.status !== "Draft") {
			frm.set_value("status", "Draft");
		}

		// Always hide the native Submit button — use our controlled approval flow instead
		// Only hide when doc is already saved (btn_primary = Submit); when new, btn_primary = Save
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			// btn_primary is rendered after refresh; defer so we catch it
			setTimeout(() => {
				frm.page.btn_primary && frm.page.btn_primary.hide();
			}, 0);
			// Override savesubmit — intercepts the button, the "Submit this document to confirm"
			// shortcut link, and any keyboard trigger regardless of Frappe version.
			frm.savesubmit = function () {
				if (frm.doc.status === "Pending Approval" && is_approver) {
					frappe.msgprint({
						title: __("Use Review Actions"),
						message: __("Go to Review Actions → Approve to activate this scheme."),
						indicator: "orange",
					});
				} else {
					frappe.msgprint({
						title: __("Approval Required"),
						message: __("Please use the <b>Submit for Review</b> button to send this scheme for manager approval."),
						indicator: "orange",
					});
				}
			};
		}
	},

	valid_to(frm) {
		_update_days_remaining(frm);
	},

	validate(frm) {
		// Give clear error messages instead of silent beeps
		if (!frm.doc.rules || !frm.doc.rules.length) {
			frappe.msgprint({
				title: __("Missing Rules"),
				message: __("Please add at least one Scheme Rule in the 'Scheme Rules' section below."),
				indicator: "orange",
			});
			frappe.validated = false;
			// Scroll to rules section
			frm.scroll_to_field("rules");
			return;
		}

		// Check each rule row has required fields
		for (let i = 0; i < frm.doc.rules.length; i++) {
			const rule = frm.doc.rules[i];
			if (!rule.rule_name) {
				frappe.msgprint({
					title: __("Incomplete Rule"),
					message: __("Row {0}: Rule Name is required in the Scheme Rules table.", [i + 1]),
					indicator: "orange",
				});
				frappe.validated = false;
				frm.scroll_to_field("rules");
				return;
			}
		}
	},
});

frappe.ui.form.on("Supplier Scheme Rule", {
	rules_add(frm, cdt, cdn) {
		// Set sensible defaults for new rule rows
		const row = locals[cdt][cdn];
		if (!row.rule_type) row.rule_type = "Quantity Slab";
		if (!row.payout_basis) row.payout_basis = "Per Unit";
		if (!row.achievement_basis) row.achievement_basis = "Invoice Date";
		frm.refresh_field("rules");
	},
});

function _update_days_remaining(frm) {
	if (!frm.doc.valid_to) return;
	const today = frappe.datetime.get_today();
	const diff = frappe.datetime.get_diff(frm.doc.valid_to, today);
	const val = diff < 0 ? 0 : diff;
	frm.doc.days_remaining = val;
	frm.refresh_field("days_remaining");
}

function _render_confidence_badge(frm) {
	// Remove any previous badge
	frm.$wrapper.find(".ai-confidence-alert").remove();
	const score = frm.doc.ai_confidence_score;
	if (!score) return;

	const color   = score >= 85 ? "#22c55e" : score >= 70 ? "#f59e0b" : "#ef4444";
	const label   = score >= 85 ? "High confidence" : score >= 70 ? "Review recommended" : "Low confidence — verify carefully";
	const icon    = score >= 85 ? "fa-check-circle" : score >= 70 ? "fa-exclamation-triangle" : "fa-times-circle";

	const $badge = $(`
		<div class="ai-confidence-alert" style="
			margin: 8px 15px;
			padding: 8px 14px;
			border-radius: 6px;
			border-left: 4px solid ${color};
			background: ${color}18;
			display: flex;
			align-items: center;
			gap: 10px;
			font-size: 13px;
		">
			<i class="fa ${icon}" style="color:${color};font-size:16px"></i>
			<span>
				<strong style="color:${color}">AI Confidence: ${score}%</strong>
				&nbsp;·&nbsp; ${label}
			</span>
		</div>
	`);

	// Insert below the page title bar
	frm.$wrapper.find(".form-page").prepend($badge);
}

function _render_achievement_section(frm) {
	if (!frm.doc.name || frm.is_new() || !frm.doc.rules || !frm.doc.rules.length) return;

	frappe.call({
		method: "ch_item_master.supplier_scheme.api.get_rule_achievements",
		args: { scheme: frm.doc.name },
		callback(r) {
			if (!r.message) return;
			const rows = r.message;
			if (!rows.length) return;

			let html = `
				<div style="margin-top:10px">
				<table style="width:100%;border-collapse:collapse;font-size:13px">
					<thead>
						<tr style="background:#f1f5f9;font-weight:600">
							<th style="padding:6px 10px;text-align:left">Rule</th>
							<th style="padding:6px 10px;text-align:right">Target</th>
							<th style="padding:6px 10px;text-align:right">Achieved</th>
							<th style="padding:6px 10px;min-width:120px">Progress</th>
							<th style="padding:6px 10px;text-align:right">Payout Est.</th>
						</tr>
					</thead>
					<tbody>
			`;

			for (const row of rows) {
				const pct = row.target_qty > 0
					? Math.min(100, ((row.achieved_qty / row.target_qty) * 100)).toFixed(0)
					: null;
				const bar_color = pct === null ? "#6b7280"
					: pct >= 100 ? "#22c55e" : pct >= 60 ? "#f59e0b" : "#ef4444";
				const bar = pct !== null
					? `<div style="background:#e5e7eb;border-radius:4px;height:8px;overflow:hidden">
						<div style="width:${pct}%;background:${bar_color};height:100%;border-radius:4px"></div>
					   </div>
					   <span style="font-size:11px;color:#6b7280">${pct}%</span>`
					: `<span style="font-size:11px;color:#6b7280">No target set</span>`;

				html += `
					<tr style="border-bottom:1px solid #f1f5f9">
						<td style="padding:6px 10px">${row.rule_name}</td>
						<td style="padding:6px 10px;text-align:right">${row.target_qty || "—"}</td>
						<td style="padding:6px 10px;text-align:right;font-weight:600">${row.achieved_qty}</td>
						<td style="padding:6px 10px">${bar}</td>
						<td style="padding:6px 10px;text-align:right">${frappe.format(row.estimated_payout, {fieldtype:"Currency"})}</td>
					</tr>`;
			}
			html += `</tbody></table></div>`;

			// Inject into the totals section HTML field — or use a custom section
			const $section = frm.$wrapper.find('[data-fieldname="section_totals"]').closest(".form-section");
			$section.find(".achievement-table").remove();
			$section.append(`<div class="achievement-table">${html}</div>`);
		},
	});
}

function _render_product_scope(frm) {
	const $wrapper = frm.get_field("html_product_scope").$wrapper;
	const is_draft = frm.doc.docstatus === 0 && frm.doc.status === "Draft";

	$wrapper.html(`<div class="text-muted text-center" style="padding:12px"><i class="fa fa-spinner fa-spin"></i> Loading product mappings…</div>`);

	frappe.call({
		method: "frappe.client.get_list",
		args: {
			doctype: "Scheme Product Map",
			filters: { scheme: frm.doc.name },
			fields: [
				"name", "supplier_product_name", "brand",
				"match_level", "model", "item_code", "item_group",
				"mapped_item_count", "mapping_source", "confidence_score",
			],
			limit: 500,
			order_by: "creation asc",
		},
		callback(r) {
			const rows = r.message || [];
			$wrapper.empty();

			// ---- Toolbar ----
			const $toolbar = $(`<div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap"></div>`);
			if (is_draft) {
				$toolbar.append(`
					<button class="btn btn-sm btn-primary" id="ssc-add-map">
						<i class="fa fa-plus"></i> Add Model Mapping
					</button>
				`);
			}
			$toolbar.append(`<span class="text-muted small" style="margin-left:auto">${rows.length} product(s) mapped</span>`);
			if (!is_draft) {
				$toolbar.append(`<span class="badge" style="background:#6366f1;color:#fff;padding:4px 10px">🔒 Locked — ${frm.doc.status}</span>`);
			}
			$wrapper.append($toolbar);

			if (!rows.length) {
			// Check if there are unlinked maps for this brand we can claim
			frappe.call({
				method: "frappe.client.get_list",
				args: {
					doctype: "Scheme Product Map",
					filters: { brand: frm.doc.brand, scheme: ["is", "not set"] },
					fields: ["name", "supplier_product_name", "match_level", "model",
					         "item_code", "item_group", "mapped_item_count", "mapping_source", "confidence_score"],
					limit: 200,
					order_by: "creation asc",
				},
				callback(r2) {
					const unlinked = r2.message || [];
					if (unlinked.length && is_draft) {
						$wrapper.append(`
							<div style="padding:16px;border:1px solid #fbbf24;border-radius:6px;background:#fffbeb">
								<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
									<i class="fa fa-link" style="color:#f59e0b;font-size:16px"></i>
									<b style="color:#92400e">${unlinked.length} existing ${frm.doc.brand} mapping(s) found without a scheme</b>
								</div>
								<div class="text-muted" style="margin-bottom:10px;font-size:12px">
									These were auto-mapped before the scheme link was introduced. Click below to select which ones belong to this scheme.
								</div>
								<button class="btn btn-sm btn-warning" id="ssc-claim-maps">
									<i class="fa fa-link"></i> Claim Existing Mappings for this Scheme
								</button>
							</div>
						`);
						$wrapper.find("#ssc-claim-maps").on("click", () => {
							_show_claim_dialog(frm, unlinked);
						});
					} else {
						$wrapper.append(`<div class="text-muted" style="padding:20px;text-align:center;border:1px dashed #d1d5db;border-radius:6px">
							No product mappings linked to this scheme yet.
							${is_draft ? ' Click <b>Add Model Mapping</b> to add one manually.' : ''}
						</div>`);
					}
				},
			});
			} else {
				const $tbl = $(`<table class="table table-bordered table-sm" style="margin-bottom:0;font-size:13px">
					<thead style="background:#f3f4f6">
						<tr>
							<th>#</th>
							<th>Supplier Product Name</th>
							<th>Match Level</th>
							<th>Mapped To</th>
							<th style="text-align:center">Items</th>
							<th style="text-align:center">Source</th>
							<th style="text-align:center">Conf.</th>
							<th style="text-align:center">Actions</th>
						</tr>
					</thead>
					<tbody></tbody>
				</table>`);
				const $tbody = $tbl.find("tbody");

				rows.forEach((row, idx) => {
					const mapped_to = row.model || row.item_code || row.item_group || "—";
					const source_map = {
						"AI": `<span class="badge" style="background:#6366f1;color:#fff">AI</span>`,
						"Manual": `<span class="badge" style="background:#16a34a;color:#fff">Manual</span>`,
						"Verified": `<span class="badge" style="background:#0ea5e9;color:#fff">✓ Verified</span>`,
					};
					const source_badge = source_map[row.mapping_source] ||
						`<span class="badge badge-default">${row.mapping_source || "—"}</span>`;

					const conf = row.confidence_score || 0;
					const conf_color = conf >= 85 ? "#16a34a" : conf >= 70 ? "#f59e0b" : conf > 0 ? "#dc2626" : "#9ca3af";
					const conf_txt = conf > 0 ? `<span style="color:${conf_color};font-weight:600">${conf}%</span>` : "—";

					const edit_url = `/app/scheme-product-map/${encodeURIComponent(row.name)}`;
					const action_td = is_draft
						? `<td style="text-align:center;white-space:nowrap">
								<a href="${edit_url}" target="_blank" class="btn btn-xs btn-default" title="Edit">
									<i class="fa fa-pencil"></i>
								</a>
								<button class="btn btn-xs btn-danger ssc-del-map" data-name="${row.name}" title="Remove" style="margin-left:4px">
									<i class="fa fa-times"></i>
								</button>
							</td>`
						: `<td style="text-align:center">
								<a href="${edit_url}" target="_blank" class="btn btn-xs btn-default" title="View">
									<i class="fa fa-eye"></i>
								</a>
							</td>`;

					$tbody.append(`<tr>
						<td class="text-muted">${idx + 1}</td>
						<td>${frappe.utils.escape_html(row.supplier_product_name)}</td>
						<td>${frappe.utils.escape_html(row.match_level || "")}</td>
						<td><b>${frappe.utils.escape_html(mapped_to)}</b></td>
						<td style="text-align:center">${row.mapped_item_count || 0}</td>
						<td style="text-align:center">${source_badge}</td>
						<td style="text-align:center">${conf_txt}</td>
						${action_td}
					</tr>`);
				});
				$wrapper.append($tbl);
			}

			// ---- Wire buttons ----
			$wrapper.find("#ssc-add-map").on("click", () => {
				frappe.new_doc("Scheme Product Map", {
					scheme: frm.doc.name,
					brand: frm.doc.brand,
				});
			});

			$wrapper.on("click", ".ssc-del-map", function() {
				const name = $(this).data("name");
				frappe.confirm(
					__("Remove this product mapping? This cannot be undone."),
					() => frappe.db.delete_doc("Scheme Product Map", name).then(() => {
						frappe.show_alert({ message: __("Mapping removed"), indicator: "green" });
						_render_product_scope(frm);
					})
				);
			});
		},
	});
}
function _show_claim_dialog(frm, unlinked) {
	let rows_html = unlinked.map((row, idx) => {
		const mapped_to = row.model || row.item_code || row.item_group || "—";
		return `<tr>
			<td style="text-align:center">
				<input type="checkbox" class="claim-check" data-idx="${idx}" checked>
			</td>
			<td>${frappe.utils.escape_html(row.supplier_product_name)}</td>
			<td>${frappe.utils.escape_html(row.match_level || "—")}</td>
			<td><b>${frappe.utils.escape_html(mapped_to)}</b></td>
			<td style="text-align:center">${row.mapped_item_count || 0}</td>
		</tr>`;
	}).join("");

	const dialog = new frappe.ui.Dialog({
		title: __("Link Existing Mappings to {0}", [frm.doc.name]),
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "map_table",
				options: `
					<p class="text-muted">Select the product mappings that belong to this scheme. Uncheck any that belong to a different scheme.</p>
					<div style="max-height:400px;overflow-y:auto">
					<table class="table table-bordered table-sm" style="font-size:13px">
						<thead style="background:#f3f4f6;position:sticky;top:0">
							<tr>
								<th style="width:36px"><input type="checkbox" id="claim-all" checked title="Select all"></th>
								<th>Supplier Product Name</th>
								<th>Match Level</th>
								<th>Mapped To</th>
								<th style="text-align:center">Items</th>
							</tr>
						</thead>
						<tbody>${rows_html}</tbody>
					</table>
					</div>
				`,
			},
		],
		primary_action_label: __("Link Selected"),
		primary_action() {
			const selected = [];
			dialog.$wrapper.find(".claim-check:checked").each(function() {
				const idx = parseInt($(this).data("idx"));
				selected.push(unlinked[idx].name);
			});
			if (!selected.length) {
				frappe.show_alert({ message: __("Nothing selected"), indicator: "orange" });
				return;
			}
			frappe.dom.freeze(__("Linking {0} mapping(s)...", [selected.length]));
			frm.call("link_existing_maps", { names_json: JSON.stringify(selected) })
				.then(r => {
					frappe.dom.unfreeze();
					dialog.hide();
					frappe.show_alert({
						message: __("{0} mapping(s) linked to this scheme", [r.message.linked]),
						indicator: "green",
					});
					_render_product_scope(frm);
				})
				.catch(() => {
					frappe.dom.unfreeze();
				});
		},
	});

	// Wire select-all checkbox
	dialog.$wrapper.on("change", "#claim-all", function() {
		dialog.$wrapper.find(".claim-check").prop("checked", $(this).is(":checked"));
	});

	dialog.show();
}