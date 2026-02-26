// Copyright (c) 2026, GoStack and contributors
// CH Item Master Dashboard — Beautiful AI-powered overview page

frappe.pages['ch-item-master-dashboard'].on_page_load = function (wrapper) {
	let page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Item Master Dashboard',
		single_column: true,
	});

	page.main.html('<div id="ch-dashboard-root" class="ch-dash-loading">' +
		'<div style="text-align:center;padding:80px 0;">' +
		'<div class="spinner-border text-primary" role="status"></div>' +
		'<p class="text-muted mt-3">Loading dashboard...</p></div></div>');

	// Refresh button
	page.set_primary_action(__('Refresh'), () => load_dashboard(page), 'refresh-ccw');

	load_dashboard(page);
};

function load_dashboard(page) {
	frappe.call({
		method: 'ch_item_master.ch_item_master.page.ch_item_master_dashboard.ch_item_master_dashboard.get_dashboard_data',
		freeze: false,
		callback(r) {
			if (r.message) {
				render_dashboard(page, r.message);
			}
		},
	});
}

function render_dashboard(page, data) {
	let html = `<div class="ch-dash">
		<style>${get_dashboard_css()}</style>

		<!-- KPI Cards -->
		<div class="ch-section">
			<div class="ch-kpi-grid">
				${kpi_card('stock', data.kpis.total_items, 'Items', '/app/item?ch_model=%5B%22is%22%2C%22set%22%5D', 'purple')}
				${kpi_card('project', data.kpis.total_models, 'Models', '/app/ch-model', 'blue')}
				${kpi_card('tag', data.kpis.active_prices, 'Active Prices', '/app/ch-item-price?status=Active', 'green')}
				${kpi_card('sell', data.kpis.active_offers, 'Active Offers', '/app/ch-item-offer?status=Active', 'orange')}
				${kpi_card('folder-normal', data.kpis.total_categories, 'Categories', '/app/ch-category', 'cyan')}
				${kpi_card('list', data.kpis.total_sub_categories, 'Sub Categories', '/app/ch-sub-category', 'teal')}
				${kpi_card('tool', data.kpis.total_manufacturers, 'Manufacturers', '/app/manufacturer', 'gray')}
				${kpi_card('share-people', data.kpis.active_channels, 'Channels', '/app/ch-price-channel', 'indigo')}
			</div>
		</div>

		<!-- Alerts Section -->
		${data.alerts.length ? `
		<div class="ch-section">
			<h5 class="ch-section-title">
				<svg class="ch-icon"><use href="#icon-alert-triangle"></use></svg>
				Alerts & Warnings
			</h5>
			<div class="ch-alerts-grid">
				${data.alerts.map(a => alert_card(a)).join('')}
			</div>
		</div>` : ''}

		<!-- AI Insights Section -->
		${data.insights.length ? `
		<div class="ch-section">
			<h5 class="ch-section-title">
				<svg class="ch-icon"><use href="#icon-bulb"></use></svg>
				AI Insights & Recommendations
			</h5>
			<div class="ch-insights-grid">
				${data.insights.map(i => insight_card(i)).join('')}
			</div>
		</div>` : ''}

		<div class="ch-two-col">
			<!-- Coverage Pipeline -->
			<div class="ch-section ch-col">
				<h5 class="ch-section-title">
					<svg class="ch-icon"><use href="#icon-bar-chart"></use></svg>
					Category Coverage Pipeline
				</h5>
				<div class="ch-coverage-table">
					<table class="table table-sm">
						<thead>
							<tr>
								<th>Category</th>
								<th class="text-right">Models</th>
								<th class="text-right">Templates</th>
								<th class="text-right">Variants</th>
								<th class="text-right">Priced</th>
								<th>Pipeline</th>
							</tr>
						</thead>
						<tbody>
							${(data.coverage || []).map(c => coverage_row(c)).join('')}
						</tbody>
					</table>
				</div>
			</div>

			<!-- Pricing Health -->
			<div class="ch-section ch-col">
				<h5 class="ch-section-title">
					<svg class="ch-icon"><use href="#icon-heart"></use></svg>
					Pricing Health
				</h5>
				${pricing_health_widget(data.pricing_health)}
			</div>
		</div>

		<div class="ch-two-col">
			<!-- Channel Comparison -->
			<div class="ch-section ch-col">
				<h5 class="ch-section-title">
					<svg class="ch-icon"><use href="#icon-globe"></use></svg>
					Channel Comparison
				</h5>
				<table class="table table-sm">
					<thead>
						<tr>
							<th>Channel</th>
							<th class="text-right">Items</th>
							<th class="text-right">Avg SP</th>
							<th class="text-right">Avg Disc %</th>
							<th class="text-right">Offers</th>
						</tr>
					</thead>
					<tbody>
						${(data.channel_comparison || []).map(ch => `
						<tr>
							<td><strong>${ch.channel_name}</strong></td>
							<td class="text-right">${ch.items_priced || 0}</td>
							<td class="text-right">${format_currency(ch.avg_selling_price)}</td>
							<td class="text-right">${(ch.avg_discount_pct || 0).toFixed(1)}%</td>
							<td class="text-right">${ch.items_with_offers || 0}</td>
						</tr>`).join('')}
					</tbody>
				</table>
			</div>

			<!-- Recent Activity -->
			<div class="ch-section ch-col">
				<h5 class="ch-section-title">
					<svg class="ch-icon"><use href="#icon-activity"></use></svg>
					Recent Activity (7 days)
				</h5>
				<div class="ch-activity-list">
					${(data.recent_activity || []).slice(0, 10).map(a => activity_item(a)).join('')}
					${(!data.recent_activity || !data.recent_activity.length) ? '<p class="text-muted text-center">No recent activity</p>' : ''}
				</div>
			</div>
		</div>

		<!-- Category Summary -->
		<div class="ch-section">
			<h5 class="ch-section-title">
				<svg class="ch-icon"><use href="#icon-grid"></use></svg>
				Category Summary
			</h5>
			<div class="ch-cat-cards">
				${(data.category_summary || []).map(c => category_card(c)).join('')}
			</div>
		</div>
	</div>`;

	page.main.html(html);
}

