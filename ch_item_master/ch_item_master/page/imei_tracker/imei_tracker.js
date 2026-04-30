/**
 * IMEI Tracker — Hub page
 * =======================
 * A single-pane lifecycle tracker for serialized inventory:
 *   • Real IMEIs (mobile devices, 15-digit manufacturer IDs)
 *   • System-generated barcode serials (laptops, accessories, anything else)
 *
 * Features:
 *   - Cascade hub filter (Company → City → Zone → Store, plus dates)
 *   - 9 KPI cards (Total / IMEI / Non-IMEI / In Stock / Sold MTD / In Service /
 *     Returned / Bought Back / Out of Pool)
 *   - 6 status bucket tabs with live counts
 *   - IMEI / Non-IMEI / All toggle
 *   - Searchable table (item code, brand, IMEI partial, last 6-digit search)
 *   - Aging badges (>90d unsold, >30d in service)
 *   - Click row → drill-through dialog with full unified timeline
 *   - Bulk actions: Mark Scrapped / Lost / In Stock (Stock Manager+ only)
 *   - CSV export
 *
 * Backend: ch_item_master.ch_item_master.page.imei_tracker.imei_tracker_api
 */
frappe.provide("ch_item_master.imei_tracker");

frappe.pages["imei-tracker"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("IMEI Tracker"),
		single_column: true,
	});

	new ch_item_master.imei_tracker.View(page);
};

