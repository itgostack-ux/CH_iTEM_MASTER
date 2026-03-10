// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.ui.form.on("CH Price Upload Batch", {
	refresh(frm) {
		// ── Clear previous state ──────────────────────────────────────────
		frm.set_intro('');
		if (frm.fields_dict.alerts_html) {
			$(frm.fields_dict.alerts_html.wrapper).html('');
		}

		// ── Status indicator ──────────────────────────────────────────────
		const indicator_map = {
			'Draft':              'orange',
			'Pending Approval':   'blue',
			'Approved':           'green',
			'Applying':           'yellow',
			'Applied':            'green',
			'Partially Applied':  'orange',
			'Rejected':           'red',
			'Cancelled':          'grey',
		};
		frm.page.set_indicator(frm.doc.status, indicator_map[frm.doc.status] || 'grey');

		// ── Action buttons based on status ───────────────────────────────
		if (frm.doc.status === 'Draft' && !frm.is_new()) {
			frm.add_custom_button(__('Submit for Approval'), () => {
				frappe.confirm(
					__('Submit this batch for manager approval? No prices will change until approved.'),
					() => {
						frm.call('submit_for_approval').then(() => frm.reload_doc());
					}
				);
			}, null).addClass('btn-primary');
		}

		if (frm.doc.status === 'Pending Approval') {
			if (frappe.user_roles.includes('System Manager') || frappe.user_roles.includes('CH Master Manager')) {
				frm.add_custom_button(__('Approve & Apply'), () => {
					frappe.confirm(
						__('Approve this batch? All {0} changes will be applied immediately.', [frm.doc.total_changes]),
						() => {
							frm.call('approve_and_apply').then(() => frm.reload_doc());
						}
					);
				}, null).addClass('btn-primary');

				frm.add_custom_button(__('Reject'), () => {
					const d = new frappe.ui.Dialog({
						title: __('Reject Price Upload'),
						fields: [
							{
								fieldtype: 'Small Text',
								fieldname: 'reason',
								label: __('Rejection Reason'),
								reqd: 1,
							}
						],
						primary_action_label: __('Reject'),
						primary_action(values) {
							d.hide();
							frm.call('reject_batch', { reason: values.reason }).then(() => frm.reload_doc());
						},
					});
					d.show();
				}).addClass('btn-danger');
			}
		}

		// ── Show rejection reason on Rejected batches ───────────────────
		if (frm.doc.status === 'Rejected' && frm.doc.rejection_reason) {
			frm.dashboard.set_headline(
				`<span style="color:var(--red-600)">Rejected: ${frm.doc.rejection_reason}</span>`
			);
		}

		// ── Revise: let maker fix and resubmit rejected/partial batches ──
		if (['Rejected', 'Partially Applied'].includes(frm.doc.status)) {
			frm.add_custom_button(__('Revise & Resubmit'), () => {
				frappe.confirm(
					__('Reset this batch to Draft? You can then edit rows and resubmit for approval.'),
					() => {
						frm.call('revise_batch').then(() => frm.reload_doc());
					}
				);
			}, null).addClass('btn-primary');
		}

		// ── Summary cards ────────────────────────────────────────────────
		// (rendered via summary fields — no extra HTML needed)

		// ── AI-based Price Intelligence ──────────────────────────────────
		if (!frm.is_new() && frm.doc.total_changes > 0
		    && ['Draft', 'Pending Approval'].includes(frm.doc.status)) {
			_render_price_intelligence(frm);
		}

		// ── Editability based on status ──────────────────────────────────
		if (frm.doc.status === 'Draft') {
			frm.enable_save();
			// Let maker edit new_value and reason in draft
			frm.fields_dict.items.grid.update_docfield_property('new_value', 'read_only', 0);
			frm.fields_dict.items.grid.update_docfield_property('reason', 'read_only', 0);

			// Show rejection reason banner if this is a revised batch
			if (frm.doc.rejection_reason) {
				frm.dashboard.set_headline(
					`<span style="color:var(--red-600)">Rejection Reason: ${frm.doc.rejection_reason}</span>`
				);
			}
		} else {
			frm.disable_save();
			frm.fields_dict.items.grid.update_docfield_property('new_value', 'read_only', 1);
			frm.fields_dict.items.grid.update_docfield_property('reason', 'read_only', 1);
		}
	},
});


