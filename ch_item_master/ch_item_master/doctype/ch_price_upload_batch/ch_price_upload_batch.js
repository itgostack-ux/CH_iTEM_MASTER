// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.ui.form.on("CH Price Upload Batch", {
	refresh(frm) {
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

		// ── Summary cards ────────────────────────────────────────────────
		if (!frm.is_new() && frm.doc.total_changes > 0) {
			const s = frm.doc;
			let html = `<div class="row" style="margin-bottom:10px">`;

			const card = (label, val, color) => `
				<div class="col-sm-3">
					<div style="padding:10px;border-radius:6px;background:var(--bg-light-gray);text-align:center">
						<div style="font-size:22px;font-weight:600;color:var(--${color})">${val}</div>
						<div style="font-size:12px;color:var(--text-muted)">${label}</div>
					</div>
				</div>`;

			html += card('Total Changes', s.total_changes, 'text-color');
			html += card('Selling Prices', s.selling_price_changes, 'blue-500');
			html += card('Buyback Prices', s.buyback_price_changes, 'orange-500');
			html += card('Tags', s.tag_changes, 'green-500');
			html += `</div>`;

			if (['Applied', 'Partially Applied'].includes(s.status)) {
				html += `<div class="row">`;
				html += card('Applied', s.applied_count, 'green-500');
				html += card('Skipped', s.skipped_count, 'text-muted');
				html += card('Errors', s.error_count, 'red-500');
				html += `</div>`;
			}

			frm.set_df_property('items_section', 'description', html);
		}

		// ── AI-based Alerts for Checker ──────────────────────────────────
		if (!frm.is_new() && frm.doc.total_changes > 0
		    && ['Draft', 'Pending Approval'].includes(frm.doc.status)) {
			_render_price_alerts(frm);
		}

		// ── Disable editing on non-Draft batches ─────────────────────────
		if (frm.doc.status !== 'Draft') {
			frm.disable_save();
		}
	},
});


