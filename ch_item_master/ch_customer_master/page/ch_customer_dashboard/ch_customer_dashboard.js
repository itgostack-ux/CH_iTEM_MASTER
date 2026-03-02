// CH Customer Intelligence Dashboard — Frontend
// Company-aware: dropdown filter + User Permission enforcement
// Loyalty is always shown overall (cross-company)

frappe.pages["ch-customer-dashboard"].on_page_load = function (wrapper) {
const page = frappe.ui.make_app_page({
parent: wrapper,
title: "Customer Intelligence Dashboard",
single_column: true,
});

page.main.addClass("ch-customer-dashboard");
page.set_secondary_action("Refresh", () => load_dashboard(page), "refresh-cw");
page.add_action_item("Customer 360", () => {
let d = new frappe.ui.Dialog({
title: "Customer 360 View",
fields: [{ fieldname: "customer", fieldtype: "Link", options: "Customer", label: "Customer", reqd: 1 }],
primary_action_label: "Open",
primary_action: (v) => { d.hide(); frappe.set_route("app", "customer", v.customer); },
});
d.show();
});

// State
page._selected_company = "All";
page._allowed_companies = [];

// Load companies first, then dashboard
frappe.call({
method: "ch_item_master.ch_customer_master.page.ch_customer_dashboard.ch_customer_dashboard.get_allowed_companies",
freeze: false,
callback: (r) => {
if (r && r.message) {
page._allowed_companies = r.message.companies || [];
page._show_all = r.message.show_all;

// If only 1 company allowed, auto-select it
if (page._allowed_companies.length === 1) {
page._selected_company = page._allowed_companies[0];
}
}
build_company_filter(page);
load_dashboard(page);
},
});
};

function build_company_filter(page) {
// Remove existing filter bar if any
page.main.find(".company-filter-bar").remove();

let options = "";
if (page._show_all) {
options += `<option value="All" ${page._selected_company === "All" ? "selected" : ""}>All Companies</option>`;
}
(page._allowed_companies || []).forEach((c) => {
const sel = page._selected_company === c ? "selected" : "";
options += `<option value="${frappe.utils.escape_html(c)}" ${sel}>${frappe.utils.escape_html(c)}</option>`;
});

const filter_html = `
<div class="company-filter-bar" style="
padding: 10px 20px;
background: var(--fg-color);
border-bottom: 1px solid var(--border-color);
display: flex;
align-items: center;
gap: 12px;
flex-wrap: wrap;
">
<span style="font-weight:600; color: var(--text-muted); font-size: 13px;">
<svg width="14" height="14" style="vertical-align:-2px; margin-right:4px;">
<use href="#icon-company"></use>
</svg>
Company
</span>
<select class="company-select form-control input-xs" style="
max-width: 260px;
font-size: 13px;
height: 30px;
padding: 2px 8px;
">${options}</select>
<span class="company-badge" style="
font-size: 12px;
color: var(--text-muted);
margin-left: auto;
"></span>
</div>
`;

page.main.prepend(filter_html);

page.main.find(".company-select").on("change", function () {
page._selected_company = $(this).val();
load_dashboard(page);
});
}

function update_company_badge(page, data) {
const badge = page.main.find(".company-badge");
if (data.selected_company === "All") {
const count = (page._allowed_companies || []).length;
badge.html(`<span style="color:var(--primary)">Viewing all ${count} companies</span>`);
} else {
badge.html(`<span style="color:var(--primary)">Filtered: ${frappe.utils.escape_html(data.selected_company)}</span>`);
}
}

function load_dashboard(page) {
page.main.find(".dashboard-content").remove();
page.main.append(`
<div class="dashboard-content" style="padding:30px 20px; text-align:center;">
<div class="loading-pulse" style="display:inline-block; width:40px; height:40px; border:3px solid var(--primary); border-top-color:transparent; border-radius:50%; animation: spin 0.8s linear infinite;"></div>
<p style="margin-top:10px; color:var(--text-muted);">Loading dashboard…</p>
<style>@keyframes spin{to{transform:rotate(360deg)}}</style>
</div>
`);

const company = page._selected_company;
frappe.call({
method: "ch_item_master.ch_customer_master.page.ch_customer_dashboard.ch_customer_dashboard.get_dashboard_data",
args: { company: company },
freeze: false,
callback: (r) => {
if (r && r.message) {
page.main.find(".dashboard-content").remove();
update_company_badge(page, r.message);
render_dashboard(page, r.message);
}
},
error: () => {
page.main.find(".dashboard-content").html(
`<div style="padding:40px; text-align:center; color:var(--text-muted);">
<p>Failed to load dashboard data.</p>
<button class="btn btn-sm btn-primary" onclick="load_dashboard(cur_page.page)">Retry</button>
</div>`
);
},
});
}

