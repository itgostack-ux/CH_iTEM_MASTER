let _ceo_cc_loading = false;
let _ceo_cc_timer = null;

frappe.pages['ceo-command-center'].on_page_load = function (wrapper) {
	let page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'CEO Command Center',
		single_column: true,
	});

	page._ready = false;

	// --- Filter bar HTML ---
	let filter_html = `
	<div class="cc-toolbar">
		<div class="cc-toolbar-row">
			<div class="cc-toolbar-filters">
				<div class="cc-ctrl" id="cc-company-wrap"></div>
				<div class="cc-ctrl" id="cc-store-wrap"></div>
			</div>
			<div class="cc-toolbar-period">
				<div class="cc-period-pills">
					<button class="cc-pill active" data-period="this_month">This Month</button>
					<button class="cc-pill" data-period="last_month">Last Month</button>
					<button class="cc-pill" data-period="last_quarter">Last Qtr</button>
					<button class="cc-pill" data-period="1_year">1 Year</button>
					<button class="cc-pill" data-period="custom">Custom</button>
				</div>
			</div>
		</div>
		<div class="cc-toolbar-custom" id="cc-custom-row" style="display:none">
			<div class="cc-ctrl cc-ctrl-date" id="cc-from-wrap"></div>
			<span class="cc-date-sep">—</span>
			<div class="cc-ctrl cc-ctrl-date" id="cc-to-wrap"></div>
			<button class="btn btn-primary btn-sm cc-go-btn" id="cc-go-btn">Go</button>
		</div>
	</div>`;
	page.main.html(filter_html + '<div class="ceo-cc-container"></div>');

	// --- Company (cascades into Store) ---
	page.company_field = frappe.ui.form.make_control({
		df: { fieldname: 'company', label: 'Company', fieldtype: 'Link', options: 'Company',
			placeholder: 'All Companies', only_select: true },
		parent: page.main.find('#cc-company-wrap'),
		render_input: true,
	});
	page.company_field.refresh();
	page.company_field.$input.on('change', () => {
		// Cascade: clear store, reload store options
		page.store_field.set_value('');
		page.store_field.refresh();
		if (page._ready) load_data(page);
	});

	// --- Store (filtered by company) ---
	page.store_field = frappe.ui.form.make_control({
		df: { fieldname: 'store', label: 'Store', fieldtype: 'Link', options: 'POS Profile',
			placeholder: 'All Stores', only_select: true,
			get_query: function () {
				let company = page.company_field.get_value();
				let filters = {};
				if (company) filters.company = company;
				return { filters: filters };
			}
		},
		parent: page.main.find('#cc-store-wrap'),
		render_input: true,
	});
	page.store_field.refresh();
	page.store_field.$input.on('change', () => {
		if (page._ready) load_data(page);
	});

	// --- Date pickers (for Custom) ---
	page.from_date_field = frappe.ui.form.make_control({
		df: { fieldname: 'from_date', label: 'From Date', fieldtype: 'Date' },
		parent: page.main.find('#cc-from-wrap'),
		render_input: true,
	});
	page.from_date_field.refresh();

	page.to_date_field = frappe.ui.form.make_control({
		df: { fieldname: 'to_date', label: 'To Date', fieldtype: 'Date' },
		parent: page.main.find('#cc-to-wrap'),
		render_input: true,
	});
	page.to_date_field.refresh();

	// --- Go button (for custom dates) ---
	page.main.find('#cc-go-btn').on('click', () => {
		if (page._ready) load_data(page);
	});

	// --- Period pill handlers ---
	page._period = 'this_month';
	page.main.find('.cc-pill').on('click', function () {
		page.main.find('.cc-pill').removeClass('active');
		$(this).addClass('active');
		page._period = $(this).data('period');

		if (page._period === 'custom') {
			page.main.find('#cc-custom-row').slideDown(150);
		} else {
			page.main.find('#cc-custom-row').slideUp(150);
			if (page._ready) load_data(page);
		}
	});

	set_styles();
	load_data(page).then(() => { page._ready = true; });
};