// ── Price Intelligence ───────────────────────────────────────────────────────
function _render_price_intelligence(frm) {
	const items = frm.doc.items || [];
	if (!items.length) return;

	const LARGE_SWING_PCT = 15;

	// ── 1. Aggregate per-item data ───────────────────────────────────────
	const item_map = {};  // item_code → { name, fields[], issues[] }
	let total_increase = 0, total_decrease = 0;
	let max_swing = { pct: 0, label: '' };

	items.forEach((row) => {
		const old_val = parseFloat(row.old_value) || 0;
		const new_val = parseFloat(row.new_value) || 0;
		const code = row.item_code;
		const name = row.item_name || row.item_code;

		if (!item_map[code]) {
			item_map[code] = { name, fields: [], issues: new Set(), stock: null, margin: null, has_reason: true };
		}
		const entry = item_map[code];

		// Track stock
		if (row.current_stock != null) entry.stock = parseFloat(row.current_stock) || 0;
		if (row.margin_percent != null) entry.margin = parseFloat(row.margin_percent);

		// Direction
		if (new_val > old_val) total_increase++;
		else if (new_val < old_val) total_decrease++;

		// % change
		let pct_change = 0;
		if (old_val > 0 && new_val > 0) {
			pct_change = (new_val - old_val) / old_val * 100;
			if (Math.abs(pct_change) > Math.abs(max_swing.pct)) {
				max_swing = { pct: pct_change, label: name };
			}
		}

		// Collect field-level info
		const dir = new_val > old_val ? '↑' : new_val < old_val ? '↓' : '=';
		entry.fields.push({
			label: row.field_label, old_val, new_val, pct_change, dir,
			purchase: parseFloat(row.last_purchase_rate) || 0,
		});

		// Classify issues per item (not per field)
		if (old_val > 0 && new_val > 0 && Math.abs(pct_change) > LARGE_SWING_PCT) {
			entry.issues.add(Math.abs(pct_change) > 50 ? 'critical_swing' : 'large_swing');
		}
		if (old_val > 0 && new_val === 0) entry.issues.add('zero_price');
		if (row.change_type === 'Selling Price' && row.field_label === 'Selling Price') {
			const purchase = parseFloat(row.last_purchase_rate) || 0;
			if (purchase > 0 && new_val > 0 && new_val < purchase) entry.issues.add('negative_margin');
			if (entry.margin != null && entry.margin > 0 && entry.margin < 5) entry.issues.add('low_margin');
		}
		if (row.change_type === 'Selling Price' && entry.stock === 0 && old_val > 0) entry.issues.add('no_stock');
		if (old_val > 0 && !row.reason) entry.has_reason = false;
	});

	const item_list = Object.values(item_map);
	const unique_count = item_list.length;

	// ── 2. Categorize items by severity ──────────────────────────────────
	const critical_items = item_list.filter(i => i.issues.has('critical_swing') || i.issues.has('zero_price') || i.issues.has('negative_margin'));
	const warning_items = item_list.filter(i => !critical_items.includes(i) && (i.issues.has('large_swing') || i.issues.has('low_margin')));
	const info_items = item_list.filter(i => !critical_items.includes(i) && !warning_items.includes(i) && (i.issues.has('no_stock') || !i.has_reason));
	const clean_items = item_list.filter(i => i.issues.size === 0 && i.has_reason);

	// ── 3. Build summary pills ───────────────────────────────────────────
	const pills = [];
	pills.push(`📋 <b>${items.length}</b> changes across <b>${unique_count}</b> items`);
	if (total_increase) pills.push(`📈 <b>${total_increase}</b> increase${total_increase > 1 ? 's' : ''}`);
	if (total_decrease) pills.push(`📉 <b>${total_decrease}</b> decrease${total_decrease > 1 ? 's' : ''}`);
	if (max_swing.pct) {
		const dir = max_swing.pct > 0 ? 'increase' : 'decrease';
		pills.push(`🔀 Max swing: <b>${Math.abs(max_swing.pct).toFixed(1)}%</b> ${dir} on ${max_swing.label}`);
	}

	// Enrichment summary
	const stock_items = item_list.filter(i => i.stock != null && i.stock > 0);
	if (stock_items.length) {
		const avg = stock_items.reduce((s, i) => s + i.stock, 0) / stock_items.length;
		pills.push(`📦 Avg stock: <b>${avg.toFixed(0)}</b> units (${stock_items.length} items)`);
	}
	const margin_items = item_list.filter(i => i.margin != null);
	if (margin_items.length) {
		const avg = margin_items.reduce((s, i) => s + i.margin, 0) / margin_items.length;
		pills.push(`💰 Avg margin: <b>${avg.toFixed(1)}%</b>`);
	}

	// ── 4. Render ────────────────────────────────────────────────────────
	let html = '';

	// Summary
	html += `<div style="margin-bottom:14px">
		<div style="font-size:13px;font-weight:600;margin-bottom:8px;color:var(--heading-color)">📊 Analysis Summary</div>
		<div style="display:flex;flex-wrap:wrap;gap:6px">`;
	pills.forEach(p => {
		html += `<span style="padding:5px 10px;background:var(--bg-light-gray);border-radius:6px;font-size:11.5px">${p}</span>`;
	});
	html += `</div></div>`;

	// Alert sections
	const total_alerts = critical_items.length + warning_items.length + info_items.length;
	if (total_alerts > 0) {
		html += `<div style="margin-top:10px">
			<div style="font-size:13px;font-weight:600;margin-bottom:8px;color:var(--heading-color)">
				🔍 Alerts
				<span class="indicator-pill ${critical_items.length ? 'red' : 'orange'}" style="font-size:10px;margin-left:6px">
					${total_alerts} item${total_alerts > 1 ? 's' : ''} need attention
				</span>
			</div>`;

		if (critical_items.length) {
			html += _render_alert_group('🔴 Critical', critical_items, 'red-500', 'red');
		}
		if (warning_items.length) {
			html += _render_alert_group('🟠 Warnings', warning_items, 'orange-500', 'orange');
		}
		if (info_items.length) {
			html += _render_alert_group('🟡 Info', info_items, 'yellow-500', 'yellow');
		}
		html += `</div>`;

		// Dashboard intro
		if (critical_items.length) {
			frm.set_intro(`⚠️ ${critical_items.length} item${critical_items.length > 1 ? 's' : ''} with critical pricing issues — review before approving`, 'red');
		} else {
			frm.set_intro(`⚡ ${warning_items.length} item${warning_items.length > 1 ? 's' : ''} with pricing warnings — review recommended`, 'orange');
		}
	} else {
		html += `<div style="padding:10px 14px;background:var(--bg-green);border-radius:6px;font-size:12px;color:var(--green-700);margin-top:8px">
			✅ <b>No pricing anomalies detected</b> — all ${unique_count} items look reasonable. Safe to approve.
		</div>`;
		frm.set_intro('✅ All changes look reasonable — no pricing anomalies detected', 'green');
	}

	$(frm.fields_dict.alerts_html.wrapper).html(html);
}