function render_dashboard(page, data) {
const html = `
<div class="dashboard-content">
<style>${get_css()}</style>

<!-- KPIs -->
<div class="cd-section">
<div class="cd-kpi-grid">
${kpi("users", "Customers", format_number(data.kpis.total_customers), `+${data.kpis.new_this_month} new this month${data.selected_company !== "All" ? " (first txn)" : ""}`, "blue")}
${kpi("star", "VIP", format_number(data.kpis.vip_count), `${data.kpis.active_customers} active (90d)`, "orange")}
${kpi("bar-chart-2", "Revenue", format_currency_short(data.kpis.total_revenue), format_currency_short(data.kpis.revenue_this_month) + " this month", "green")}
${kpi("shopping-bag", "Avg Spend", format_currency(data.kpis.avg_spend), "per customer", "purple")}
${kpi("gift", "Loyalty Pts ★", format_number(data.kpis.total_loyalty), "🌐 always overall", "yellow")}
${kpi("smartphone", "Devices", format_number(data.kpis.total_devices), `🌐 all companies · KYC: ${data.kpis.kyc_verified}/${data.kpis.kyc_total}`, "cyan")}
</div>
</div>

<!-- Alerts -->
${data.alerts.length ? `
<div class="cd-section">
<div class="cd-section-title">
<svg width="16" height="16"><use href="#icon-alert-triangle"></use></svg>
Alerts & Actions
</div>
<div class="cd-alert-grid">
${data.alerts.map(a => alert_card(a)).join("")}
</div>
</div>` : ""}

<!-- Insights -->
${data.insights.length ? `
<div class="cd-section">
<div class="cd-section-title">
<svg width="16" height="16"><use href="#icon-bulb"></use></svg>
AI Insights${data.selected_company !== "All" ? ` <small style="color:var(--text-muted)">(company-specific where applicable)</small>` : ""}
</div>
<div class="cd-insight-grid">
${data.insights.map(i => insight_card(i)).join("")}
</div>
</div>` : ""}

<!-- Row: Segments | Loyalty | Revenue Trend -->
<div class="cd-section cd-row-3">
<div class="cd-card">
<div class="cd-card-title">Customer Segments</div>
${segment_chart(data.segments)}
</div>
<div class="cd-card">
<div class="cd-card-title">Loyalty Overview ★ <small style="color:var(--text-muted)">(always overall)</small></div>
${loyalty_widget(data.loyalty_overview)}
</div>
<div class="cd-card">
<div class="cd-card-title">Revenue Trend (6 months)</div>
${revenue_trend_chart(data.revenue_trend)}
</div>
</div>

<!-- Row: Company Breakdown | Devices -->
<div class="cd-section cd-row-2">
<div class="cd-card">
<div class="cd-card-title">Company Breakdown</div>
${company_table(data.company_breakdown)}
</div>
<div class="cd-card">
<div class="cd-card-title">Device Analytics <small style="color:var(--text-muted)">🌐 cross-company</small></div>
${device_widget(data.device_analytics)}
</div>
</div>

<!-- Top Customers -->
<div class="cd-section">
<div class="cd-section-title">
<svg width="16" height="16"><use href="#icon-user"></use></svg>
Top Customers${data.selected_company !== "All" ? ` — ${frappe.utils.escape_html(data.selected_company)}` : ""}
</div>
${top_customers_table(data.top_customers)}
</div>

<!-- Row: Recent Activity | Referrals | Store Performance -->
<div class="cd-section cd-row-3">
<div class="cd-card" style="max-height:400px; overflow:auto;">
<div class="cd-card-title">Recent Activity (7 days)</div>
${data.recent_activity.length ? data.recent_activity.map(a => activity_item(a)).join("") : `<p class="text-muted">No recent activity.</p>`}
</div>
<div class="cd-card">
<div class="cd-card-title">Referral Program <small style="color:var(--text-muted)">🌐</small></div>
${referral_widget(data.referral_stats)}
</div>
<div class="cd-card" style="max-height:400px; overflow:auto;">
<div class="cd-card-title">Store Performance</div>
${store_table(data.store_performance)}
</div>
</div>

<!-- KYC bar -->
<div class="cd-section">
<div class="cd-card">
<div class="cd-card-title">KYC Verification Progress</div>
${kyc_bar(data.kpis)}
</div>
</div>
</div>
`;
page.main.append(html);
}

