/* Campaign Hub — Coupon & Voucher Campaign Management Dashboard
   Follows the standard Hub pattern (Stock Hub / Purchase Hub) */

frappe.pages["campaign-hub"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Campaign Hub"),
		single_column: true,
	});
	wrapper.campaign_hub = new CampaignHub(page);
};

frappe.pages["campaign-hub"].refresh = function (wrapper) {
	wrapper.campaign_hub && wrapper.campaign_hub.refresh();
};

class CampaignHub {
	constructor(page) {
		this.page = page;
		this._timer = null;
		this._setup_controls();
		this._setup_container();
		this.refresh();
		this._start_auto_refresh();
	}

	/* ── Controls ─────────────────────────────────────────────── */

	_setup_controls() {
		this.company_field = this.page.add_field({
			fieldname: "company", label: __("Company"),
			fieldtype: "Link", options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			change: () => this.refresh(),
		});
		this.page.add_button(__("Refresh"), () => this.refresh(), { icon: "refresh" });
	}

	_setup_container() {
		this.$root = $(`<div class="hub-root campaign-hub-root"></div>`).appendTo(this.page.body);
	}

	/* ── Data ─────────────────────────────────────────────────── */

	refresh() {
		const company = this.company_field?.get_value() || "";
		this.$root.html(`<div class="hub-loading"><i class="fa fa-spinner fa-spin"></i> ${__("Loading Campaign Hub…")}</div>`);
		frappe.xcall(
			"ch_item_master.ch_item_master.coupon_campaign_api.get_campaign_hub_data",
			{ company }
		).then((data) => this._render(data))
		 .catch(() => {
			this.$root.html(`<div class="hub-loading text-danger">${__("Failed to load data.")}</div>`);
		 });
	}

	_start_auto_refresh() {
		this._timer = setInterval(() => this.refresh(), 60000);
		$(this.page.parent).on("remove", () => clearInterval(this._timer));
	}

	/* ── Render ───────────────────────────────────────────────── */

	_render(data) {
		this.$root.empty();
		this._render_header();
		this._render_pipeline(data.pipeline || []);
		this._render_kpis(data.kpis || {});
		this._render_actions();
		this._render_intelligence(data.insights || [], data.kpis || {});
		this._render_tables(data);
	}

	/* ── Header ───────────────────────────────────────────────── */

	_render_header() {
		this.$root.append(`
			<div class="hub-header">
				<div>
					<div class="hub-title"><i class="fa fa-bullhorn"></i> ${__("Campaign Hub")}</div>
					<div class="hub-subtitle">${__("Create, distribute & track coupon and voucher campaigns — measure ROI in real time")}</div>
				</div>
				<div class="hub-auto-badge">
					<span class="pulse-dot"></span> ${__("Live · Auto-refreshes every 60s")}
				</div>
			</div>
		`);
	}

	/* ── Pipeline (Flow Nodes) ────────────────────────────────── */

