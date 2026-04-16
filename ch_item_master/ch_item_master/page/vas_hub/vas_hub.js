frappe.pages["vas-hub"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("VAS Hub"),
		single_column: true,
	});
	wrapper.vas_hub = new VASHub(page);
};

frappe.pages["vas-hub"].refresh = function (wrapper) {
	wrapper.vas_hub && wrapper.vas_hub.refresh();
};

class VASHub {
	constructor(page) {
		this.page = page;
		this._timer = null;
		this._setup_controls();
		this._setup_container();
		this.refresh();
		this._start_auto_refresh();
	}

	_setup_controls() {
		this.company_field = this.page.add_field({
			fieldname: "company", label: __("Company"),
			fieldtype: "Link", options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			change: () => this.refresh(),
		});
		this.store_field = this.page.add_field({
			fieldname: "store", label: __("Store / Warehouse"),
			fieldtype: "Link", options: "Warehouse",
			get_query: () => {
				const company = this.company_field?.get_value();
				const filters = { is_group: 0 };
				if (company) filters.company = company;
				return { filters };
			},
			change: () => this.refresh(),
		});
		this.from_date_field = this.page.add_field({
			fieldname: "from_date", label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			change: () => this.refresh(),
		});
		this.to_date_field = this.page.add_field({
			fieldname: "to_date", label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			change: () => this.refresh(),
		});
		this.page.add_button(__("Refresh"), () => this.refresh(), { icon: "refresh" });
	}

	_setup_container() {
		this.$root = $(`<div class="hub-root"></div>`).appendTo(this.page.body);
	}

	refresh() {
		const company = this.company_field?.get_value() || "";
		const store = this.store_field?.get_value() || "";
		const from_date = this.from_date_field?.get_value() || "";
		const to_date = this.to_date_field?.get_value() || "";
		this.$root.html(`<div class="hub-loading"><i class="fa fa-spinner fa-spin"></i> ${__("Loading VAS Hub...")}</div>`);
		frappe.xcall("ch_item_master.ch_item_master.page.vas_hub.vas_hub_api.get_vas_hub_data",
			{ company, store, from_date, to_date })
			.then((data) => this._render(data))
			.catch(() => {
				this.$root.html(`<div class="hub-loading text-danger">${__("Failed to load data. Please try again.")}</div>`);
			});
	}

	_start_auto_refresh() {
		this._timer = setInterval(() => this.refresh(), 60000);
		$(this.page.parent).on("remove", () => clearInterval(this._timer));
	}

	_render(data) {
		this.$root.empty();
		this._render_header();
		this._render_pipeline(data.pipeline || []);
		this._render_kpis(data.kpis || []);
		this._render_actions();
		this._render_intelligence(data.ai_insights || [], data.financial_control || {});
		this._render_tables(data);
	}

	_render_header() {
		this.$root.append(`
			<div class="hub-header">
				<div>
					<div class="hub-title"><i class="fa fa-shield"></i> ${__("VAS Hub")}</div>
					<div class="hub-subtitle">${__("Value-Added Services: Warranty Plans → Sold Plans → Claims → Vouchers")}</div>
				</div>
				<div class="hub-auto-badge">
					<span class="pulse-dot"></span> ${__("Live · Auto-refreshes every 60s")}
				</div>
			</div>
		`);
	}