// ── Component Renderers ─────────────────────────────────────────────────────

function kpi_card(icon, value, label, link, color) {
	return `<a href="${link}" class="ch-kpi-card ch-kpi-${color}">
		<div class="ch-kpi-icon">
			<svg class="ch-icon"><use href="#icon-${icon}"></use></svg>
		</div>
		<div class="ch-kpi-body">
			<div class="ch-kpi-value">${format_number(value)}</div>
			<div class="ch-kpi-label">${label}</div>
		</div>
	</a>`;
}

function alert_card(alert) {
	let color_map = { danger: '#e53e3e', warning: '#dd6b20', info: '#3182ce' };
	let bg_map = { danger: '#fff5f5', warning: '#fffaf0', info: '#ebf8ff' };
	return `<a href="${alert.action}" class="ch-alert-card" style="border-left: 4px solid ${color_map[alert.type]};background:${bg_map[alert.type]}">
		<div class="ch-alert-icon" style="color:${color_map[alert.type]}">
			<svg class="ch-icon"><use href="#icon-${alert.icon}"></use></svg>
		</div>
		<div class="ch-alert-text">${alert.title}</div>
		<svg class="ch-icon ch-alert-arrow"><use href="#icon-right"></use></svg>
	</a>`;
}

function insight_card(insight) {
	let sev_colors = {
		high: { border: '#e53e3e', bg: '#fff5f5', badge: '#e53e3e' },
		medium: { border: '#dd6b20', bg: '#fffaf0', badge: '#dd6b20' },
		low: { border: '#38a169', bg: '#f0fff4', badge: '#38a169' },
		info: { border: '#3182ce', bg: '#ebf8ff', badge: '#3182ce' },
	};
	let c = sev_colors[insight.severity] || sev_colors.info;
	let type_label = insight.type === 'analysis' ? 'Analysis' : 'Recommendation';

	return `<div class="ch-insight-card" style="border-left:4px solid ${c.border};background:${c.bg}">
		<div class="ch-insight-header">
			<span class="ch-insight-badge" style="background:${c.badge}">${type_label}</span>
			<svg class="ch-icon"><use href="#icon-${insight.icon}"></use></svg>
		</div>
		<div class="ch-insight-title">${insight.title}</div>
		<div class="ch-insight-desc">${insight.description}</div>
		${insight.action ? `<a href="${insight.action}" class="ch-insight-link">View Details →</a>` : ''}
	</div>`;
}