	_render_pipeline(steps) {
		const arrow = `<div class="hub-flow-connector">
			<svg width="32" height="24" viewBox="0 0 32 24" fill="none" stroke="currentColor"
				stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
				<path d="M4 12H24M18 6l6 6-6 6"/>
			</svg>
		</div>`;

		const meta = {
			Draft:     { icon: "pencil",       color: "#9ca3af" },
			Active:    { icon: "rocket",       color: "#10b981" },
			Paused:    { icon: "pause-circle", color: "#f59e0b" },
			Completed: { icon: "check-circle", color: "#3b82f6" },
			Expired:   { icon: "clock-o",      color: "#6b7280" },
			Cancelled: { icon: "times-circle", color: "#ef4444" },
		};

		// ensure all statuses appear even if count is 0
		const order = ["Draft", "Active", "Paused", "Completed", "Expired", "Cancelled"];
		const map = {};
		steps.forEach(s => { map[s.status] = s.count; });
		const full = order.map(s => ({ status: s, count: map[s] || 0 }));

		const nodes = full.map((s, i) => {
			const m = meta[s.status] || { icon: "circle", color: "#9ca3af" };
			const node = `
				<div class="hub-flow-node" data-step="${s.status}">
					<div class="hub-flow-badge" style="background:${m.color}">${s.count}</div>
					<div class="hub-flow-meta">
						<i class="fa fa-${m.icon}"></i>
						<span class="hub-flow-name">${__(s.status)}</span>
					</div>
				</div>`;
			return i < full.length - 1 ? node + arrow : node;
		}).join("");

		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-random"></i> ${__("Campaign Lifecycle")}</h5>
				<div class="hub-flow-wrap"><div class="hub-flow">${nodes}</div></div>
			</div>
		`);

		this.$root.find(".hub-flow-node").on("click", (e) => {
			const step = $(e.currentTarget).data("step");
			frappe.set_route("List", "CH Coupon Campaign", { status: step });
		});
	}

	/* ── KPIs ─────────────────────────────────────────────────── */

	_render_kpis(k) {
		const cards = [
			{ key: "active",   label: __("Active Campaigns"),    value: k.active_campaigns || 0,               color: "#8b5cf6" },
			{ key: "codes",    label: __("Codes in Circulation"), value: (k.total_codes_in_circulation || 0).toLocaleString(), color: "#0891b2" },
			{ key: "redeemed", label: __("Redeemed"),             value: (k.total_redeemed || 0).toLocaleString(),             color: "#059669" },
			{ key: "rate",     label: __("Redemption Rate"),      value: `${k.overall_redemption_rate || 0}%`,                 color: "#f59e0b" },
			{ key: "discount", label: __("Discount Given"),       value: format_currency(k.total_discount_given || 0),         color: "#ef4444" },
			{ key: "revenue",  label: __("Revenue Impact"),       value: format_currency(k.total_revenue || 0),                color: "#059669" },
		];

		const html = cards.map(c =>
			`<div class="hub-kpi-card" style="--kpi-color:${c.color}" data-kpi="${c.key}">
				<div class="hub-kpi-value">${c.value}</div>
				<div class="hub-kpi-label">${c.label}</div>
			</div>`
		).join("");

		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-tachometer"></i> ${__("Key Metrics")}</h5>
				<div class="hub-kpi-grid">${html}</div>
			</div>
		`);