// ── Price Intelligence Alerts ────────────────────────────────────────────────
function _render_price_alerts(frm) {
	const items = frm.doc.items || [];
	if (!items.length) return;

	const alerts = [];
	const LARGE_SWING_PCT = 20;  // >20% change is a large swing

	items.forEach((row, idx) => {
		const old_val = parseFloat(row.old_value) || 0;
		const new_val = parseFloat(row.new_value) || 0;
		const item_label = `<b>${row.item_code}</b> → ${row.field_label}`;

		// 1. Large price swing (>20%)
		if (old_val > 0 && new_val > 0) {
			const pct = Math.abs((new_val - old_val) / old_val * 100);
			if (pct > LARGE_SWING_PCT) {
				const dir = new_val > old_val ? 'increase' : 'decrease';
				alerts.push({
					level: pct > 50 ? 'red' : 'orange',
					icon: '⚠️',
					msg: `${item_label}: <b>${pct.toFixed(1)}% ${dir}</b> (${frappe.format(old_val, {fieldtype:'Currency'})} → ${frappe.format(new_val, {fieldtype:'Currency'})})`,
					type: 'Large Price Swing',
				});
			}
		}

		// 2. Price set to zero (possible data entry error)
		if (old_val > 0 && new_val === 0) {
			alerts.push({
				level: 'red',
				icon: '🚫',
				msg: `${item_label}: Price dropped to <b>₹0</b> from ${frappe.format(old_val, {fieldtype:'Currency'})}. Intentional?`,
				type: 'Zero Price',
			});
		}

		// 3. Negative margin (selling below purchase cost)
		if (row.change_type === 'Selling Price' && row.field_label === 'Selling Price'
		    && row.last_purchase_rate && new_val > 0) {
			const purchase = parseFloat(row.last_purchase_rate) || 0;
			if (purchase > 0 && new_val < purchase) {
				const loss_pct = ((purchase - new_val) / purchase * 100).toFixed(1);
				alerts.push({
					level: 'red',
					icon: '📉',
					msg: `${item_label}: New price <b>₹${new_val}</b> is <b>below purchase cost ₹${purchase}</b> (−${loss_pct}% margin)`,
					type: 'Negative Margin',
				});
			}
		}

		// 4. Low margin warning (<5%)
		if (row.change_type === 'Selling Price' && row.field_label === 'Selling Price'
		    && row.margin_percent !== null && row.margin_percent !== undefined) {
			const margin = parseFloat(row.margin_percent) || 0;
			if (margin > 0 && margin < 5) {
				alerts.push({
					level: 'orange',
					icon: '📊',
					msg: `${item_label}: Thin margin of <b>${margin.toFixed(1)}%</b> on new price`,
					type: 'Low Margin',
				});
			}
		}

		// 5. Zero stock item getting price change
		if (row.change_type === 'Selling Price' && row.current_stock !== null
		    && parseFloat(row.current_stock) === 0 && old_val > 0) {
			alerts.push({
				level: 'yellow',
				icon: '📦',
				msg: `${item_label}: Item has <b>zero stock</b> — re-pricing may not be impactful right now`,
				type: 'No Stock',
			});
		}

		// 6. Missing reason for price change
		if (old_val > 0 && !row.reason) {
			alerts.push({
				level: 'yellow',
				icon: '📝',
				msg: `${item_label}: No reason provided for this price change`,
				type: 'Missing Reason',
			});
		}
	});

	if (!alerts.length) {
		frm.dashboard.set_headline(
			'<span style="color:var(--green-500)">✅ No pricing anomalies detected — all changes look reasonable</span>'
		);
		return;
	}

	// Group by severity
	const red_alerts = alerts.filter(a => a.level === 'red');
	const orange_alerts = alerts.filter(a => a.level === 'orange');
	const yellow_alerts = alerts.filter(a => a.level === 'yellow');

	let alert_html = `<div style="margin:12px 0 16px 0">
		<h6 style="margin-bottom:8px;font-weight:600">
			🔍 Price Intelligence Alerts
			<span class="badge badge-${red_alerts.length ? 'danger' : 'warning'}" style="margin-left:8px">
				${alerts.length} alert${alerts.length > 1 ? 's' : ''}
			</span>
		</h6>`;

	const _render_group = (items, color, border_color) => {
		return items.map(a => `
			<div style="padding:8px 12px;margin:4px 0;border-left:3px solid var(--${border_color});
			            background:var(--bg-light-gray);border-radius:4px;font-size:12px">
				${a.icon} <span class="badge badge-light" style="font-size:10px">${a.type}</span>
				${a.msg}
			</div>
		`).join('');
	};

	if (red_alerts.length) {
		alert_html += `<div style="margin-bottom:6px;font-size:11px;font-weight:600;color:var(--red-500)">
			Critical (${red_alerts.length})</div>`;
		alert_html += _render_group(red_alerts, 'red', 'red-500');
	}
	if (orange_alerts.length) {
		alert_html += `<div style="margin:8px 0 6px;font-size:11px;font-weight:600;color:var(--orange-500)">
			Warnings (${orange_alerts.length})</div>`;
		alert_html += _render_group(orange_alerts, 'orange', 'orange-500');
	}
	if (yellow_alerts.length) {
		alert_html += `<div style="margin:8px 0 6px;font-size:11px;font-weight:600;color:var(--yellow-500)">
			Info (${yellow_alerts.length})</div>`;
		alert_html += _render_group(yellow_alerts, 'yellow', 'yellow-500');
	}

	alert_html += `</div>`;

	// Show alerts above the items table
	frm.set_df_property('items_section', 'description',
		(frm.fields_dict.items_section.df.description || '') + alert_html
	);

	// Also set headline for immediate visibility
	if (red_alerts.length) {
		frm.dashboard.set_headline(
			`<span style="color:var(--red-500)">⚠️ ${red_alerts.length} critical alert${red_alerts.length > 1 ? 's' : ''} — review before approving</span>`
		);
	} else if (orange_alerts.length) {
		frm.dashboard.set_headline(
			`<span style="color:var(--orange-500)">⚡ ${orange_alerts.length} warning${orange_alerts.length > 1 ? 's' : ''} — review before approving</span>`
		);
	}
}