// ── Component renderers ─────────────────────────────────────────────────────

function kpi(icon, label, value, sub, color) {
const colors = {
blue: "#4299e1", green: "#48bb78", orange: "#ed8936", purple: "#9f7aea",
yellow: "#ecc94b", cyan: "#38b2ac", red: "#fc8181",
};
const c = colors[color] || colors.blue;
return `
<div class="cd-kpi" style="border-left:3px solid ${c}">
<div class="cd-kpi-icon" style="background:${c}22; color:${c}">
<svg width="18" height="18"><use href="#icon-${icon}"></use></svg>
</div>
<div class="cd-kpi-body">
<div class="cd-kpi-label">${label}</div>
<div class="cd-kpi-value">${value}</div>
<div class="cd-kpi-sub">${sub}</div>
</div>
</div>`;
}

function alert_card(a) {
const typeColor = { warning: "#ed8936", danger: "#e53e3e", info: "#4299e1" };
const c = typeColor[a.type] || "#4299e1";
const action = a.action ? `<a href="${a.action}" class="cd-link" style="color:${c}">View →</a>` : "";
return `
<div class="cd-alert" style="border-left: 3px solid ${c}; background: ${c}08;">
<div style="flex:1; font-size:13px; color:var(--text-color);">${a.title}</div>
${action}
</div>`;
}

function insight_card(ins) {
const sevColor = { high: "#e53e3e", medium: "#ed8936", low: "#48bb78", info: "#4299e1" };
const c = sevColor[ins.severity] || "#4299e1";
const action = ins.action ? `<a href="${ins.action}" class="cd-link" style="color:${c}; font-size:12px;">View →</a>` : "";
return `
<div class="cd-insight" style="border-left:3px solid ${c}; background:${c}08">
<div style="font-weight:600; font-size:13px; margin-bottom:4px; color:var(--text-color);">${ins.title}</div>
<div style="font-size:12px; color:var(--text-muted); margin-bottom:6px;">${ins.description || ""}</div>
<div style="display:flex; align-items:center; gap:8px;">
<span class="cd-badge" style="background:${c}22; color:${c}">${ins.type}</span>
${action}
</div>
</div>`;
}

function segment_chart(segments) {
if (!segments || !segments.length) return `<p class="text-muted">No segment data.</p>`;
const total = segments.reduce((s, x) => s + x.count, 0);
const segColors = { VIP: "#9f7aea", Regular: "#4299e1", New: "#48bb78", Dormant: "#ed8936", Churned: "#e53e3e", Unclassified: "#a0aec0" };
let html = `<div style="margin-bottom:10px;">`;
html += `<div style="display:flex; height:18px; border-radius:9px; overflow:hidden; margin-bottom:12px;">`;
segments.forEach((s) => {
const pct = ((s.count / total) * 100).toFixed(1);
const c = segColors[s.segment] || "#a0aec0";
html += `<div style="width:${pct}%; background:${c};" title="${s.segment}: ${pct}%"></div>`;
});
html += `</div>`;
segments.forEach((s) => {
const pct = ((s.count / total) * 100).toFixed(1);
const c = segColors[s.segment] || "#a0aec0";
html += `<div style="display:flex; align-items:center; gap:8px; margin-bottom:6px; font-size:13px;">
<span style="width:10px; height:10px; border-radius:50%; background:${c}; flex-shrink:0;"></span>
<span style="flex:1">${s.segment}</span>
<span style="font-weight:600">${s.count}</span>
<span style="color:var(--text-muted); width:45px; text-align:right;">${pct}%</span>
</div>`;
});
html += `</div>`;
return html;
}