function coverage_row(c) {
	let total = Math.max(c.models || 1, 1);
	let tpl_pct = Math.round((c.templates / total) * 100);
	let var_pct = Math.round((c.variants / Math.max(total * 5, 1)) * 100); // rough estimate
	let price_pct = c.variants > 0 ? Math.round((c.priced_variants / c.variants) * 100) : 0;

	return `<tr>
		<td><strong>${c.category}</strong></td>
		<td class="text-right">${c.models}</td>
		<td class="text-right">${c.templates}</td>
		<td class="text-right">${c.variants}</td>
		<td class="text-right">${c.priced_variants}</td>
		<td>
			<div class="ch-pipeline">
				<div class="ch-pipeline-bar ch-pipe-tpl" style="width:${tpl_pct}%" title="Templates: ${tpl_pct}%"></div>
				<div class="ch-pipeline-bar ch-pipe-var" style="width:${Math.min(var_pct, 100)}%" title="Variants: ${var_pct}%"></div>
				<div class="ch-pipeline-bar ch-pipe-price" style="width:${price_pct}%" title="Priced: ${price_pct}%"></div>
			</div>
		</td>
	</tr>`;
}

function pricing_health_widget(health) {
	let prices = health.prices || {};
	let offers = health.offers || {};
	let total_p = health.total_prices || 1;
	let total_o = health.total_offers || 1;

	let p_statuses = ['Active', 'Draft', 'Scheduled', 'Expired'];
	let o_statuses = ['Active', 'Draft', 'Scheduled', 'Expired', 'Cancelled'];
	let colors = { Active: '#38a169', Draft: '#a0aec0', Scheduled: '#3182ce', Expired: '#e53e3e', Cancelled: '#718096' };

	let price_bars = p_statuses.map(s =>
		`<div class="ch-health-segment" style="width:${((prices[s]||0)/total_p*100).toFixed(1)}%;background:${colors[s]}"
			title="${s}: ${prices[s]||0}"></div>`
	).join('');

	let offer_bars = o_statuses.map(s =>
		`<div class="ch-health-segment" style="width:${((offers[s]||0)/total_o*100).toFixed(1)}%;background:${colors[s]}"
			title="${s}: ${offers[s]||0}"></div>`
	).join('');

	let legend = p_statuses.map(s =>
		`<span class="ch-health-legend-item">
			<span class="ch-legend-dot" style="background:${colors[s]}"></span>${s}: ${prices[s]||0}
		</span>`
	).join('');

	return `
		<div class="ch-health-block">
			<div class="ch-health-label">Prices (${health.total_prices})</div>
			<div class="ch-health-bar">${price_bars}</div>
		</div>
		<div class="ch-health-block">
			<div class="ch-health-label">Offers (${health.total_offers})</div>
			<div class="ch-health-bar">${offer_bars}</div>
		</div>
		<div class="ch-health-legend">${legend}</div>`;
}

function activity_item(a) {
	let type_icons = { price: 'tag', offer: 'gift', model: 'box', item: 'list' };
	let type_colors = { price: '#38a169', offer: '#dd6b20', model: '#3182ce', item: '#805ad5' };
	let time_ago = frappe.datetime.prettyDate(a.timestamp);

	return `<a href="${a.link}" class="ch-activity-item">
		<div class="ch-activity-icon" style="background:${type_colors[a.type]}20;color:${type_colors[a.type]}">
			<svg class="ch-icon"><use href="#icon-${type_icons[a.type]}"></use></svg>
		</div>
		<div class="ch-activity-body">
			<div class="ch-activity-desc">${a.description}</div>
			<div class="ch-activity-meta">${time_ago} · ${frappe.user.full_name(a.user)}</div>
		</div>
		${a.amount ? `<div class="ch-activity-amount">${format_currency(a.amount)}</div>` : ''}
	</a>`;
}