ch_item_master.imei_tracker.View = class IMEITrackerView {
	constructor(page) {
		this.page = page;
		this.imei_mode = "all"; // "all" | "imei" | "non_imei"
		this.active_bucket = null; // null = no filter
		this.aging_filter = null;
		this.selected_serials = new Set();
		this.api_base = "ch_item_master.ch_item_master.page.imei_tracker.imei_tracker_api";

		this.setup_layout();
		this.setup_filters();
		this.setup_actions();
		this.refresh();
	}

	// ── Layout ────────────────────────────────────────────────────────────────
	setup_layout() {
		const $body = $(this.page.body);
		$body.html(`
			<div class="imei-tracker">
				<div class="imei-tracker-controls">
					<div class="imei-mode-group btn-group btn-group-sm" role="group" aria-label="IMEI mode">
						<button type="button" class="btn btn-default active" data-mode="all">${__("All")}</button>
						<button type="button" class="btn btn-default" data-mode="imei">${__("IMEI Only")}</button>
						<button type="button" class="btn btn-default" data-mode="non_imei">${__("Non-IMEI")}</button>
					</div>
					<div class="imei-search-wrap">
						<input type="text" class="form-control input-sm imei-search"
						       placeholder="${__("Search by IMEI / Serial / Item / last 6 digits…")}"/>
					</div>
					<div class="imei-aging-group btn-group btn-group-sm">
						<button type="button" class="btn btn-default" data-aging="unsold_90">
							${__("Unsold >90d")}
						</button>
						<button type="button" class="btn btn-default" data-aging="in_service_30">
							${__("Service >30d")}
						</button>
						<button type="button" class="btn btn-default" data-aging="">${__("Clear")}</button>
					</div>
				</div>

				<div class="imei-kpi-row"></div>

				<div class="imei-bucket-tabs"></div>

				<div class="imei-bulk-bar" style="display:none">
					<span class="bulk-count"></span>
					<button class="btn btn-xs btn-warning bulk-act" data-action="In Stock">${__("Mark In Stock")}</button>
					<button class="btn btn-xs btn-danger bulk-act"  data-action="Scrapped">${__("Mark Scrapped")}</button>
					<button class="btn btn-xs btn-danger bulk-act"  data-action="Lost">${__("Mark Lost")}</button>
					<button class="btn btn-xs btn-default bulk-clear">${__("Clear Selection")}</button>
				</div>

				<div class="imei-table-wrap">
					<div class="imei-table-status text-muted small"></div>
					<div class="imei-table"></div>
				</div>
			</div>
		`);

		// Inject scoped styles (kept inline so the hub works even without a bundle build)
		if (!$("#imei-tracker-style").length) {
			$("head").append(`
				<style id="imei-tracker-style">
				.imei-tracker { padding: 8px 4px; }
				.imei-tracker-controls { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 12px; }
				.imei-search-wrap { flex: 1; min-width: 240px; }
				.imei-kpi-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 14px; }
				.imei-kpi-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 12px; cursor: pointer; transition: all .15s; border-left-width: 4px; }
				.imei-kpi-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,.08); transform: translateY(-1px); }
				.imei-kpi-card .lbl { font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: .5px; }
				.imei-kpi-card .val { font-size: 22px; font-weight: 600; color: #0f172a; }
				.imei-bucket-tabs { display:flex; flex-wrap:wrap; gap:6px; margin-bottom: 10px; }
				.imei-bucket-tab { background:#f1f5f9; border:1px solid #cbd5e1; border-radius:14px; padding:4px 12px; font-size:12px; cursor:pointer; }
				.imei-bucket-tab.active { background:#1e3a8a; color:#fff; border-color:#1e3a8a; }
				.imei-bulk-bar { background:#fef3c7; border:1px solid #fbbf24; border-radius:6px; padding:6px 10px; margin-bottom:10px; display:flex; gap:8px; align-items:center; }
				.imei-bulk-bar .bulk-count { font-weight:600; color:#78350f; }
				.imei-table { background:#fff; border:1px solid #e2e8f0; border-radius:8px; overflow:auto; max-height: 65vh; }
				.imei-table table { margin: 0; font-size: 12px; }
				.imei-table th { background: #f8fafc; position: sticky; top: 0; z-index: 1; font-weight: 600; }
				.imei-table td, .imei-table th { padding: 6px 8px; vertical-align: middle; white-space: nowrap; }
				.imei-table tr.selected { background: #fef9c3; }
				.imei-table tr:hover { background: #f8fafc; cursor: pointer; }
				.badge-imei { background:#0891b2; color:#fff; font-size:10px; padding:2px 6px; border-radius:8px; }
				.badge-barcode { background:#94a3b8; color:#fff; font-size:10px; padding:2px 6px; border-radius:8px; }
				.badge-aging { background:#fee2e2; color:#991b1b; font-size:10px; padding:2px 6px; border-radius:8px; margin-left:4px; }
				.imei-status { display:inline-block; padding:2px 8px; border-radius:8px; font-size:11px; font-weight:500; }
				.imei-status.in-stock,.imei-status.received,.imei-status.displayed,.imei-status.refurbished { background:#d1fae5; color:#065f46; }
				.imei-status.sold,.imei-status.delivered { background:#dbeafe; color:#1e40af; }
				.imei-status.returned,.imei-status.customer-return { background:#fee2e2; color:#991b1b; }
				.imei-status.in-service,.imei-status.repaired { background:#fef3c7; color:#92400e; }
				.imei-status.buyback { background:#ede9fe; color:#5b21b6; }
				.imei-status.scrapped,.imei-status.lost { background:#e2e8f0; color:#475569; }
				.imei-history-modal .timeline-entry { border-left:3px solid #cbd5e1; padding:6px 10px; margin-bottom:6px; font-size:12px; background:#f8fafc; border-radius:0 6px 6px 0; }
				.imei-history-modal .timeline-entry.stock { border-left-color:#3b82f6; }
				.imei-history-modal .timeline-entry.sale,.imei-history-modal .timeline-entry.pos { border-left-color:#10b981; }
				.imei-history-modal .timeline-entry.lifecycle { border-left-color:#8b5cf6; }
				.imei-history-modal .timeline-entry .ts { color:#64748b; font-size:10px; }
				</style>
			`);
		}

		// Mode toggle
		$body.find(".imei-mode-group .btn").on("click", (e) => {
			const $b = $(e.currentTarget);
			$body.find(".imei-mode-group .btn").removeClass("active");
			$b.addClass("active");
			this.imei_mode = $b.data("mode");
			this.refresh();
		});

		// Aging
		$body.find(".imei-aging-group .btn").on("click", (e) => {
			const v = $(e.currentTarget).data("aging");
			this.aging_filter = v || null;
			this.refresh();
		});

		// Search (debounced)
		let timer;
		$body.find(".imei-search").on("input", (e) => {
			clearTimeout(timer);
			const v = e.target.value;
			timer = setTimeout(() => { this.search_term = v; this.refresh(); }, 300);
		});

		// Bulk actions
		$body.on("click", ".imei-bulk-bar .bulk-act", (e) => {
			this.run_bulk($(e.currentTarget).data("action"));
		});
		$body.on("click", ".imei-bulk-bar .bulk-clear", () => {
			this.selected_serials.clear();
			this.render_table();
		});
	}

	setup_filters() {
		this.filters = ch_erp15.hub_filters.attach(this.page, {
			include_dates: true,
			on_change: () => this.refresh(),
		});
	}

	setup_actions() {
		this.page.set_primary_action(__("Refresh"), () => this.refresh(), "refresh");
		this.page.add_menu_item(__("Export CSV"), () => this.export_csv());
		this.page.add_menu_item(__("Backfill IMEI Flag"), () => this.run_backfill());
		this.page.add_menu_item(__("Open Lifecycle List"), () => {
			frappe.set_route("List", "CH Serial Lifecycle");
		});
	}

	// ── Data ──────────────────────────────────────────────────────────────────
	build_filter_payload() {
		const f = this.filters.values();
		f.imei_only     = this.imei_mode === "imei"     ? 1 : 0;
		f.non_imei_only = this.imei_mode === "non_imei" ? 1 : 0;
		f.status_bucket = this.active_bucket || "";
		f.aging_bucket  = this.aging_filter  || "";
		f.search        = this.search_term   || "";
		return f;
	}

	refresh() {
		const filters = this.build_filter_payload();
		$(this.page.body).find(".imei-table-status").text(__("Loading…"));
		frappe.xcall(`${this.api_base}.get_imei_tracker_data`, { filters })
			.then((res) => {
				this.data = res || { kpis: [], buckets: [], rows: [] };
				this.render_kpis();
				this.render_buckets();
				this.render_table();
			})
			.catch((err) => {
				console.error(err);
				$(this.page.body).find(".imei-table-status").text(__("Error loading data"));
			});
	}

	// ── Render: KPI cards ────────────────────────────────────────────────────
	render_kpis() {
		const $row = $(this.page.body).find(".imei-kpi-row").empty();
		(this.data.kpis || []).forEach((k) => {
			const $card = $(`
				<div class="imei-kpi-card" style="border-left-color:${k.color}">
					<div class="lbl">${frappe.utils.escape_html(k.label)}</div>
					<div class="val">${frappe.format(k.value, { fieldtype: "Int" })}</div>
				</div>
			`);
			$card.on("click", () => this.kpi_clicked(k));
			$row.append($card);
		});
	}

	kpi_clicked(k) {
		// Map KPI key → mode/bucket selection
		const mode_map = { real_imei: "imei", non_imei: "non_imei" };
		const bucket_map = {
			in_stock: "in_stock", in_service: "in_service",
			returned: "returned", bought_back: "bought_back",
			out_of_pool: "out_of_pool", sold_mtd: "sold",
		};
		if (mode_map[k.key]) {
			this.imei_mode = mode_map[k.key];
			$(this.page.body).find(".imei-mode-group .btn").removeClass("active")
				.filter(`[data-mode="${this.imei_mode}"]`).addClass("active");
		}
		if (bucket_map[k.key]) this.active_bucket = bucket_map[k.key];
		if (k.key === "total") { this.imei_mode = "all"; this.active_bucket = null; }
		this.refresh();
	}

	// ── Render: status bucket tabs ──────────────────────────────────────────
	render_buckets() {
		const $row = $(this.page.body).find(".imei-bucket-tabs").empty();
		const all_btn = $(`<div class="imei-bucket-tab ${!this.active_bucket ? "active" : ""}">${__("All Statuses")}</div>`);
		all_btn.on("click", () => { this.active_bucket = null; this.refresh(); });
		$row.append(all_btn);
		(this.data.buckets || []).forEach((b) => {
			const cls = this.active_bucket === b.key ? "active" : "";
			const $tab = $(`<div class="imei-bucket-tab ${cls}">${frappe.utils.escape_html(b.label)} (${b.count})</div>`);
			$tab.on("click", () => {
				this.active_bucket = (this.active_bucket === b.key) ? null : b.key;
				this.refresh();
			});
			$row.append($tab);
		});
	}

	// ── Render: table ─────────────────────────────────────────────────────────
	render_table() {
		const rows = this.data.rows || [];
		const $status = $(this.page.body).find(".imei-table-status");
		$status.text(__("Showing {0} of {1} records", [rows.length, this.data.total_rows || 0]));

		const $bulk = $(this.page.body).find(".imei-bulk-bar");
		if (this.selected_serials.size) {
			$bulk.show().find(".bulk-count").text(__("{0} selected", [this.selected_serials.size]));
		} else {
			$bulk.hide();
		}

		const $tbl = $(this.page.body).find(".imei-table").empty();
		if (!rows.length) {
			$tbl.html(`<div class="text-center text-muted" style="padding:40px">${__("No serials match the current filters.")}</div>`);
			return;
		}

		const headers = [
			{ k: "_chk",   l: "" },
			{ k: "_type",  l: __("Type") },
			{ k: "key",    l: __("IMEI / Serial") },
			{ k: "item",   l: __("Item") },
			{ k: "brand",  l: __("Brand") },
			{ k: "status", l: __("Status") },
			{ k: "wh",     l: __("Warehouse") },
			{ k: "city",   l: __("City") },
			{ k: "zone",   l: __("Zone") },
			{ k: "age",    l: __("Age (d)") },
			{ k: "rate",   l: __("Purchase ₹") },
			{ k: "warr",   l: __("Warranty") },
			{ k: "upd",    l: __("Last Updated") },
		];

		let html = '<table class="table table-bordered table-condensed"><thead><tr>';
		for (const h of headers) html += `<th>${frappe.utils.escape_html(h.l)}</th>`;
		html += "</tr></thead><tbody>";

		for (const r of rows) {
			const key = r.imei_number || r.serial_no;
			const sel = this.selected_serials.has(r.serial_no);
			const aging_html = (r.aging_flags || [])
				.map(t => `<span class="badge-aging">${t === "unsold_90" ? __("Stale") : __("Long Service")}</span>`)
				.join("");
			const status_class = (r.lifecycle_status || "").toLowerCase().replace(/\s+/g, "-");
			html += `
				<tr data-serial="${frappe.utils.escape_html(r.serial_no)}" class="${sel ? "selected" : ""}">
					<td><input type="checkbox" class="row-chk" ${sel ? "checked" : ""}/></td>
					<td>${r.is_imei ? '<span class="badge-imei">IMEI</span>' : '<span class="badge-barcode">Barcode</span>'}</td>
					<td><b>${frappe.utils.escape_html(key || "")}</b>${aging_html}</td>
					<td>${frappe.utils.escape_html(r.item_name || r.item_code || "")}</td>
					<td>${frappe.utils.escape_html(r.brand || "")}</td>
					<td><span class="imei-status ${status_class}">${frappe.utils.escape_html(r.lifecycle_status || "")}</span>${r.sub_status ? ` <small>${frappe.utils.escape_html(r.sub_status)}</small>` : ""}</td>
					<td>${frappe.utils.escape_html(r.warehouse_name || r.current_warehouse || "")}</td>
					<td>${frappe.utils.escape_html(r.city || "")}</td>
					<td>${frappe.utils.escape_html(r.zone || "")}</td>
					<td class="text-right">${r.age_days != null ? r.age_days : ""}</td>
					<td class="text-right">${r.purchase_rate ? format_currency(r.purchase_rate) : ""}</td>
					<td>${frappe.utils.escape_html(r.warranty_status || "")}</td>
					<td>${r.last_updated ? frappe.datetime.str_to_user(r.last_updated) : ""}</td>
				</tr>
			`;
		}
		html += "</tbody></table>";
		$tbl.html(html);

		// Row click → drill-through (but ignore checkbox clicks)
		$tbl.find("tbody tr").on("click", (e) => {
			if ($(e.target).is("input.row-chk")) return;
			const sn = $(e.currentTarget).data("serial");
			this.show_history(sn);
		});

		$tbl.find(".row-chk").on("change", (e) => {
			e.stopPropagation();
			const sn = $(e.currentTarget).closest("tr").data("serial");
			if (e.currentTarget.checked) this.selected_serials.add(sn);
			else this.selected_serials.delete(sn);
			$(e.currentTarget).closest("tr").toggleClass("selected", e.currentTarget.checked);
			const $bulk = $(this.page.body).find(".imei-bulk-bar");
			if (this.selected_serials.size) {
				$bulk.show().find(".bulk-count").text(__("{0} selected", [this.selected_serials.size]));
			} else {
				$bulk.hide();
			}
		});
	}

	// ── Drill-through history dialog ──────────────────────────────────────────
	show_history(serial_no) {
		const d = new frappe.ui.Dialog({
			title: __("Lifecycle History — {0}", [serial_no]),
			size: "extra-large",
			fields: [{ fieldtype: "HTML", fieldname: "body" }],
		});
		d.$wrapper.addClass("imei-history-modal");
		d.show();
		d.fields_dict.body.$wrapper.html(`<div class="text-muted">${__("Loading…")}</div>`);

		frappe.xcall(`${this.api_base}.get_imei_history`, { serial_no })
			.then((res) => this.render_history(d, res))
			.catch(() => d.fields_dict.body.$wrapper.html(
				`<div class="text-danger">${__("Failed to load history.")}</div>`
			));
	}

	render_history(dialog, res) {
		if (res.error) {
			dialog.fields_dict.body.$wrapper.html(`<div class="text-danger">${frappe.utils.escape_html(res.error)}</div>`);
			return;
		}
		const lc = res.lifecycle || {};
		const sn = res.serial_no || {};
		const tl = res.timeline || [];

		const head = `
			<div class="row" style="margin-bottom:12px">
				<div class="col-md-6">
					<h5>${__("Device")}</h5>
					<table class="table table-condensed table-bordered">
						<tr><th>${__("Serial")}</th><td><b>${frappe.utils.escape_html(res.key)}</b></td></tr>
						<tr><th>${__("IMEI 1")}</th><td>${frappe.utils.escape_html(lc.imei_number || "")}</td></tr>
						<tr><th>${__("IMEI 2")}</th><td>${frappe.utils.escape_html(lc.imei_number_2 || "")}</td></tr>
						<tr><th>${__("Item")}</th><td>${frappe.utils.escape_html(lc.item_name || lc.item_code || "")}</td></tr>
						<tr><th>${__("Type")}</th><td>${sn.ch_is_imei ? '<span class="badge-imei">IMEI</span>' : '<span class="badge-barcode">Barcode</span>'}</td></tr>
					</table>
				</div>
				<div class="col-md-6">
					<h5>${__("Status")}</h5>
					<table class="table table-condensed table-bordered">
						<tr><th>${__("Lifecycle")}</th><td>${frappe.utils.escape_html(lc.lifecycle_status || "")}</td></tr>
						<tr><th>${__("Sub-Status")}</th><td>${frappe.utils.escape_html(lc.sub_status || "")}</td></tr>
						<tr><th>${__("Condition")}</th><td>${frappe.utils.escape_html(lc.stock_condition || "")}</td></tr>
						<tr><th>${__("Warehouse")}</th><td>${frappe.utils.escape_html(lc.current_warehouse || "")}</td></tr>
						<tr><th>${__("Warranty")}</th><td>${frappe.utils.escape_html(lc.warranty_status || "")}</td></tr>
					</table>
				</div>
			</div>
			<h5>${__("Unified Timeline")} <small class="text-muted">(${tl.length})</small></h5>
		`;

		let body = head + '<div class="timeline-list">';
		if (!tl.length) {
			body += `<div class="text-muted">${__("No events.")}</div>`;
		} else {
			for (const e of tl) {
				const link = e.doc_type && e.doc_name
					? ` <a href="/app/${frappe.router.slug(e.doc_type)}/${encodeURIComponent(e.doc_name)}" target="_blank">${__("Open")}</a>`
					: "";
				body += `
					<div class="timeline-entry ${e.kind}">
						<div class="ts">${frappe.utils.escape_html(e.ts || "")} · ${e.kind}</div>
						<div>${frappe.utils.escape_html(e.summary || "")}${link}</div>
					</div>
				`;
			}
		}
		body += "</div>";

		// Action: open lifecycle doc directly
		body += `
			<div style="margin-top:12px">
				<button class="btn btn-default btn-sm" id="imei-open-lc">${__("Open Lifecycle Doc")}</button>
				<button class="btn btn-default btn-sm" id="imei-open-sn">${__("Open Serial No Doc")}</button>
			</div>
		`;
		dialog.fields_dict.body.$wrapper.html(body);
		dialog.fields_dict.body.$wrapper.find("#imei-open-lc").on("click", () => {
			frappe.set_route("Form", "CH Serial Lifecycle", res.key);
			dialog.hide();
		});
		dialog.fields_dict.body.$wrapper.find("#imei-open-sn").on("click", () => {
			frappe.set_route("Form", "Serial No", res.key);
			dialog.hide();
		});
	}

	// ── Bulk actions ──────────────────────────────────────────────────────────
	run_bulk(action) {
		if (!this.selected_serials.size) return;
		const list = Array.from(this.selected_serials);
		frappe.prompt([{
			fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks"),
			reqd: 1,
		}], (vals) => {
			frappe.confirm(
				__("Mark {0} serial(s) as <b>{1}</b>?", [list.length, action]),
				() => {
					frappe.xcall(`${this.api_base}.bulk_update_status`, {
						serial_nos: list, new_status: action, remarks: vals.remarks,
					}).then((res) => {
						frappe.show_alert({
							message: __("Updated {0}, skipped {1}", [res.updated, (res.skipped || []).length]),
							indicator: "green",
						});
						this.selected_serials.clear();
						this.refresh();
					});
				}
			);
		}, __("Bulk Update Status"), __("Apply"));
	}

	// ── Export ───────────────────────────────────────────────────────────────
	export_csv() {
		frappe.show_alert(__("Preparing export…"));
		frappe.xcall(`${this.api_base}.export_imei_data`, { filters: this.build_filter_payload() })
			.then((res) => {
				const blob = b64_to_blob(res.content_b64, res.mime);
				const url = URL.createObjectURL(blob);
				const a = document.createElement("a");
				a.href = url;
				a.download = res.filename;
				document.body.appendChild(a);
				a.click();
				document.body.removeChild(a);
				URL.revokeObjectURL(url);
				frappe.show_alert({
					message: __("Exported {0} rows", [res.row_count]),
					indicator: "green",
				});
			});
	}

	// ── Maintenance: backfill ch_is_imei ─────────────────────────────────────
	run_backfill() {
		frappe.confirm(
			__("Recompute the IMEI flag for ALL existing Serial No records? This is safe to re-run."),
			() => {
				frappe.xcall(`${this.api_base}.backfill_is_imei_flag`).then((res) => {
					frappe.msgprint({
						title: __("Backfill Complete"),
						indicator: "green",
						message: `<pre>${frappe.utils.escape_html(JSON.stringify(res.counts, null, 2))}</pre>`,
					});
					this.refresh();
				});
			}
		);
	}
};

// Helper: base64 → Blob
function b64_to_blob(b64, mime) {
	const bin = atob(b64);
	const len = bin.length;
	const buf = new Uint8Array(len);
	for (let i = 0; i < len; i++) buf[i] = bin.charCodeAt(i);
	return new Blob([buf], { type: mime || "application/octet-stream" });
}