	_render_pipeline(steps) {
		const arrow = `<div class="hub-flow-connector">
			<svg width="32" height="24" viewBox="0 0 32 24" fill="none" stroke="currentColor"
				stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
				<path d="M4 12H24M18 6l6 6-6 6"/>
			</svg>
		</div>`;
		const nodes = steps.map((s, i) => {
			const node = `
				<div class="hub-flow-node" data-step="${s.key}">
					<div class="hub-flow-badge" style="background:${s.color}">${s.count}</div>
					<div class="hub-flow-meta">
						<i class="fa fa-${s.icon}"></i>
						<span class="hub-flow-name">${__(s.label)}</span>
					</div>
					<div class="hub-flow-sub">${s.sub || ""}</div>
				</div>`;
			return i < steps.length - 1 ? node + arrow : node;
		}).join("");
		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-random"></i> ${__("VAS Pipeline")}</h5>
				<div class="hub-flow-wrap"><div class="hub-flow">${nodes}</div></div>
			</div>
		`);
	}

	_render_kpis(kpis) {
		const cards = kpis.map((k) => {
			const val = k.fmt === "currency"
				? frappe.format(k.value, { fieldtype: "Currency" })
				: k.value;
			return `<div class="hub-kpi-card" style="--kpi-color:${k.color}" data-kpi="${k.key}">
				<div class="hub-kpi-value">${val}</div>
				<div class="hub-kpi-label">${__(k.label)}</div>
			</div>`;
		}).join("");
		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-tachometer"></i> ${__("Key Metrics")}</h5>
				<div class="hub-kpi-grid">${cards}</div>
			</div>
		`);
	}

	_render_actions() {
		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-bolt"></i> ${__("Quick Actions")}</h5>
				<div class="hub-actions-grid">
					<button class="hub-action-btn" onclick="frappe.set_route('List','CH Sold Plan',{status:'Active'})"><i class="fa fa-certificate"></i> ${__("Active Plans")}</button>
					<button class="hub-action-btn" onclick="frappe.set_route('List','CH Warranty Claim',{status:'Pending Approval'})"><i class="fa fa-exclamation-circle"></i> ${__("Pending Claims")}</button>
					<button class="hub-action-btn" onclick="frappe.set_route('List','CH Voucher',{status:'Active'})"><i class="fa fa-ticket"></i> ${__("Active Vouchers")}</button>
					<button class="hub-action-btn" onclick="frappe.set_route('List','CH Warranty Plan')"><i class="fa fa-list"></i> ${__("Warranty Plans")}</button>
					<button class="hub-action-btn" onclick="frappe.set_route('List','CH Sold Plan',{status:'Expired'})"><i class="fa fa-clock-o"></i> ${__("Expired Plans")}</button>
					<button class="hub-action-btn" onclick="frappe.set_route('List','CH Warranty Claim')"><i class="fa fa-gavel"></i> ${__("All Claims")}</button>
				</div>
			</div>
		`);
	}

	_render_intelligence(insights, financial) {
		const insightCards = insights.map((i) => `
			<div class="hub-insight-card hub-insight-${(i.severity || 'medium').toLowerCase()}">
				<div class="hub-insight-top">
					<span class="hub-badge hub-badge-${i.severity === 'High' ? 'red' : i.severity === 'Low' ? 'green' : 'yellow'}">${i.severity}</span>
					<span class="hub-insight-title">${i.title}</span>
				</div>
				<div class="hub-insight-detail">${i.detail}</div>
				${i.action ? `<div class="hub-insight-action">${i.action}</div>` : ""}
			</div>
		`).join("");

		const fc = financial;
		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-brain"></i> ${__("AI Insights & VAS Control")}</h5>
				<div class="hub-intel-grid">
					<div class="hub-intel-panel">${insightCards || '<div class="hub-empty">No insights</div>'}</div>
					<div class="hub-intel-panel">
						<div class="hub-mini-kpi-grid">
							<div class="hub-mini-kpi" style="--mini-color:#d946ef">
								<div class="hub-mini-kpi-value">${fc.active_plans || 0}</div>
								<div class="hub-mini-kpi-label">${__("Active Plans")}</div>
							</div>
							<div class="hub-mini-kpi" style="--mini-color:#ef4444">
								<div class="hub-mini-kpi-value">${fc.claim_rate || "0%"}</div>
								<div class="hub-mini-kpi-label">${__("Claim Rate")}</div>
							</div>
							<div class="hub-mini-kpi" style="--mini-color:#059669">
								<div class="hub-mini-kpi-value">${fc.voucher_utilization || "0%"}</div>
								<div class="hub-mini-kpi-label">${__("Voucher Utilization")}</div>
							</div>
							<div class="hub-mini-kpi" style="--mini-color:#3b82f6">
								<div class="hub-mini-kpi-value">${frappe.format(fc.vas_revenue_mtd || 0, {fieldtype:"Currency"})}</div>
								<div class="hub-mini-kpi-label">${__("VAS Revenue MTD")}</div>
							</div>
						</div>
					</div>
				</div>
			</div>
		`);
	}

	_render_tables(data) {
		const tabs = [
			{ key: "plans", label: __("Sold Plans"), count: (data.sold_plans || []).length },
			{ key: "claims", label: __("Warranty Claims"), count: (data.warranty_claims || []).length },
			{ key: "vouchers", label: __("Vouchers"), count: (data.vouchers || []).length },
			{ key: "expiring", label: __("Expiring Soon"), count: (data.expiring_soon || []).length },
			{ key: "plan_types", label: __("Plan Performance"), count: (data.plan_performance || []).length },
		];
		const tabBtns = tabs.map((t, i) =>
			`<button class="hub-tab${i === 0 ? " active" : ""}" data-tab="${t.key}">
				${t.label} <span class="badge">${t.count}</span>
			</button>`
		).join("");

		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-table"></i> ${__("Detail Tables")}</h5>
				<div class="hub-tabs">${tabBtns}</div>
				<div class="hub-tab-panel active" data-panel="plans">${this._table_plans(data.sold_plans || [])}</div>
				<div class="hub-tab-panel" data-panel="claims">${this._table_claims(data.warranty_claims || [])}</div>
				<div class="hub-tab-panel" data-panel="vouchers">${this._table_vouchers(data.vouchers || [])}</div>
				<div class="hub-tab-panel" data-panel="expiring">${this._table_expiring(data.expiring_soon || [])}</div>
				<div class="hub-tab-panel" data-panel="plan_types">${this._table_plan_perf(data.plan_performance || [])}</div>
			</div>
		`);

		this.$root.find(".hub-tab").on("click", (e) => {
			const key = $(e.currentTarget).data("tab");
			this.$root.find(".hub-tab").removeClass("active");
			$(e.currentTarget).addClass("active");
			this.$root.find(".hub-tab-panel").removeClass("active");
			this.$root.find(`[data-panel="${key}"]`).addClass("active");
		});
	}

	_lnk(dt, name) { return `<a href="/app/${frappe.router.slug(dt)}/${name}">${name}</a>`; }
	_badge(status) {
		const map = { "Active": "green", "Expired": "orange", "Claimed": "blue", "Pending Approval": "yellow", "Approved": "green", "Ticket Created": "purple", "Closed": "grey", "Final QC Passed": "green", "Fee Paid": "blue", "Fully Used": "grey", "Partially Used": "yellow", "Rejected": "red" };
		return `<span class="hub-badge hub-badge-${map[status] || "grey"}">${status}</span>`;
	}

	_table_plans(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-certificate"></i> ${__("No sold plans")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Plan")}</th><th>${__("Customer")}</th><th>${__("Plan Type")}</th>
			<th>${__("Start")}</th><th>${__("End")}</th><th>${__("Status")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${this._lnk("CH Sold Plan", r.name)}</td>
			<td>${r.customer_name || r.customer || ""}</td>
			<td>${r.warranty_plan || ""}</td>
			<td>${r.start_date ? frappe.datetime.str_to_user(r.start_date) : "-"}</td>
			<td>${r.end_date ? frappe.datetime.str_to_user(r.end_date) : "-"}</td>
			<td>${this._badge(r.status)}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}

	_table_claims(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-gavel"></i> ${__("No warranty claims")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Claim")}</th><th>${__("Customer")}</th><th>${__("Plan")}</th>
			<th>${__("Date")}</th><th>${__("Status")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${this._lnk("CH Warranty Claim", r.name)}</td>
			<td>${r.customer_name || r.customer || ""}</td>
			<td>${r.sold_plan || ""}</td>
			<td>${r.claim_date ? frappe.datetime.str_to_user(r.claim_date) : "-"}</td>
			<td>${this._badge(r.claim_status)}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}

	_table_vouchers(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-ticket"></i> ${__("No vouchers")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Voucher")}</th><th>${__("Customer")}</th><th>${__("Status")}</th>
			<th class="text-right">${__("Amount")}</th><th class="text-right">${__("Used")}</th>
			<th>${__("Expiry")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${this._lnk("CH Voucher", r.name)}</td>
			<td>${r.issued_to_name || r.issued_to || ""}</td>
			<td>${this._badge(r.status)}</td>
			<td class="text-right">${frappe.format(r.original_amount || 0, {fieldtype:"Currency"})}</td>
			<td class="text-right">${frappe.format((r.original_amount || 0) - (r.balance || 0), {fieldtype:"Currency"})}</td>
			<td>${r.valid_upto ? frappe.datetime.str_to_user(r.valid_upto) : "-"}</td></td>
		</tr>`).join("")}</tbody></table></div>`;
	}

	_table_expiring(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-check-circle"></i> ${__("Nothing expiring soon")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Plan")}</th><th>${__("Customer")}</th><th>${__("Type")}</th>
			<th>${__("Expiry")}</th><th>${__("Days Left")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${this._lnk("CH Sold Plan", r.name)}</td>
			<td>${r.customer_name || r.customer || ""}</td>
			<td>${r.warranty_plan || ""}</td>
			<td>${r.end_date ? frappe.datetime.str_to_user(r.end_date) : "-"}</td>
			<td class="text-warning">${r.days_left || "-"}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}

	_table_plan_perf(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-bar-chart"></i> ${__("No plan data")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Plan Type")}</th><th class="text-right">${__("Sold")}</th>
			<th class="text-right">${__("Active")}</th><th class="text-right">${__("Claimed")}</th>
			<th class="text-right">${__("Claim Rate")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${r.warranty_plan || "N/A"}</td>
			<td class="text-right">${r.total || 0}</td>
			<td class="text-right">${r.active || 0}</td>
			<td class="text-right">${r.claimed || 0}</td>
			<td class="text-right">${r.claim_rate || "0%"}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}
}