function category_card(c) {
	return `<a href="/app/ch-category/${c.category}" class="ch-cat-card ${!c.cat_active ? 'ch-cat-inactive' : ''}">
		<div class="ch-cat-name">${c.category_name || c.category}</div>
		<div class="ch-cat-stats">
			<span title="Sub Categories">${c.sub_categories} SC</span>
			<span title="Models">${c.models} Models</span>
			<span title="Items">${c.items} Items</span>
		</div>
		${!c.cat_active ? '<span class="ch-cat-badge-inactive">Inactive</span>' : ''}
	</a>`;
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function format_number(n) {
	if (n === null || n === undefined) return '0';
	return Number(n).toLocaleString();
}

function format_currency(n) {
	if (!n) return '₹0';
	return '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

// ── CSS ─────────────────────────────────────────────────────────────────────

function get_dashboard_css() {
	return `
	.ch-dash { max-width: 1400px; margin: 0 auto; padding: 0 15px; }
	.ch-section { margin-bottom: 24px; background: var(--card-bg); border-radius: 12px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
	.ch-section-title { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; font-size: 15px; font-weight: 600; color: var(--text-color); }
	.ch-icon { width: 16px; height: 16px; }

	/* KPI Grid */
	.ch-kpi-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(155px, 1fr)); gap: 12px; }
	.ch-kpi-card { display: flex; align-items: center; gap: 12px; padding: 16px; border-radius: 10px; text-decoration: none !important; transition: transform 0.15s, box-shadow 0.15s; background: var(--card-bg); border: 1px solid var(--border-color); }
	.ch-kpi-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.08); }
	.ch-kpi-icon { width: 40px; height: 40px; border-radius: 10px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
	.ch-kpi-icon .ch-icon { width: 20px; height: 20px; }
	.ch-kpi-value { font-size: 22px; font-weight: 700; line-height: 1.1; }
	.ch-kpi-label { font-size: 12px; color: var(--text-muted); margin-top: 2px; }

	.ch-kpi-purple .ch-kpi-icon { background: #f3e8ff; color: #7c3aed; }
	.ch-kpi-purple .ch-kpi-value { color: #7c3aed; }
	.ch-kpi-blue .ch-kpi-icon { background: #dbeafe; color: #2563eb; }
	.ch-kpi-blue .ch-kpi-value { color: #2563eb; }
	.ch-kpi-green .ch-kpi-icon { background: #dcfce7; color: #16a34a; }
	.ch-kpi-green .ch-kpi-value { color: #16a34a; }
	.ch-kpi-orange .ch-kpi-icon { background: #ffedd5; color: #ea580c; }
	.ch-kpi-orange .ch-kpi-value { color: #ea580c; }
	.ch-kpi-cyan .ch-kpi-icon { background: #cffafe; color: #0891b2; }
	.ch-kpi-cyan .ch-kpi-value { color: #0891b2; }
	.ch-kpi-teal .ch-kpi-icon { background: #ccfbf1; color: #0d9488; }
	.ch-kpi-teal .ch-kpi-value { color: #0d9488; }
	.ch-kpi-gray .ch-kpi-icon { background: #f3f4f6; color: #6b7280; }
	.ch-kpi-gray .ch-kpi-value { color: #6b7280; }
	.ch-kpi-indigo .ch-kpi-icon { background: #e0e7ff; color: #4f46e5; }
	.ch-kpi-indigo .ch-kpi-value { color: #4f46e5; }

	/* Alerts */
	.ch-alerts-grid { display: flex; flex-direction: column; gap: 8px; }
	.ch-alert-card { display: flex; align-items: center; gap: 12px; padding: 12px 16px; border-radius: 8px; text-decoration: none !important; color: var(--text-color) !important; transition: background 0.15s; }
	.ch-alert-card:hover { filter: brightness(0.97); }
	.ch-alert-icon .ch-icon { width: 18px; height: 18px; }
	.ch-alert-text { flex: 1; font-size: 13px; font-weight: 500; }
	.ch-alert-arrow { width: 14px; height: 14px; color: var(--text-muted); }

	/* Insights */
	.ch-insights-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
	.ch-insight-card { padding: 16px; border-radius: 8px; }
	.ch-insight-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
	.ch-insight-badge { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; padding: 3px 8px; border-radius: 4px; color: white; }
	.ch-insight-title { font-size: 14px; font-weight: 600; margin-bottom: 6px; }
	.ch-insight-desc { font-size: 12px; color: var(--text-muted); line-height: 1.5; }
	.ch-insight-link { font-size: 12px; font-weight: 500; display: inline-block; margin-top: 8px; }

	/* Two Column */
	.ch-two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
	@media (max-width: 768px) { .ch-two-col { grid-template-columns: 1fr; } }
	.ch-col { margin-bottom: 0; }

	/* Coverage Pipeline */
	.ch-pipeline { display: flex; height: 20px; border-radius: 4px; overflow: hidden; background: var(--subtle-fg); }
	.ch-pipeline-bar { height: 100%; min-width: 2px; }
	.ch-pipe-tpl { background: #3182ce; }
	.ch-pipe-var { background: #38a169; }
	.ch-pipe-price { background: #805ad5; }

	/* Pricing Health */
	.ch-health-block { margin-bottom: 16px; }
	.ch-health-label { font-size: 12px; font-weight: 600; margin-bottom: 6px; color: var(--text-color); }
	.ch-health-bar { display: flex; height: 24px; border-radius: 6px; overflow: hidden; background: var(--subtle-fg); }
	.ch-health-segment { height: 100%; min-width: 2px; transition: width 0.3s; cursor: pointer; }
	.ch-health-legend { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 8px; }
	.ch-health-legend-item { font-size: 11px; display: flex; align-items: center; gap: 4px; color: var(--text-muted); }
	.ch-legend-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }

	/* Activity */
	.ch-activity-list { max-height: 400px; overflow-y: auto; }
	.ch-activity-item { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border-color); text-decoration: none !important; color: var(--text-color) !important; }
	.ch-activity-item:last-child { border-bottom: none; }
	.ch-activity-item:hover { background: var(--subtle-fg); margin: 0 -8px; padding-left: 8px; padding-right: 8px; border-radius: 6px; }
	.ch-activity-icon { width: 32px; height: 32px; border-radius: 8px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
	.ch-activity-icon .ch-icon { width: 14px; height: 14px; }
	.ch-activity-body { flex: 1; min-width: 0; }
	.ch-activity-desc { font-size: 12px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.ch-activity-meta { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
	.ch-activity-amount { font-size: 13px; font-weight: 600; color: var(--text-color); white-space: nowrap; }

	/* Category Cards */
	.ch-cat-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; }
	.ch-cat-card { padding: 14px; border-radius: 8px; border: 1px solid var(--border-color); text-decoration: none !important; color: var(--text-color) !important; transition: transform 0.15s, box-shadow 0.15s; position: relative; }
	.ch-cat-card:hover { transform: translateY(-1px); box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
	.ch-cat-name { font-size: 14px; font-weight: 600; margin-bottom: 8px; }
	.ch-cat-stats { display: flex; gap: 12px; font-size: 11px; color: var(--text-muted); }
	.ch-cat-stats span { white-space: nowrap; }
	.ch-cat-inactive { opacity: 0.6; }
	.ch-cat-badge-inactive { position: absolute; top: 8px; right: 8px; font-size: 9px; background: #e53e3e; color: white; padding: 2px 6px; border-radius: 3px; text-transform: uppercase; }

	/* Table tweaks */
	.ch-section .table { margin-bottom: 0; }
	.ch-section .table th { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); border-top: none; font-weight: 600; }
	.ch-section .table td { font-size: 12px; vertical-align: middle; }
	`;
}