function loyalty_widget(lo) {
if (!lo || !lo.total_balance) return `<p class="text-muted">No loyalty data yet.</p>`;
return `
<div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:10px;">
<div class="cd-mini-stat" style="background:#48bb7822">
<div style="font-size:18px; font-weight:700; color:#48bb78">${format_number(lo.earned)}</div>
<div style="font-size:11px; color:var(--text-muted)">Earned</div>
</div>
<div class="cd-mini-stat" style="background:#e53e3e22">
<div style="font-size:18px; font-weight:700; color:#e53e3e">${format_number(lo.redeemed)}</div>
<div style="font-size:11px; color:var(--text-muted)">Redeemed</div>
</div>
<div class="cd-mini-stat" style="background:#ed893622">
<div style="font-size:18px; font-weight:700; color:#ed8936">${format_number(lo.expired)}</div>
<div style="font-size:11px; color:var(--text-muted)">Expired</div>
</div>
<div class="cd-mini-stat" style="background:#9f7aea22">
<div style="font-size:18px; font-weight:700; color:#9f7aea">${format_number(lo.referral_bonus || 0)}</div>
<div style="font-size:11px; color:var(--text-muted)">Referral Bonus</div>
</div>
</div>
<div style="text-align:center; padding:8px; background:var(--bg-color); border-radius:8px;">
<div style="font-size:24px; font-weight:700; color:var(--primary)">${format_number(lo.total_balance)}</div>
<div style="font-size:12px; color:var(--text-muted)">Net Balance · ${lo.customers_enrolled || 0} enrolled</div>
</div>`;
}

function revenue_trend_chart(months) {
if (!months || !months.length) return `<p class="text-muted">No revenue data yet.</p>`;
const max_rev = Math.max(...months.map(m => m.revenue), 1);
let html = `<div>`;
months.forEach((m) => {
const pct = Math.max((m.revenue / max_rev) * 100, 2);
html += `
<div style="display:flex; align-items:center; gap:8px; margin-bottom:6px; font-size:12px;">
<span style="width:60px; flex-shrink:0; color:var(--text-muted);">${m.month}</span>
<div style="flex:1; background:var(--bg-color); border-radius:4px; height:20px; overflow:hidden;">
<div style="width:${pct}%; background:var(--primary); height:100%; border-radius:4px; min-width:2px;"></div>
</div>
<span style="width:80px; text-align:right; font-weight:600;">${format_currency_short(m.revenue)}</span>
<span style="width:50px; text-align:right; color:var(--text-muted);">${m.transactions} txn</span>
</div>`;
});
html += `</div>`;
return html;
}

function company_table(rows) {
if (!rows || !rows.length) return `<p class="text-muted">No company transaction data.</p>`;
let html = `<table class="cd-table"><thead><tr>
<th>Company</th><th>Customers</th><th>Txns</th><th>Revenue</th><th>Avg Ticket</th>
</tr></thead><tbody>`;
rows.forEach(r => {
html += `<tr>
<td><strong>${r.company}</strong></td>
<td>${format_number(r.customers)}</td>
<td>${format_number(r.transactions)}</td>
<td>${format_currency(r.revenue)}</td>
<td>${format_currency(r.avg_ticket)}</td>
</tr>`;
});
html += `</tbody></table>`;
return html;
}

function device_widget(da) {
if (!da || !da.total) return `<p class="text-muted">No device data yet.</p>`;
let html = `
<div style="display:flex; gap:12px; margin-bottom:10px;">
<div class="cd-mini-stat" style="flex:1; background:var(--bg-color);">
<div style="font-size:20px; font-weight:700;">${da.total}</div>
<div style="font-size:11px; color:var(--text-muted)">Total Devices</div>
</div>
<div class="cd-mini-stat" style="flex:1; background:#48bb7822;">
<div style="font-size:20px; font-weight:700; color:#48bb78">${da.with_vas}</div>
<div style="font-size:11px; color:var(--text-muted)">With VAS (${da.vas_adoption_pct}%)</div>
</div>
</div>`;
if (da.by_status && da.by_status.length) {
html += `<div style="font-size:12px; font-weight:600; margin-bottom:6px; color:var(--text-muted)">By Status</div>`;
da.by_status.forEach(s => {
html += `<div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom:3px;">
<span>${s.status}</span><span style="font-weight:600">${s.count}</span>
</div>`;
});
}
if (da.by_brand && da.by_brand.length) {
html += `<div style="font-size:12px; font-weight:600; margin-top:10px; margin-bottom:6px; color:var(--text-muted)">Top Brands</div>`;
da.by_brand.slice(0, 5).forEach(b => {
html += `<div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom:3px;">
<span>${b.brand}</span><span style="font-weight:600">${b.count}</span>
</div>`;
});
}
return html;
}