		this.$root.find(".hub-kpi-card").on("click", (e) => {
			const key = $(e.currentTarget).data("kpi");
			const actions = {
				active:   () => frappe.set_route("List", "CH Coupon Campaign", { status: "Active" }),
				codes:    () => this._activate_tab("active"),
				redeemed: () => this._activate_tab("redemptions"),
				rate:     () => this._activate_tab("top"),
				discount: () => this._activate_tab("top"),
				revenue:  () => this._activate_tab("top"),
			};
			const fn = actions[key];
			if (fn) fn();
		});
	}

	/* ── Quick Actions ────────────────────────────────────────── */

	_render_actions() {
		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-bolt"></i> ${__("Quick Actions")}</h5>
				<div class="hub-actions-grid">
					<button class="hub-action-btn" data-act="new_campaign">
						<i class="fa fa-plus"></i> ${__("New Campaign")}
					</button>
					<button class="hub-action-btn" data-act="all_campaigns">
						<i class="fa fa-list"></i> ${__("All Campaigns")}
					</button>
					<button class="hub-action-btn" data-act="coupon_codes">
						<i class="fa fa-ticket"></i> ${__("Coupon Codes")}
					</button>
					<button class="hub-action-btn" data-act="vouchers">
						<i class="fa fa-credit-card"></i> ${__("CH Vouchers")}
					</button>
					<button class="hub-action-btn" data-act="pricing_rules">
						<i class="fa fa-percent"></i> ${__("Pricing Rules")}
					</button>
					<button class="hub-action-btn" data-act="lookup">
						<i class="fa fa-search"></i> ${__("Code Lookup")}
					</button>
				</div>
			</div>
		`);

		this.$root.on("click", ".hub-action-btn", function () {
			const actions = {
				new_campaign:  () => frappe.new_doc("CH Coupon Campaign"),
				all_campaigns: () => frappe.set_route("List", "CH Coupon Campaign"),
				coupon_codes:  () => frappe.set_route("List", "Coupon Code"),
				vouchers:      () => frappe.set_route("List", "CH Voucher"),
				pricing_rules: () => frappe.set_route("List", "Pricing Rule", { coupon_code_based: 1 }),
				lookup:        () => {
					const $btn = $(".hub-tab[data-tab='lookup']").first();
					if ($btn.length) $btn.trigger("click");
				},
			};
			const fn = actions[$(this).data("act")];
			if (fn) fn();
		});
	}

	/* ── AI Insights + Mini KPIs ──────────────────────────────── */

	_render_intelligence(insights, kpis) {
		const insightCards = insights && insights.length
			? insights.map((i) => {
				const cls = {
					alert: "hub-insight-high",
					warning: "hub-insight-medium",
					success: "hub-insight-low",
					info: "hub-insight-low",
				};
				const badgeCls = {
					alert: "hub-badge-red",
					warning: "hub-badge-orange",
					success: "hub-badge-green",
					info: "hub-badge-blue",
				};
				return `<div class="hub-insight-card ${cls[i.type] || "hub-insight-low"}">
					<div class="hub-insight-top">
						<span class="hub-badge ${badgeCls[i.type] || "hub-badge-blue"}">${i.icon || "💡"} ${i.type || "Info"}</span>
					</div>
					<div class="hub-insight-detail">${frappe.utils.escape_html(i.text || "")}</div>
				</div>`;
			}).join("")
			: `<div class="hub-empty"><i class="fa fa-check-circle"></i>${__("No critical insights right now")}</div>`;

		// mini KPIs for campaign economics
		const controls = [
			{ label: __("Active Campaigns"), value: kpis.active_campaigns || 0, color: "#8b5cf6" },
			{ label: __("Total Codes"),      value: kpis.total_codes_in_circulation || 0, color: "#0891b2" },
			{ label: __("Total Redeemed"),    value: kpis.total_redeemed || 0, color: "#059669" },
			{ label: __("Redemption Rate"),   value: `${kpis.overall_redemption_rate || 0}%`, color: "#f59e0b" },
		];

		const controlCards = controls.map((c) => {
			return `<div class="hub-mini-kpi" style="--mini-color:${c.color}">
				<div class="hub-mini-kpi-value">${c.value}</div>
				<div class="hub-mini-kpi-label">${c.label}</div>
			</div>`;
		}).join("");

		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-lightbulb-o"></i> ${__("Campaign Insights")}</h5>
				<div class="hub-intel-grid">
					<div class="hub-intel-panel">${insightCards}</div>
					<div class="hub-intel-panel"><div class="hub-mini-kpi-grid">${controlCards}</div></div>
				</div>
			</div>
		`);
	}

	/* ── Detail Tables with Tabs ──────────────────────────────── */

	_render_tables(data) {
		const active_count = (data.active_campaigns || []).length;
		const top_count    = (data.top_campaigns || []).length;
		const expiry_count = (data.expiring_soon || []).length;
		const redeem_count = (data.recent_redemptions || []).length;

		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-list"></i> ${__("Detail View")}</h5>
				<div class="hub-tabs">
					<button class="hub-tab active" data-tab="active">
						${__("Active Campaigns")} <span class="badge">${active_count}</span>
					</button>
					<button class="hub-tab" data-tab="top">
						${__("Top Performers")} <span class="badge">${top_count}</span>
					</button>
					<button class="hub-tab" data-tab="expiring">
						${__("Expiring Soon")} <span class="badge">${expiry_count}</span>
					</button>
					<button class="hub-tab" data-tab="redemptions">
						${__("Recent Redemptions")} <span class="badge">${redeem_count}</span>
					</button>
					<button class="hub-tab" data-tab="lookup">
						${__("Code Lookup")}
					</button>
				</div>
				<div class="hub-tab-panel active" data-panel="active"></div>
				<div class="hub-tab-panel" data-panel="top"></div>
				<div class="hub-tab-panel" data-panel="expiring"></div>
				<div class="hub-tab-panel" data-panel="redemptions"></div>
				<div class="hub-tab-panel" data-panel="lookup"></div>
			</div>
		`);

		this._bind_tabs();
		this._render_active(data.active_campaigns || []);
		this._render_top(data.top_campaigns || []);
		this._render_expiring(data.expiring_soon || []);
		this._render_redemptions(data.recent_redemptions || []);
		this._render_lookup();
	}

	_bind_tabs() {
		this.$root.off("click", ".hub-tab");
		this.$root.on("click", ".hub-tab", function () {
			const tab = $(this).data("tab");
			$(this).siblings().removeClass("active");
			$(this).addClass("active");
			$(this).closest(".hub-section").find(".hub-tab-panel").removeClass("active");
			$(this).closest(".hub-section").find(`[data-panel="${tab}"]`).addClass("active");
		});
	}

	_activate_tab(tab) {
		const $btn = this.$root.find(`.hub-tab[data-tab="${tab}"]`).first();
		if ($btn.length) $btn.trigger("click");
	}

	/* ── Active Campaigns ─────────────────────────────────────── */

	_render_active(items) {
		const $panel = this.$root.find('[data-panel="active"]');
		if (!items.length) {
			$panel.html(`<div class="hub-empty"><i class="fa fa-bullhorn"></i>${__("No active campaigns. Create one to get started.")}</div>`);
			return;
		}

		const rows = items.map((r) => {
			const rate = parseFloat(r.redemption_rate) || 0;
			const barColor = rate >= 30 ? "#22c55e" : rate >= 10 ? "#f59e0b" : "#ef4444";
			const typeCls = {
				"Coupon Code": "hub-badge-blue",
				"Voucher": "hub-badge-purple",
				"Both": "hub-badge-teal",
			};
			return `<tr data-name="${r.name}">
				<td><a href="/app/ch-coupon-campaign/${r.name}">${frappe.utils.escape_html(r.campaign_name || "")}</a></td>
				<td><span class="hub-badge ${typeCls[r.campaign_type] || "hub-badge-grey"}">${r.campaign_type}</span></td>
				<td>${r.valid_from ? frappe.datetime.str_to_user(r.valid_from) : "–"}</td>
				<td>${r.valid_upto ? frappe.datetime.str_to_user(r.valid_upto) : "–"}</td>
				<td class="text-right">${(r.total_codes_generated || 0).toLocaleString()}</td>
				<td class="text-right">${(r.total_redeemed || 0).toLocaleString()}</td>
				<td style="min-width:100px">
					<div class="hub-progress"><div class="hub-progress-bar" style="width:${rate}%;background:${barColor}"></div></div>
					<span style="font-size:11px;color:var(--text-muted)">${rate.toFixed(1)}%</span>
				</td>
				<td class="text-right">${frappe.format(r.total_revenue_generated || 0, { fieldtype: "Currency" })}</td>
			</tr>`;
		}).join("");

		$panel.html(`<div class="hub-table-wrap"><table class="hub-table">
			<thead><tr>
				<th>${__("Campaign")}</th><th>${__("Type")}</th>
				<th>${__("From")}</th><th>${__("To")}</th>
				<th class="text-right">${__("Codes")}</th><th class="text-right">${__("Redeemed")}</th>
				<th>${__("Rate")}</th><th class="text-right">${__("Revenue")}</th>
			</tr></thead>
			<tbody>${rows}</tbody>
		</table></div>`);

		$panel.find("tbody tr").on("click", function (e) {
			if (e.target.tagName === "A") return;
			frappe.set_route("Form", "CH Coupon Campaign", $(this).data("name"));
		});
	}

	/* ── Top Performers ───────────────────────────────────────── */

	_render_top(items) {
		const $panel = this.$root.find('[data-panel="top"]');
		if (!items.length) {
			$panel.html(`<div class="hub-empty"><i class="fa fa-trophy"></i>${__("No redeemed campaigns yet")}</div>`);
			return;
		}

		const rows = items.map((r) => {
			const rate = parseFloat(r.redemption_rate) || 0;
			const barColor = rate >= 30 ? "#22c55e" : rate >= 10 ? "#f59e0b" : "#ef4444";
			return `<tr data-name="${r.name}">
				<td><a href="/app/ch-coupon-campaign/${r.name}">${frappe.utils.escape_html(r.campaign_name || "")}</a></td>
				<td><span class="hub-badge hub-badge-grey">${r.campaign_type || "–"}</span></td>
				<td class="text-right">${(r.total_codes_generated || 0).toLocaleString()}</td>
				<td class="text-right">${(r.total_redeemed || 0).toLocaleString()}</td>
				<td style="min-width:100px">
					<div class="hub-progress"><div class="hub-progress-bar" style="width:${rate}%;background:${barColor}"></div></div>
					<span style="font-size:11px;color:var(--text-muted)">${rate.toFixed(1)}%</span>
				</td>
				<td class="text-right">${frappe.format(r.total_discount_given || 0, { fieldtype: "Currency" })}</td>
				<td class="text-right">${frappe.format(r.total_revenue_generated || 0, { fieldtype: "Currency" })}</td>
			</tr>`;
		}).join("");

		$panel.html(`<div class="hub-table-wrap"><table class="hub-table">
			<thead><tr>
				<th>${__("Campaign")}</th><th>${__("Type")}</th>
				<th class="text-right">${__("Codes")}</th><th class="text-right">${__("Redeemed")}</th>
				<th>${__("Rate")}</th><th class="text-right">${__("Discount")}</th>
				<th class="text-right">${__("Revenue")}</th>
			</tr></thead>
			<tbody>${rows}</tbody>
		</table></div>`);

		$panel.find("tbody tr").on("click", function (e) {
			if (e.target.tagName === "A") return;
			frappe.set_route("Form", "CH Coupon Campaign", $(this).data("name"));
		});
	}

	/* ── Expiring Soon ────────────────────────────────────────── */

	_render_expiring(items) {
		const $panel = this.$root.find('[data-panel="expiring"]');
		if (!items.length) {
			$panel.html(`<div class="hub-empty"><i class="fa fa-check-circle"></i>${__("No campaigns expiring in the next 7 days")}</div>`);
			return;
		}

		const rows = items.map((r) => {
			const days = frappe.datetime.get_day_diff(r.valid_upto, frappe.datetime.nowdate());
			const urgency = days <= 2 ? "hub-badge-red" : days <= 5 ? "hub-badge-orange" : "hub-badge-yellow";
			return `<tr data-name="${r.name}">
				<td><a href="/app/ch-coupon-campaign/${r.name}">${frappe.utils.escape_html(r.campaign_name || "")}</a></td>
				<td><span class="hub-badge hub-badge-grey">${r.campaign_type || "–"}</span></td>
				<td>${r.valid_upto ? frappe.datetime.str_to_user(r.valid_upto) : "–"}</td>
				<td><span class="hub-badge ${urgency}">${days}d left</span></td>
				<td class="text-right">${(r.total_codes_generated || 0).toLocaleString()}</td>
				<td class="text-right">${(r.total_redeemed || 0).toLocaleString()}</td>
				<td class="text-right">${((parseFloat(r.redemption_rate) || 0)).toFixed(1)}%</td>
			</tr>`;
		}).join("");

		$panel.html(`<div class="hub-table-wrap"><table class="hub-table">
			<thead><tr>
				<th>${__("Campaign")}</th><th>${__("Type")}</th>
				<th>${__("Expires")}</th><th>${__("Urgency")}</th>
				<th class="text-right">${__("Codes")}</th><th class="text-right">${__("Redeemed")}</th>
				<th class="text-right">${__("Rate %")}</th>
			</tr></thead>
			<tbody>${rows}</tbody>
		</table></div>`);

		$panel.find("tbody tr").on("click", function (e) {
			if (e.target.tagName === "A") return;
			frappe.set_route("Form", "CH Coupon Campaign", $(this).data("name"));
		});
	}

	/* ── Recent Redemptions ───────────────────────────────────── */

	_render_redemptions(items) {
		const $panel = this.$root.find('[data-panel="redemptions"]');
		if (!items.length) {
			$panel.html(`<div class="hub-empty"><i class="fa fa-check-circle"></i>${__("No redemptions in the selected period")}</div>`);
			return;
		}

		const rows = items.map((r) => {
			const typeCls = r.instrument_type === "Coupon" ? "hub-badge-blue" : "hub-badge-purple";
			return `<tr>
				<td>${r.posting_date ? frappe.datetime.str_to_user(r.posting_date) : "–"}</td>
				<td><code style="font-size:12px;background:var(--subtle-fg);padding:2px 6px;border-radius:4px">${frappe.utils.escape_html(r.code_used || "")}</code></td>
				<td><span class="hub-badge ${typeCls}">${r.instrument_type || "–"}</span></td>
				<td>${frappe.utils.escape_html(r.campaign_name || "–")}</td>
				<td>${r.invoice ? `<a href="/app/pos-invoice/${r.invoice}">${r.invoice}</a>` : "–"}</td>
				<td class="text-right">${frappe.format(r.discount_amount || 0, { fieldtype: "Currency" })}</td>
			</tr>`;
		}).join("");

		$panel.html(`<div class="hub-table-wrap"><table class="hub-table">
			<thead><tr>
				<th>${__("Date")}</th><th>${__("Code")}</th><th>${__("Type")}</th>
				<th>${__("Campaign")}</th><th>${__("Invoice")}</th>
				<th class="text-right">${__("Discount")}</th>
			</tr></thead>
			<tbody>${rows}</tbody>
		</table></div>`);
	}

	/* ── Code Lookup ──────────────────────────────────────────── */

	_render_lookup() {
		const $panel = this.$root.find('[data-panel="lookup"]');
		$panel.html(`
			<div class="hub-lookup-box">
				<div class="hub-lookup-input-row">
					<input type="text" class="form-control hub-lookup-input"
						placeholder="${__("Enter coupon or voucher code…")}" />
					<button class="btn btn-primary-dark hub-lookup-btn">${__("Look Up")}</button>
				</div>
				<div class="hub-lookup-result"></div>
			</div>
		`);
		$panel.find(".hub-lookup-btn").on("click", () => this._do_lookup());
		$panel.find(".hub-lookup-input").on("keydown", (e) => {
			if (e.key === "Enter") this._do_lookup();
		});
	}

	_do_lookup() {
		const code = this.$root.find(".hub-lookup-input").val().trim();
		if (!code) return;
		const $result = this.$root.find(".hub-lookup-result").html(
			`<div class="hub-loading" style="padding:20px"><i class="fa fa-spinner fa-spin"></i> ${__("Looking up…")}</div>`
		);

		frappe.xcall(
			"ch_item_master.ch_item_master.coupon_campaign_api.lookup_code",
			{ code }
		).then((d) => {
			if (!d || !d.found) {
				$result.html(`<div class="hub-empty" style="padding:20px">
					<i class="fa fa-times-circle text-danger" style="font-size:28px"></i>
					<div style="margin-top:8px">${__("Code")} <code>${frappe.utils.escape_html(code)}</code> ${__("not found in any system")}</div>
				</div>`);
				return;
			}
			let rows = "";
			if (d.instrument_type === "Coupon") {
				rows = `
					<tr><td>${__("Reference")}</td><td><a href="/app/coupon-code/${d.reference}">${d.reference}</a></td></tr>
					<tr><td>${__("Used / Max")}</td><td>${d.used} / ${d.maximum_use}</td></tr>
					<tr><td>${__("Valid")}</td><td>${d.valid_from || "–"} → ${d.valid_upto || "–"}</td></tr>`;
			} else {
				rows = `
					<tr><td>${__("Voucher Type")}</td><td><span class="hub-badge hub-badge-purple">${d.voucher_type}</span></td></tr>
					<tr><td>${__("Status")}</td><td><span class="hub-badge hub-badge-${d.status === "Active" ? "green" : "grey"}">${d.status}</span></td></tr>
					<tr><td>${__("Balance")}</td><td>${format_currency(d.balance)} / ${format_currency(d.original_amount)}</td></tr>
					<tr><td>${__("Valid")}</td><td>${d.valid_from || "–"} → ${d.valid_upto || "–"}</td></tr>
					<tr><td>${__("Issued To")}</td><td>${d.issued_to || __("Bearer")}</td></tr>`;
			}
			if (d.campaign) {
				rows += `<tr><td>${__("Campaign")}</td><td><a href="/app/ch-coupon-campaign/${d.campaign_id}">${frappe.utils.escape_html(d.campaign)}</a></td></tr>`;
			} else {
				rows += `<tr><td>${__("Campaign")}</td><td><em>${__("Not from a campaign")}</em></td></tr>`;
			}

			$result.html(`
				<div class="hub-lookup-result-card">
					<div class="hub-lookup-header">
						<span class="hub-badge ${d.instrument_type === "Coupon" ? "hub-badge-blue" : "hub-badge-purple"}">${d.instrument_type}</span>
						<code style="font-size:1.1rem;background:var(--subtle-fg);padding:4px 12px;border-radius:6px;font-weight:700">${frappe.utils.escape_html(d.code)}</code>
					</div>
					<table class="hub-table" style="margin-top:12px">${rows}</table>
				</div>
			`);
		}).catch(() => {
			$result.html(`<div class="hub-empty text-danger">${__("Lookup failed")}</div>`);
		});
	}
}