function load_data(page) {
	if (_ceo_cc_loading) return Promise.resolve();
	_ceo_cc_loading = true;

	let container = page.main.find('.ceo-cc-container');
	container.css('opacity', '0.5').css('pointer-events', 'none');
	// Show spinner only if container is empty
	if (!container.children().length || container.find('.spinner-border').length) {
		container.html('<div class="text-center p-5"><div class="spinner-border text-primary" role="status"></div></div>');
	}

	let period = page._period || 'this_month';
	if (period === 'custom') {
		let fd = page.from_date_field.get_value();
		let td = page.to_date_field.get_value();
		if (fd && td) {
			period = fd + ':' + td;
		} else {
			_ceo_cc_loading = false;
			container.css('opacity', '1').css('pointer-events', 'auto');
			frappe.show_alert({ message: 'Please select both From and To dates', indicator: 'orange' });
			return Promise.resolve();
		}
	}

	return frappe.call({
		method: 'ch_item_master.ch_core.page.ceo_command_center.ceo_command_center.get_command_center_data',
		args: {
			company: page.company_field.get_value() || null,
			store: page.store_field.get_value() || null,
			period: period,
		},
		freeze: false,
		callback: function (r) {
			_ceo_cc_loading = false;
			container.css('opacity', '1').css('pointer-events', 'auto');
			if (r.message) {
				try {
					render_dashboard(container, r.message);
				} catch (e) {
					console.error('CEO CC render error:', e);
					container.html(`<div class="text-center p-5 text-muted">
						<p>Dashboard rendering failed</p>
						<pre class="small text-left text-danger">${frappe.utils.escape_html(e.message || e)}</pre>
					</div>`);
				}
			} else {
				container.html('<div class="text-center p-5 text-muted">No data returned</div>');
			}
		},
		error: function (r) {
			_ceo_cc_loading = false;
			container.css('opacity', '1').css('pointer-events', 'auto');
			console.error('CEO CC API error:', r);
			container.html(`<div class="text-center p-5 text-muted">
				<p>Failed to load data</p>
				<button class="btn btn-sm btn-default" onclick="load_data(cur_page.page)">Retry</button>
			</div>`);
		},
	});
}

function render_dashboard(container, data) {
	let html = '';

	// --- Section 0: AI Insights (most prominent) ---
	html += render_ai_insights(data.ai_insights || []);

	// --- Section 1: KPI Cards with trend arrows ---
	html += render_kpi_cards(data.summary || {}, data.prev_summary || {});

	// --- Section 2: Hourly Trend ---
	html += '<div class="cc-section"><h5>Hourly Revenue Trend</h5><canvas id="cc-hourly-chart" height="80"></canvas></div>';

	// --- Section 3: Conversion Funnel ---
	html += '<div class="cc-section"><h5>Conversion Funnel</h5><canvas id="cc-conversion-chart" height="80"></canvas></div>';

	// --- Section 4: Store Rankings ---
	html += render_store_rankings(data.stores || {});

	// --- Section 5: Attach Rates ---
	html += render_attach_rates(data.attach || {});

	// --- Section 6: Leakage + Repairs ---
	html += '<div class="row">';
	html += render_leakage(data.leakage || {});
	html += render_repairs(data.repairs || {});
	html += '</div>';

	// --- Section 7: Inventory ---
	html += render_inventory(data.inventory || {});

	// --- Section 8: Warranty Claims ---
	html += render_warranty_claims(data.warranty_claims || {});

	// --- Section 9: Buyback ---
	html += render_buyback(data.buyback || {});

	// --- Section 10: Alerts ---
	html += render_alerts(data.alerts || []);

	container.html(html);

	// Bind AI insight row expand/collapse
	container.find('.cc-ai-row-main').on('click', function () {
		let row = $(this).closest('.cc-ai-row');
		row.toggleClass('expanded');
	});

	// Render charts after DOM update
	setTimeout(() => {
		render_hourly_chart(data.hourly_trend || [], data.prev_hourly_trend || []);
		render_conversion_chart(data.conversion || []);
	}, 100);
}