function top_customers_table(rows) {
if (!rows || !rows.length) return `<p class="text-muted">No customer data.</p>`;
let html = `<table class="cd-table"><thead><tr>
<th>Customer</th><th>Segment</th><th>Total Spend</th><th>Services</th><th>Devices</th><th>Loyalty</th><th>Last Visit</th>
</tr></thead><tbody>`;
const segColors = { VIP: "#9f7aea", Regular: "#4299e1", New: "#48bb78", Dormant: "#ed8936", Churned: "#e53e3e" };
rows.forEach(r => {
const c = segColors[r.segment] || "#a0aec0";
html += `<tr>
<td><a href="/app/customer/${r.customer}" class="cd-link">${r.customer_name || r.customer}</a>
${r.mobile_no ? `<br><small style="color:var(--text-muted)">${r.mobile_no}</small>` : ""}</td>
<td><span class="cd-badge" style="background:${c}22; color:${c}">${r.segment}</span></td>
<td>${format_currency(r.total_spend)}</td>
<td>${r.total_services || 0}</td>
<td>${r.devices || 0}</td>
<td>${format_number(r.loyalty_balance || 0)} pts</td>
<td>${r.last_visit || "—"}</td>
</tr>`;
});
html += `</tbody></table>`;
return html;
}

function activity_item(a) {
const typeIcons = { customer: "user", purchase: "shopping-cart", loyalty: "gift", service: "tool", buyback: "refresh-cw" };
const typeColors = { customer: "#4299e1", purchase: "#48bb78", loyalty: "#ecc94b", service: "#9f7aea", buyback: "#ed8936" };
const icon = typeIcons[a.type] || "activity";
const c = typeColors[a.type] || "#4299e1";
const amount = a.amount ? ` · ${format_currency(a.amount)}` : "";
const link = a.link ? `<a href="${a.link}" class="cd-link" style="font-size:11px; color:${c}">→</a>` : "";
return `
<div style="display:flex; align-items:flex-start; gap:8px; padding:6px 0; border-bottom:1px solid var(--border-color); font-size:12px;">
<div style="width:24px; height:24px; border-radius:50%; background:${c}18; display:flex; align-items:center; justify-content:center; flex-shrink:0;">
<svg width="12" height="12" style="color:${c}"><use href="#icon-${icon}"></use></svg>
</div>
<div style="flex:1; min-width:0;">
<div style="font-weight:500; color:var(--text-color);">${a.description}${amount}</div>
<div style="color:var(--text-muted); font-size:11px;">${a.detail || ""} · ${frappe.datetime.prettyDate(a.timestamp)}</div>
</div>
${link}
</div>`;
}

function referral_widget(rs) {
if (!rs) return `<p class="text-muted">No referral data.</p>`;
let html = `
<div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; margin-bottom:12px; text-align:center;">
<div class="cd-mini-stat" style="background:var(--bg-color);">
<div style="font-size:18px; font-weight:700">${rs.total_referrers}</div>
<div style="font-size:11px; color:var(--text-muted)">Referrers</div>
</div>
<div class="cd-mini-stat" style="background:var(--bg-color);">
<div style="font-size:18px; font-weight:700">${rs.total_referred}</div>
<div style="font-size:11px; color:var(--text-muted)">Referred</div>
</div>
<div class="cd-mini-stat" style="background:var(--bg-color);">
<div style="font-size:18px; font-weight:700">${rs.conversion_rate}%</div>
<div style="font-size:11px; color:var(--text-muted)">Rate</div>
</div>
</div>`;
if (rs.top_referrers && rs.top_referrers.length) {
html += `<div style="font-size:12px; font-weight:600; margin-bottom:6px; color:var(--text-muted)">Top Referrers</div>`;
rs.top_referrers.forEach(r => {
html += `<div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom:4px;">
<a href="/app/customer/${r.referrer}" class="cd-link">${r.customer_name}</a>
<span style="font-weight:600">${r.referral_count}</span>
</div>`;
});
}
return html;
}

