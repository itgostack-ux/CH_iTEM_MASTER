frappe.pages['ceo-command-center'].on_page_load = function (wrapper) {
	let page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'CEO Command Center',
		single_column: true,
	});

	page.set_secondary_action('Refresh', () => load_data(page), 'refresh');

	// Filters
	page.company_field = page.add_field({
		fieldname: 'company',
		label: __('Company'),
		fieldtype: 'Link',
		options: 'Company',
		change: () => load_data(page),
	});

	page.store_field = page.add_field({
		fieldname: 'store',
		label: __('Store'),
		fieldtype: 'Link',
		options: 'POS Profile',
		change: () => load_data(page),
	});

	page.period_field = page.add_field({
		fieldname: 'period',
		label: __('Period'),
		fieldtype: 'Select',
		options: 'today\nwtd\nmtd\nqtd\nytd',
		default: 'today',
		change: () => load_data(page),
	});

	page.main.html('<div class="ceo-cc-container"></div>');
	set_styles();
	load_data(page);
};

function load_data(page) {
	let container = page.main.find('.ceo-cc-container');
	container.html('<div class="text-center p-5"><div class="spinner-border" role="status"></div></div>');

	frappe.call({
		method: 'ch_item_master.ch_core.page.ceo_command_center.ceo_command_center.get_command_center_data',
		args: {
			company: page.company_field.get_value() || null,
			store: page.store_field.get_value() || null,
			period: page.period_field.get_value() || 'today',
		},
		callback: function (r) {
			if (r.message) {
				render_dashboard(container, r.message);
			}
		},
	});
}

function render_dashboard(container, data) {
	let html = '';

	// --- Section 1: KPI Cards ---
	html += render_kpi_cards(data.summary || {});

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

	// --- Section 7: Alerts ---
	html += render_alerts(data.alerts || []);

	container.html(html);

	// Render charts after DOM update
	setTimeout(() => {
		render_hourly_chart(data.hourly_trend || []);
		render_conversion_chart(data.conversion || []);
	}, 100);
}

function render_kpi_cards(s) {
	let cards = [
		{ label: 'Revenue', value: format_currency(s.revenue), color: '#2490ef' },
		{ label: 'Invoices', value: s.invoice_count || 0, color: '#29cd42' },
		{ label: 'Avg Bill Value', value: format_currency(s.avg_bill_value), color: '#7c5ecf' },
		{ label: 'Footfall', value: s.footfall || 0, color: '#ed6e3a' },
		{ label: 'Conversion %', value: (s.conversion_pct || 0) + '%', color: s.conversion_pct >= 40 ? '#29cd42' : '#e24c4c' },
	];

	let html = '<div class="cc-kpi-row">';
	cards.forEach(c => {
		html += `<div class="cc-kpi-card" style="border-top: 3px solid ${c.color}">
			<div class="cc-kpi-value">${c.value}</div>
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
		html += `<tr><td>${s.store}</td><td class="text-right">${format_currency(s.revenue)}</td><td class="text-right">${s.invoices}</td></tr>`;
	});
	html += '</tbody></table></div>';

	html += '<div class="col-md-6"><h6 class="text-danger">Bottom 5</h6><table class="table table-sm"><thead><tr><th>Store</th><th class="text-right">Revenue</th><th class="text-right">Bills</th></tr></thead><tbody>';
	(stores.bottom_5 || []).forEach(s => {
		html += `<tr><td>${s.store}</td><td class="text-right">${format_currency(s.revenue)}</td><td class="text-right">${s.invoices}</td></tr>`;
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
	return `<div class="col-md-6"><div class="cc-section"><h5>Repairs</h5>
		<div class="cc-metric"><span class="cc-metric-label">Open Service Requests</span>
		<span class="cc-metric-value">${r.open_service_requests || 0}</span></div>
		<div class="cc-metric"><span class="cc-metric-label">Avg TAT (hrs)</span>
		<span class="cc-metric-value ${r.avg_tat_hours > 48 ? 'text-danger' : ''}">${r.avg_tat_hours || 0}</span></div>
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

function render_hourly_chart(data) {
	let canvas = document.getElementById('cc-hourly-chart');
	if (!canvas || !data.length) return;

	let labels = data.map(d => d.hour + ':00');
	let values = data.map(d => d.revenue);

	new frappe.Chart(canvas.parentElement, {
		type: 'line',
		height: 200,
		data: {
			labels: labels,
			datasets: [{ name: 'Revenue', values: values }],
		},
		colors: ['#2490ef'],
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

function format_currency(val) {
	return frappe.format(val || 0, { fieldtype: 'Currency' });
}

function set_styles() {
	frappe.dom.set_style(`
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
		.ceo-cc-container { max-width: 1200px; margin: 0 auto; }
	`);
}