function render_kpi_cards(s, prev) {
	prev = prev || {};

	function trend_html(current, previous) {
		if (!previous || previous === 0) return '';
		let pct = ((current - previous) / previous * 100).toFixed(1);
		if (pct == 0) return '';
		let arrow = pct > 0 ? '▲' : '▼';
		let cls = pct > 0 ? 'cc-trend-up' : 'cc-trend-down';
		return `<span class="${cls}">${arrow} ${Math.abs(pct)}%</span>`;
	}

	let cards = [
		{ label: 'Revenue', value: fmt_currency(s.revenue), color: '#2490ef', trend: trend_html(s.revenue, prev.revenue) },
		{ label: 'Invoices', value: s.invoice_count || 0, color: '#29cd42', trend: trend_html(s.invoice_count, prev.invoice_count) },
		{ label: 'Avg Bill Value', value: fmt_currency(s.avg_bill_value), color: '#7c5ecf', trend: trend_html(s.avg_bill_value, prev.avg_bill_value) },
		{ label: 'Footfall', value: s.footfall || 0, color: '#ed6e3a', trend: trend_html(s.footfall, prev.footfall) },
		{ label: 'Conversion %', value: (s.conversion_pct || 0) + '%', color: s.conversion_pct >= 40 ? '#29cd42' : '#e24c4c', trend: trend_html(s.conversion_pct, prev.conversion_pct) },
	];

	let html = '<div class="cc-kpi-row">';
	cards.forEach(c => {
		html += `<div class="cc-kpi-card" style="border-top: 3px solid ${c.color}">
			<div class="cc-kpi-value">${c.value} ${c.trend || ''}</div>
			<div class="cc-kpi-label">${c.label}</div>
		</div>`;
	});
	html += '</div>';
	return html;
}

function render_store_rankings(stores) {
	if (!stores.top_5 || !stores.top_5.length) {
		return '<div class="cc-section"><h5>Store Rankings</h5><p class="text-muted">No data</p></div>';
	}

	let html = '<div class="cc-section"><h5>Store Rankings</h5><div class="row"><div class="col-md-6">';
	html += '<h6 class="text-success">Top 5</h6><table class="table table-sm"><thead><tr><th>Store</th><th class="text-right">Revenue</th><th class="text-right">Bills</th></tr></thead><tbody>';
	(stores.top_5 || []).forEach(s => {
		html += `<tr><td>${s.store}</td><td class="text-right">${fmt_currency(s.revenue)}</td><td class="text-right">${s.invoices}</td></tr>`;
	});
	html += '</tbody></table></div>';

	html += '<div class="col-md-6"><h6 class="text-danger">Bottom 5</h6><table class="table table-sm"><thead><tr><th>Store</th><th class="text-right">Revenue</th><th class="text-right">Bills</th></tr></thead><tbody>';
	(stores.bottom_5 || []).forEach(s => {
		html += `<tr><td>${s.store}</td><td class="text-right">${fmt_currency(s.revenue)}</td><td class="text-right">${s.invoices}</td></tr>`;
	});
	html += '</tbody></table></div></div></div>';
	return html;
}

function render_attach_rates(a) {
	let html = '<div class="cc-section"><h5>Attach Rates</h5><div class="cc-kpi-row">';
	let items = [
		{ label: 'Warranty', rate: a.warranty_rate || 0, color: '#2490ef' },
		{ label: 'Accessory', rate: a.accessory_rate || 0, color: '#29cd42' },
		{ label: 'VAS', rate: a.vas_rate || 0, color: '#7c5ecf' },
	];
	items.forEach(i => {
		html += `<div class="cc-kpi-card" style="border-top: 3px solid ${i.color}">
			<div class="cc-kpi-value">${i.rate}%</div>
			<div class="cc-kpi-label">${i.label} Attach</div>
		</div>`;
	});
	html += '</div></div>';
	return html;
}

function render_leakage(l) {
	return `<div class="col-md-6"><div class="cc-section"><h5>Leakage</h5>
		<div class="cc-metric"><span class="cc-metric-label">Discount Override %</span>
		<span class="cc-metric-value ${l.discount_override_pct > 8 ? 'text-danger' : ''}">${l.discount_override_pct || 0}%</span></div>
	</div></div>`;
}