function store_table(rows) {
if (!rows || !rows.length) return `<p class="text-muted">No store visit data.</p>`;
let html = `<table class="cd-table"><thead><tr>
<th>Store</th><th>Company</th><th>Visits</th><th>Customers</th><th>Last Visit</th>
</tr></thead><tbody>`;
rows.forEach(r => {
html += `<tr>
<td>${r.store}</td>
<td><small>${r.company || ""}</small></td>
<td>${r.total_visits}</td>
<td>${r.unique_customers}</td>
<td>${r.last_visit || "—"}</td>
</tr>`;
});
html += `</tbody></table>`;
return html;
}

function kyc_bar(kpis) {
const pct = kpis.kyc_total ? Math.round(kpis.kyc_verified / kpis.kyc_total * 100) : 0;
const c = pct >= 80 ? "#48bb78" : pct >= 50 ? "#ecc94b" : "#e53e3e";
return `
<div style="display:flex; align-items:center; gap:12px;">
<div style="flex:1; background:var(--bg-color); border-radius:8px; height:24px; overflow:hidden;">
<div style="width:${pct}%; background:${c}; height:100%; border-radius:8px; transition:width .3s;"></div>
</div>
<span style="font-weight:700; font-size:16px; color:${c}">${pct}%</span>
<span style="color:var(--text-muted); font-size:12px;">${kpis.kyc_verified}/${kpis.kyc_total} verified</span>
</div>`;
}

// ── Formatters ──────────────────────────────────────────────────────────────

function format_number(n) { return (n || 0).toLocaleString("en-IN"); }
function format_currency(n) { return "₹" + (n || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 }); }
function format_currency_short(n) {
n = n || 0;
if (n >= 10000000) return "₹" + (n / 10000000).toFixed(2) + " Cr";
if (n >= 100000) return "₹" + (n / 100000).toFixed(2) + " L";
if (n >= 1000) return "₹" + (n / 1000).toFixed(1) + " K";
return "₹" + n.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

// ── CSS ─────────────────────────────────────────────────────────────────────

function get_css() {
return `
.ch-customer-dashboard { background: var(--bg-color); }
.cd-section { margin-bottom: 20px; padding: 0 20px; }
.cd-section-title { font-size: 15px; font-weight: 700; margin-bottom: 12px; display:flex; align-items:center; gap:8px; color:var(--heading-color); }
.cd-kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
.cd-kpi { background: var(--fg-color); border-radius: 10px; padding: 14px; display: flex; gap: 12px; align-items: center; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
.cd-kpi-icon { width: 40px; height: 40px; border-radius: 10px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.cd-kpi-body { min-width: 0; }
.cd-kpi-label { font-size: 11px; text-transform: uppercase; letter-spacing: .5px; color: var(--text-muted); font-weight: 600; }
.cd-kpi-value { font-size: 22px; font-weight: 700; color: var(--text-color); line-height: 1.2; }
.cd-kpi-sub { font-size: 11px; color: var(--text-muted); }
.cd-alert-grid { display: grid; gap: 8px; }
.cd-alert { display: flex; align-items: center; gap: 12px; padding: 10px 14px; border-radius: 8px; }
.cd-insight-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px; }
.cd-insight { padding: 12px 14px; border-radius: 8px; }
.cd-badge { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px; display: inline-block; }
.cd-row-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.cd-row-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
.cd-card { background: var(--fg-color); border-radius: 10px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
.cd-card-title { font-size: 13px; font-weight: 700; margin-bottom: 12px; color: var(--heading-color); }
.cd-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.cd-table th { text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--border-color); font-size: 11px; text-transform: uppercase; letter-spacing: .5px; color: var(--text-muted); }
.cd-table td { padding: 6px 8px; border-bottom: 1px solid var(--border-color); }
.cd-table tr:hover { background: var(--bg-color); }
.cd-link { color: var(--primary); text-decoration: none; font-weight: 500; }
.cd-link:hover { text-decoration: underline; }
.cd-mini-stat { padding: 10px; border-radius: 8px; text-align: center; }
@media (max-width: 768px) {
.cd-row-3 { grid-template-columns: 1fr; }
.cd-row-2 { grid-template-columns: 1fr; }
.cd-kpi-grid { grid-template-columns: repeat(2, 1fr); }
.cd-insight-grid { grid-template-columns: 1fr; }
}
`;
}