function _render_alert_group(title, alert_items, border_color, pill_color) {
	let html = `<div style="font-size:11px;font-weight:600;color:var(--${border_color});margin:10px 0 4px">
		${title} (${alert_items.length})
	</div>`;

	alert_items.forEach(item => {
		// Build compact issue tags
		const tags = [];
		if (item.issues.has('critical_swing') || item.issues.has('large_swing')) {
			const max_pct = Math.max(...item.fields.map(f => Math.abs(f.pct_change)));
			tags.push(`⚠️ ${max_pct.toFixed(0)}% swing`);
		}
		if (item.issues.has('zero_price')) tags.push('🚫 Set to ₹0');
		if (item.issues.has('negative_margin')) tags.push('📉 Below cost');
		if (item.issues.has('low_margin')) tags.push(`📊 ${(item.margin || 0).toFixed(1)}% margin`);
		if (item.issues.has('no_stock')) tags.push('📦 No stock');
		if (!item.has_reason) tags.push('📝 No reason');

		// Build compact field changes line
		const field_summary = item.fields.map(f =>
			`${f.label} ${f.dir} ${_fmt(f.new_val)}`
		).join(' · ');

		html += `<div style="padding:8px 12px;margin:3px 0;border-left:3px solid var(--${border_color});
		                      background:var(--bg-light-gray);border-radius:4px;font-size:12px">
			<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:4px">
				<span><b>${item.name}</b></span>
				<span style="display:flex;flex-wrap:wrap;gap:4px">
					${tags.map(t => `<span class="indicator-pill no-indicator-dot ${pill_color}" style="font-size:9px;white-space:nowrap">${t}</span>`).join('')}
				</span>
			</div>
			<div style="font-size:11px;color:var(--text-muted);margin-top:3px">${field_summary}</div>
		</div>`;
	});

	return html;
}

function _fmt(val) {
	return '₹ ' + parseFloat(val).toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}