function render_repairs(r) {
	return `<div class="col-md-6"><div class="cc-section"><h5>Repairs & Service</h5>
		<div class="cc-metric"><span class="cc-metric-label">Open Service Requests</span>
		<span class="cc-metric-value">${r.open_service_requests || 0}</span></div>
		<div class="cc-metric"><span class="cc-metric-label">Avg TAT (hrs)</span>
		<span class="cc-metric-value ${r.avg_tat_hours > 48 ? 'text-danger' : ''}">${r.avg_tat_hours || 0}</span></div>
		<div class="cc-metric"><span class="cc-metric-label">SLA Breached</span>
		<span class="cc-metric-value ${r.sla_breached > 0 ? 'text-danger' : ''}">${r.sla_breached || 0}</span></div>
		<div class="cc-metric"><span class="cc-metric-label">QC Passed / Failed / Not Repairable</span>
		<span class="cc-metric-value">${r.qc_passed || 0} / <span class="${r.qc_failed > 0 ? 'text-danger' : ''}">${r.qc_failed || 0}</span> / ${r.qc_not_repairable || 0}</span></div>
	</div></div>`;
}

function render_alerts(alerts) {
	if (!alerts.length) {
		return '<div class="cc-section"><h5>Alerts</h5><p class="text-muted">No active alerts</p></div>';
	}
	let html = '<div class="cc-section"><h5>Alerts</h5><div class="cc-alerts">';
	alerts.forEach(a => {
		let cls = a.severity === 'Critical' ? 'alert-danger' : (a.severity === 'Warning' ? 'alert-warning' : 'alert-info');
		html += `<div class="alert ${cls} cc-alert-item"><strong>${frappe.utils.escape_html(a.alert_type)}</strong>
			${a.store ? ' — ' + frappe.utils.escape_html(a.store) : ''}: ${frappe.utils.escape_html(a.message)}
			<span class="text-muted small">${frappe.datetime.prettyDate(a.creation)}</span></div>`;
	});
	html += '</div></div>';
	return html;
}

function render_ai_insights(insights) {
	if (!insights || !insights.length) {
		return '';
	}

	let severity_colors = {
		'critical': '#e24c4c',
		'warning': '#ed6e3a',
		'opportunity': '#29cd42',
		'info': '#2490ef'
	};
	let severity_bg = {
		'critical': 'rgba(226,76,76,0.08)',
		'warning': 'rgba(237,110,58,0.08)',
		'opportunity': 'rgba(41,205,66,0.08)',
		'info': 'rgba(36,144,239,0.08)'
	};
	let category_icons = {
		'revenue': '💰', 'conversion': '🔄', 'inventory': '📦',
		'service': '🔧', 'warranty': '🛡️', 'buyback': '♻️',
		'leakage': '⚠️', 'staffing': '👥'
	};

	let critical_count = insights.filter(i => i.severity === 'critical').length;
	let warning_count = insights.filter(i => i.severity === 'warning').length;
	let opp_count = insights.filter(i => i.severity === 'opportunity').length;
	let info_count = insights.filter(i => i.severity === 'info').length;

	// Determine overall health
	let health_color = critical_count > 0 ? '#e24c4c' : (warning_count > 0 ? '#ed6e3a' : '#29cd42');
	let health_label = critical_count > 0 ? 'Needs Attention' : (warning_count > 0 ? 'Monitor' : 'Healthy');

	let html = `<div class="cc-ai-panel">`;

	// Compact header bar with health indicator and severity summary inline
	html += `<div class="cc-ai-topbar">
		<div class="cc-ai-topbar-left">
			<span class="cc-ai-health-dot" style="background:${health_color}"></span>
			<span class="cc-ai-topbar-title">AI Insights</span>
			<span class="cc-ai-health-label" style="color:${health_color}">${health_label}</span>
		</div>
		<div class="cc-ai-topbar-right">`;
	if (critical_count) html += `<span class="cc-ai-count" style="background:#e24c4c">${critical_count}</span>`;
	if (warning_count) html += `<span class="cc-ai-count" style="background:#ed6e3a">${warning_count}</span>`;
	if (opp_count) html += `<span class="cc-ai-count" style="background:#29cd42">${opp_count}</span>`;
	if (info_count) html += `<span class="cc-ai-count" style="background:#2490ef">${info_count}</span>`;
	html += `</div></div>`;

	// Compact insight rows — each is a single-line strip, expandable on click
	html += `<div class="cc-ai-rows">`;
	insights.forEach((item, idx) => {
		let color = severity_colors[item.severity] || '#2490ef';
		let bg = severity_bg[item.severity] || 'rgba(36,144,239,0.08)';
		let cat_icon = category_icons[item.category] || '📊';
		let metric = frappe.utils.escape_html(item.metric_value || '');
		let title = frappe.utils.escape_html(item.title || '');
		let detail = frappe.utils.escape_html(item.detail || '');
		let action = frappe.utils.escape_html(item.action || '');

		html += `<div class="cc-ai-row" data-idx="${idx}" style="border-left: 3px solid ${color}; background: ${bg}">
			<div class="cc-ai-row-main">
				<span class="cc-ai-row-icon">${cat_icon}</span>
				<span class="cc-ai-row-title">${title}</span>
				<span class="cc-ai-row-metric" style="color:${color}">${metric}</span>
				<span class="cc-ai-row-chevron">›</span>
			</div>
			<div class="cc-ai-row-detail" id="cc-ai-detail-${idx}">
				<div class="cc-ai-row-detail-text">${detail}</div>
				${action ? `<div class="cc-ai-row-action"><strong>Action:</strong> ${action}</div>` : ''}
			</div>
		</div>`;
	});
	html += `</div></div>`;

	return html;
}

function render_inventory(inv) {
	let html = '<div class="cc-section"><h5>Inventory Overview</h5><div class="cc-kpi-row">';
	let cards = [
		{ label: 'Stock Value', value: fmt_currency(inv.total_stock_value), color: '#2490ef' },
		{ label: 'Stock Qty', value: Math.round(inv.total_stock_qty || 0).toLocaleString('en-IN'), color: '#29cd42' },
		{ label: 'Dead Stock Items', value: inv.dead_stock_items || 0, color: inv.dead_stock_items > 0 ? '#e24c4c' : '#7c5ecf' },
		{ label: 'Slow Moving', value: inv.slow_moving_items || 0, color: '#ed6e3a' },
		{ label: 'In Transit', value: inv.in_transit_count || 0, color: '#36b37e' },
	];
	cards.forEach(c => {
		html += `<div class="cc-kpi-card" style="border-top: 3px solid ${c.color}">
			<div class="cc-kpi-value">${c.value}</div>
			<div class="cc-kpi-label">${c.label}</div>
		</div>`;
	});
	html += '</div></div>';
	return html;
}

function render_warranty_claims(wc) {
	let html = '<div class="cc-section"><h5>Warranty Claims</h5>';

	// KPI row
	let total = wc.total_claims || 0;
	let splits = wc.cost_splits || {};
	let total_cost = (splits.gogizmo || 0) + (splits.gofix || 0) + (splits.customer || 0);
	html += '<div class="cc-kpi-row">';
	html += `<div class="cc-kpi-card" style="border-top: 3px solid #2490ef">
		<div class="cc-kpi-value">${total}</div><div class="cc-kpi-label">Total Claims</div></div>`;
	html += `<div class="cc-kpi-card" style="border-top: 3px solid #e24c4c">
		<div class="cc-kpi-value">${fmt_currency(splits.gogizmo)}</div><div class="cc-kpi-label">GoGizmo Pays</div></div>`;
	html += `<div class="cc-kpi-card" style="border-top: 3px solid #ed6e3a">
		<div class="cc-kpi-value">${fmt_currency(splits.gofix)}</div><div class="cc-kpi-label">GoFix Pays</div></div>`;
	html += `<div class="cc-kpi-card" style="border-top: 3px solid #29cd42">
		<div class="cc-kpi-value">${fmt_currency(splits.customer)}</div><div class="cc-kpi-label">Customer Pays</div></div>`;
	html += `<div class="cc-kpi-card" style="border-top: 3px solid #7c5ecf">
		<div class="cc-kpi-value">${wc.plan_utilization_pct || 0}%</div><div class="cc-kpi-label">Plan Utilization</div></div>`;
	html += '</div>';

	// Status breakdown table
	if (wc.by_status && wc.by_status.length) {
		html += '<div class="row"><div class="col-md-6"><h6>By Status</h6><table class="table table-sm"><tbody>';
		wc.by_status.slice(0, 8).forEach(s => {
			html += `<tr><td>${frappe.utils.escape_html(s.claim_status)}</td><td class="text-right font-weight-bold">${s.cnt}</td></tr>`;
		});
		html += '</tbody></table></div>';

		// Coverage type breakdown
		html += '<div class="col-md-6"><h6>By Coverage Type</h6><table class="table table-sm"><tbody>';
		(wc.by_coverage || []).forEach(c => {
			let label = (c.coverage_type || '').replace(/_/g, ' ');
			label = label.charAt(0).toUpperCase() + label.slice(1);
			html += `<tr><td>${frappe.utils.escape_html(label)}</td><td class="text-right font-weight-bold">${c.cnt}</td></tr>`;
		});
		if (!wc.by_coverage || !wc.by_coverage.length) {
			html += '<tr><td class="text-muted">No data</td></tr>';
		}
		html += '</tbody></table></div></div>';
	}

	html += '</div>';
	return html;
}

function render_buyback(bb) {
	let html = '<div class="cc-section"><h5>Buyback</h5>';

	html += '<div class="cc-kpi-row">';
	html += `<div class="cc-kpi-card" style="border-top: 3px solid #2490ef">
		<div class="cc-kpi-value">${bb.total_orders || 0}</div><div class="cc-kpi-label">Total Orders</div></div>`;
	html += `<div class="cc-kpi-card" style="border-top: 3px solid #29cd42">
		<div class="cc-kpi-value">${fmt_currency(bb.total_value)}</div><div class="cc-kpi-label">Total Value</div></div>`;
	html += `<div class="cc-kpi-card" style="border-top: 3px solid #7c5ecf">
		<div class="cc-kpi-value">${fmt_currency(bb.avg_order_value)}</div><div class="cc-kpi-label">Avg Order Value</div></div>`;
	html += '</div>';

	if ((bb.by_status && bb.by_status.length) || (bb.by_settlement_type && bb.by_settlement_type.length)) {
		html += '<div class="row">';

		// By status
		html += '<div class="col-md-6"><h6>By Status</h6><table class="table table-sm"><tbody>';
		(bb.by_status || []).forEach(s => {
			html += `<tr><td>${frappe.utils.escape_html(s.status)}</td><td class="text-right">${s.cnt}</td><td class="text-right">${fmt_currency(s.value)}</td></tr>`;
		});
		html += '</tbody></table></div>';

		// By settlement type
		html += '<div class="col-md-6"><h6>By Type</h6><table class="table table-sm"><tbody>';
		(bb.by_settlement_type || []).forEach(s => {
			html += `<tr><td>${frappe.utils.escape_html(s.settlement_type)}</td><td class="text-right">${s.cnt}</td><td class="text-right">${fmt_currency(s.value)}</td></tr>`;
		});
		html += '</tbody></table></div>';

		html += '</div>';
	}

	html += '</div>';
	return html;
}

function render_hourly_chart(data, prev_data) {
	let canvas = document.getElementById('cc-hourly-chart');
	if (!canvas || !data.length) return;

	let labels = data.map(d => d.hour + ':00');
	let values = data.map(d => d.revenue);

	let datasets = [{ name: 'Revenue', values: values }];
	let colors = ['#2490ef'];

	// Add previous period comparison line if data available
	if (prev_data && prev_data.length) {
		let prev_map = {};
		prev_data.forEach(d => { prev_map[d.hour] = d.revenue; });
		let prev_values = data.map(d => prev_map[d.hour] || 0);
		datasets.push({ name: 'Previous Period', values: prev_values });
		colors.push('#d1d5db');
	}

	new frappe.Chart(canvas.parentElement, {
		type: 'line',
		height: 200,
		data: { labels: labels, datasets: datasets },
		colors: colors,
		lineOptions: { regionFill: 1 },
	});
	canvas.remove();
}

function render_conversion_chart(data) {
	let canvas = document.getElementById('cc-conversion-chart');
	if (!canvas || !data.length) return;

	let labels = data.map(d => d.hour + ':00');

	new frappe.Chart(canvas.parentElement, {
		type: 'bar',
		height: 200,
		data: {
			labels: labels,
			datasets: [
				{ name: 'Tokens', values: data.map(d => d.tokens) },
				{ name: 'Invoices', values: data.map(d => d.invoices) },
			],
		},
		colors: ['#ed6e3a', '#29cd42'],
	});
	canvas.remove();
}

function fmt_currency(val) {
	val = parseFloat(val) || 0;
	return '₹ ' + val.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

function set_styles() {
	frappe.dom.set_style(`
		/* ===== Toolbar ===== */
		.cc-toolbar { background: var(--card-bg); border-radius: 10px; padding: 14px 20px; margin-bottom: 18px; box-shadow: var(--shadow-sm); }
		.cc-toolbar-row { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
		.cc-toolbar-filters { display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; }
		.cc-toolbar-period { display: flex; align-items: flex-end; }
		.cc-ctrl { min-width: 180px; }
		.cc-ctrl .form-group { margin-bottom: 0; }
		.cc-ctrl .control-label { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); margin-bottom: 3px; }
		.cc-ctrl .input-with-feedback { font-size: 13px; }
		.cc-ctrl-date { min-width: 130px; }
		.cc-period-pills { display: flex; gap: 4px; flex-wrap: wrap; padding-bottom: 2px; }
		.cc-pill { border: 1.5px solid var(--border-color); background: transparent; color: var(--text-muted); font-size: 12px; font-weight: 600; padding: 5px 14px; border-radius: 20px; cursor: pointer; transition: all 0.15s; white-space: nowrap; }
		.cc-pill:hover { border-color: var(--primary); color: var(--primary); background: rgba(36,144,239,0.04); }
		.cc-pill.active { background: var(--primary); color: #fff; border-color: var(--primary); box-shadow: 0 2px 8px rgba(36,144,239,0.25); }
		.cc-toolbar-custom { display: flex; align-items: flex-end; gap: 10px; padding-top: 12px; margin-top: 12px; border-top: 1px dashed var(--border-color); }
		.cc-date-sep { font-size: 14px; color: var(--text-muted); padding-bottom: 6px; }
		.cc-go-btn { padding: 5px 20px !important; font-weight: 600; border-radius: 6px; margin-bottom: 1px; }

		/* ===== KPI + Sections ===== */
		.ceo-cc-container { max-width: 1200px; margin: 0 auto; }
		.cc-kpi-row { display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
		.cc-kpi-card { background: var(--card-bg); border-radius: 8px; padding: 16px 20px; min-width: 150px; flex: 1; box-shadow: var(--shadow-sm); }
		.cc-kpi-value { font-size: 24px; font-weight: 700; }
		.cc-kpi-label { font-size: 12px; color: var(--text-muted); text-transform: uppercase; margin-top: 4px; }
		.cc-section { background: var(--card-bg); border-radius: 8px; padding: 16px 20px; margin-bottom: 16px; box-shadow: var(--shadow-sm); }
		.cc-section h5 { margin-bottom: 12px; font-weight: 600; }
		.cc-metric { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--border-color); }
		.cc-metric:last-child { border-bottom: none; }
		.cc-metric-value { font-weight: 600; font-size: 16px; }
		.cc-alert-item { margin-bottom: 6px; padding: 8px 12px; font-size: 13px; }
		.cc-trend-up { color: #29cd42; font-size: 13px; font-weight: 600; margin-left: 4px; }
		.cc-trend-down { color: #e24c4c; font-size: 13px; font-weight: 600; margin-left: 4px; }

		/* ===== AI Insights Panel ===== */
		.cc-ai-panel { background: var(--card-bg); border-radius: 10px; margin-bottom: 16px; box-shadow: var(--shadow-sm); overflow: hidden; }
		.cc-ai-topbar { display: flex; justify-content: space-between; align-items: center; padding: 10px 16px; background: var(--fg-color); border-bottom: 1px solid var(--border-color); }
		.cc-ai-topbar-left { display: flex; align-items: center; gap: 8px; }
		.cc-ai-health-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; animation: cc-pulse 2s infinite; }
		@keyframes cc-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
		.cc-ai-topbar-title { font-size: 14px; font-weight: 700; color: var(--heading-color); }
		.cc-ai-health-label { font-size: 12px; font-weight: 600; }
		.cc-ai-topbar-right { display: flex; gap: 5px; }
		.cc-ai-count { display: inline-flex; align-items: center; justify-content: center; min-width: 22px; height: 22px; border-radius: 11px; color: #fff; font-size: 11px; font-weight: 700; padding: 0 6px; }
		.cc-ai-rows { padding: 2px 0; }
		.cc-ai-row { border-left: 3px solid transparent; cursor: pointer; transition: background 0.15s; }
		.cc-ai-row:not(:last-child) { border-bottom: 1px solid var(--light-border-color, var(--border-color)); }
		.cc-ai-row:hover { background: rgba(0,0,0,0.015); }
		.cc-ai-row-main { display: flex; align-items: center; padding: 9px 16px; gap: 10px; }
		.cc-ai-row-icon { font-size: 14px; flex-shrink: 0; width: 20px; text-align: center; }
		.cc-ai-row-title { flex: 1; font-size: 13px; font-weight: 600; color: var(--heading-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
		.cc-ai-row-metric { font-size: 13px; font-weight: 700; flex-shrink: 0; }
		.cc-ai-row-chevron { font-size: 16px; color: var(--text-light); transition: transform 0.2s; flex-shrink: 0; width: 16px; text-align: center; }
		.cc-ai-row.expanded .cc-ai-row-chevron { transform: rotate(90deg); }
		.cc-ai-row-detail { display: none; padding: 0 16px 10px 46px; }
		.cc-ai-row.expanded .cc-ai-row-detail { display: block; }
		.cc-ai-row-detail-text { font-size: 12px; color: var(--text-muted); line-height: 1.5; margin-bottom: 6px; }
		.cc-ai-row-action { font-size: 12px; color: var(--text-color); background: rgba(36,144,239,0.06); padding: 6px 10px; border-radius: 6px; line-height: 1.4; }

		/* ===== Mobile ===== */
		@media (max-width: 768px) {
			.cc-toolbar-row { flex-direction: column; align-items: stretch; }
			.cc-toolbar-filters { flex-direction: column; }
			.cc-ctrl { min-width: auto; width: 100%; }
			.cc-period-pills { flex-wrap: wrap; }
			.cc-toolbar-custom { flex-wrap: wrap; }
			.cc-kpi-row { flex-direction: column; gap: 8px; }
			.cc-kpi-card { min-width: auto; padding: 12px 14px; }
			.cc-kpi-value { font-size: 20px; }
			.cc-section { padding: 12px 14px; margin-bottom: 10px; }
			.cc-section .row { flex-direction: column; }
			.cc-section .col-md-6 { width: 100%; max-width: 100%; padding: 0; margin-bottom: 12px; }
			.cc-section .table { font-size: 12px; }
			.ceo-cc-container { padding: 0 4px; }
			.cc-ai-row-title { white-space: normal; }
		}
		@media (max-width: 480px) {
			.cc-kpi-value { font-size: 18px; }
			.cc-kpi-label { font-size: 11px; }
			.cc-section h5 { font-size: 14px; }
			.cc-alert-item { font-size: 12px; padding: 6px 8px; }
		}
	`);
}
