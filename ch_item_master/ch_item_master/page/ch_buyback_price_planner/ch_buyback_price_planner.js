/* ─── Buyback Price Planner ───────────────────────────────────────────────────
 * One view: what we pay, what the market pays, how much the model matters.
 *
 * The grid stages changes locally and pushes them as a Draft CH Price Upload
 * Batch — it never writes a Buyback Price Master row directly, because those
 * are under maker/checker governance and should stay that way.
 * ─────────────────────────────────────────────────────────────────────────── */

frappe.pages['ch-buyback-price-planner'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Buyback Price Planner'),
		single_column: true,
	});

	const state = {
		filters: {
			item_group: 'Mobiles',
			condition_profile: 'Good',
			search: '',
			coverage: '',
			sort_by: 'sales',
		},
		page: 1,
		page_length: 50,
		data: { rows: [], total: 0, competitors: [], benchmark_field: '' },
		staged: {},          // `${item_code}::${field}` → number
		loading: false,
	};

	page.add_inner_button(__('Refresh'), () => load(page, state));
	page.add_action_item(__('Competitor Sources'), () =>
		frappe.set_route('List', 'CH Competitor Source')
	);
	page.add_action_item(__('Collected Snapshots'), () =>
		frappe.set_route('List', 'CH Competitor Price Snapshot')
	);

	page.main.html(shell_html());
	bind(page, state);
	load(page, state);
};

/* ── Markup ──────────────────────────────────────────────────────────────── */

function shell_html() {
	return `
	<div class="chbp">
		<div class="chbp-summary" id="chbp-summary"></div>

		<div class="chbp-toolbar">
			<input type="text" class="form-control chbp-search"
			       placeholder="${__('Search model…')}" id="chbp-search">
			<select class="form-control chbp-select" id="chbp-coverage">
				<option value="">${__('All models')}</option>
				<option value="missing_price">${__('No buy price set')}</option>
				<option value="has_price">${__('Priced')}</option>
				<option value="has_market">${__('Has market data')}</option>
				<option value="no_market">${__('No market data')}</option>
			</select>
			<select class="form-control chbp-select" id="chbp-profile">
				<option value="Good">${__('Profile: Good')}</option>
				<option value="Excellent">${__('Profile: Excellent')}</option>
				<option value="Average">${__('Profile: Average')}</option>
			</select>
			<select class="form-control chbp-select" id="chbp-sort">
				<option value="sales">${__('Sort: Sales volume')}</option>
				<option value="gap">${__('Sort: Biggest gap')}</option>
				<option value="name">${__('Sort: Name')}</option>
			</select>
			<div class="chbp-spacer"></div>
			<button class="btn btn-primary btn-sm" id="chbp-stage" disabled>
				${__('Create Price Batch')} <span class="chbp-count"></span>
			</button>
		</div>

		<div class="chbp-grid-wrap">
			<table class="chbp-grid">
				<thead id="chbp-head"></thead>
				<tbody id="chbp-body"></tbody>
			</table>
		</div>

		<div class="chbp-footer">
			<span id="chbp-range" class="text-muted"></span>
			<div class="chbp-pager">
				<button class="btn btn-default btn-xs" id="chbp-prev">${__('Previous')}</button>
				<button class="btn btn-default btn-xs" id="chbp-next">${__('Next')}</button>
			</div>
		</div>
	</div>
	${style_html()}`;
}

function style_html() {
	return `<style>
	.chbp { padding: 0 4px 24px; }
	.chbp-summary { display: flex; flex-wrap: wrap; gap: 12px; margin: 4px 0 16px; }
	.chbp-stat {
		flex: 1 1 150px; background: var(--fg-color); border: 1px solid var(--border-color);
		border-radius: 8px; padding: 12px 14px;
	}
	.chbp-stat .k {
		font-size: 10px; letter-spacing: .09em; text-transform: uppercase;
		color: var(--text-muted); font-weight: 600;
	}
	.chbp-stat .v {
		font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums;
		color: var(--text-color); line-height: 1.3;
	}
	.chbp-stat .s { font-size: 11px; color: var(--text-muted); }
	.chbp-stat.alert .v { color: var(--red-500); }

	.chbp-toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
	.chbp-toolbar .form-control { width: auto; min-width: 150px; }
	.chbp-toolbar .chbp-search { min-width: 220px; }
	.chbp-spacer { flex: 1 1 auto; }

	.chbp-grid-wrap { overflow-x: auto; border: 1px solid var(--border-color); border-radius: 8px; }
	.chbp-grid { width: 100%; border-collapse: collapse; font-size: 13px; background: var(--fg-color); }
	.chbp-grid th {
		text-align: left; padding: 9px 12px; font-size: 10px; letter-spacing: .08em;
		text-transform: uppercase; color: var(--text-muted); font-weight: 600;
		border-bottom: 1px solid var(--border-color); white-space: nowrap;
		background: var(--subtle-accent); position: sticky; top: 0; z-index: 1;
	}
	.chbp-grid th.num, .chbp-grid td.num { text-align: right; font-variant-numeric: tabular-nums; }
	.chbp-grid td {
		padding: 9px 12px; border-bottom: 1px solid var(--border-color);
		color: var(--text-color); vertical-align: middle;
	}
	.chbp-grid tbody tr:hover { background: var(--subtle-fg); }
	.chbp-grid tbody tr.staged { background: var(--bg-blue); }

	.chbp-model { font-weight: 600; }
	.chbp-model .code { display: block; font-size: 11px; color: var(--text-muted); font-weight: 400; }

	.chbp-band { display: flex; flex-direction: column; gap: 3px; min-width: 170px; }
	.chbp-band .nums {
		display: flex; justify-content: space-between; gap: 10px;
		font-size: 11px; color: var(--text-muted); font-variant-numeric: tabular-nums;
	}
	.chbp-band .nums b { color: var(--text-color); }
	.chbp-track {
		position: relative; height: 5px; border-radius: 3px; background: var(--border-color);
	}
	.chbp-track .ours {
		position: absolute; top: -3px; width: 3px; height: 11px; border-radius: 2px;
		background: var(--text-color);
	}
	.chbp-none { color: var(--text-muted); font-style: italic; font-size: 12px; }

	.chbp-plan-input {
		width: 100px; text-align: right; padding: 3px 6px; font-size: 13px;
		border: 1px solid var(--border-color); border-radius: 4px;
		background: var(--control-bg); color: var(--text-color);
		font-variant-numeric: tabular-nums;
	}
	.chbp-plan-input:focus { border-color: var(--primary); outline: none; }

	.chbp-chips { display: flex; flex-wrap: wrap; gap: 4px; }
	.chbp-chip {
		font-size: 10px; font-weight: 600; letter-spacing: .04em; padding: 2px 7px;
		border-radius: 10px; white-space: nowrap; border: 1px solid transparent;
	}
	.chbp-chip.critical { background: var(--bg-red); color: var(--red-600); border-color: var(--red-300); }
	.chbp-chip.warning  { background: var(--bg-yellow); color: var(--yellow-700); border-color: var(--yellow-300); }
	.chbp-chip.neutral  { background: var(--bg-gray); color: var(--text-muted); border-color: var(--border-color); }

	.chbp-gap.pos { color: var(--red-600); font-weight: 600; }
	.chbp-gap.neg { color: var(--yellow-700); font-weight: 600; }

	/* Our price and the competitor block are the comparison — rule them off
	   from the rest so the eye reads ours | theirs as one unit. */
	.chbp-grid th.chbp-ours, .chbp-grid td.chbp-ours {
		border-left: 2px solid var(--border-color);
		font-weight: 600;
	}
	.chbp-grid th.chbp-site:first-of-type, .chbp-grid td.chbp-site:first-of-type {
		border-left: 1px dashed var(--border-color);
	}
	.chbp-grid td.chbp-site { white-space: nowrap; }
	.chbp-grid td.chbp-site a { color: inherit; text-decoration: none; border-bottom: 1px dotted currentColor; }
	.chbp-grid td.chbp-site a:hover { color: var(--primary); }
	/* Dearest competitor is who the customer quotes at you; cheapest is the
	   floor you can defend. Both earn a mark, nothing in between does. */
	.chbp-grid td.chbp-site.hi { color: var(--red-600); font-weight: 700; }
	.chbp-grid td.chbp-site.lo { color: var(--green-600); font-weight: 700; }

	.chbp-spread { font-size: 12px; color: var(--text-muted); line-height: 1.25; }
	.chbp-spread.wide { color: var(--red-600); font-weight: 700; }
	.chbp-spread small { font-size: 10px; opacity: .8; }

	.chbp-footer {
		display: flex; align-items: center; justify-content: space-between;
		margin-top: 12px; gap: 12px; flex-wrap: wrap;
	}
	.chbp-pager { display: flex; gap: 6px; }
	.chbp-empty { padding: 40px 16px; text-align: center; color: var(--text-muted); }
	</style>`;
}

/* ── Wiring ──────────────────────────────────────────────────────────────── */

function bind(page, state) {
	const $m = $(page.main);

	$m.find('#chbp-search').on('input', frappe.utils.debounce(function () {
		state.filters.search = this.value.trim();
		state.page = 1;
		load(page, state);
	}, 350));

	$m.find('#chbp-coverage, #chbp-profile, #chbp-sort').on('change', function () {
		const map = { 'chbp-coverage': 'coverage', 'chbp-profile': 'condition_profile', 'chbp-sort': 'sort_by' };
		state.filters[map[this.id]] = this.value;
		state.page = 1;
		load(page, state);
	});

	$m.find('#chbp-prev').on('click', () => {
		if (state.page > 1) { state.page--; load(page, state); }
	});
	$m.find('#chbp-next').on('click', () => {
		if (state.page * state.page_length < state.data.total) { state.page++; load(page, state); }
	});

	// Delegated so it survives every re-render of the grid body.
	$m.on('change', '.chbp-plan-input', function () {
		const key = `${$(this).data('item')}::${$(this).data('field')}`;
		const value = flt($(this).val());
		if (value > 0) {
			state.staged[key] = value;
		} else {
			delete state.staged[key];
		}
		$(this).closest('tr').toggleClass('staged', value > 0);
		update_stage_button(page, state);
	});

	$m.find('#chbp-stage').on('click', () => stage_batch(page, state));
}

function update_stage_button(page, state) {
	const n = Object.keys(state.staged).length;
	const $btn = $(page.main).find('#chbp-stage');
	$btn.prop('disabled', n === 0);
	$btn.find('.chbp-count').text(n ? `(${n})` : '');
}

/* ── Data ────────────────────────────────────────────────────────────────── */

function load(page, state) {
	if (state.loading) return;
	state.loading = true;

	const $body = $(page.main).find('#chbp-body');
	$body.html(`<tr><td colspan="99" class="chbp-empty">${__('Loading…')}</td></tr>`);

	Promise.all([
		frappe.xcall('ch_item_master.ch_item_master.buyback_planner_api.get_planner_rows', {
			...state.filters, page: state.page, page_length: state.page_length,
		}),
		frappe.xcall('ch_item_master.ch_item_master.buyback_planner_api.get_planner_summary', {
			item_group: state.filters.item_group,
			condition_profile: state.filters.condition_profile,
		}),
	]).then(([rows, summary]) => {
		state.data = rows;
		render_summary(page, summary);
		render_rows(page, state);
	}).catch((e) => {
		$body.html(`<tr><td colspan="${8 + state.data.competitors.length}" class="chbp-empty">${
			__('Could not load planner data.')} ${frappe.utils.escape_html(e.message || '')}</td></tr>`);
	}).finally(() => {
		// .finally, not jQuery's .always — frappe.xcall returns a native
		// Promise, and calling .always on one throws inside on_page_load,
		// which aborts before the desk ever shows the page container.
		state.loading = false;
	});
}

function render_summary(page, s) {
	const pct = (n, d) => (d ? Math.round((n / d) * 100) : 0);
	const unpriced = flt(s.models) - flt(s.priced);

	$(page.main).find('#chbp-summary').html(`
		<div class="chbp-stat">
			<div class="k">${__('Models')}</div>
			<div class="v">${format_number(s.models, null, 0)}</div>
			<div class="s">${__('in this group')}</div>
		</div>
		<div class="chbp-stat ${unpriced ? 'alert' : ''}">
			<div class="k">${__('No buy price')}</div>
			<div class="v">${format_number(unpriced, null, 0)}</div>
			<div class="s">${pct(unpriced, s.models)}% ${__('of catalogue')}</div>
		</div>
		<div class="chbp-stat">
			<div class="k">${__('With market data')}</div>
			<div class="v">${format_number(s.with_market, null, 0)}</div>
			<div class="s">${format_number(s.stale, null, 0)} ${__('stale')}</div>
		</div>
		<div class="chbp-stat">
			<div class="k">${__('Collected (7d)')}</div>
			<div class="v">${format_number(s.snapshots_7d, null, 0)}</div>
			<div class="s">${format_number(s.sources, null, 0)} ${__('sources')} ·
			                ${format_number(s.links, null, 0)} ${__('links')}</div>
		</div>
	`);
}

function render_head(page, state) {
	const sites = state.data.competitors || [];
	$(page.main).find('#chbp-head').html(`
		<tr>
			<th class="chbp-th-model">${__('Model')}</th>
			<th class="num">${__('12-mo Sales')}</th>
			<th class="num chbp-ours">${__('Our Price')}</th>
			${sites.map((c) => `<th class="num chbp-site">${frappe.utils.escape_html(c)}</th>`).join('')}
			<th class="num">${__('Spread')}</th>
			<th class="chbp-th-band">${__('Market Band')}</th>
			<th class="num">${__('Gap')}</th>
			<th class="num">${__('Suggested')}</th>
			<th class="num chbp-th-plan">${__('Plan')}</th>
			<th>${__('Insights')}</th>
		</tr>`);
}

function site_cells_html(r, state) {
	const sites = state.data.competitors || [];
	const prices = sites.map((c) => flt((r.sites || {})[c] && r.sites[c].price));
	const real = prices.filter((p) => p > 0);
	// Cheapest and dearest are what the eye is hunting for; mark them rather
	// than leaving the reader to compare five numbers by hand.
	const lo = real.length ? Math.min(...real) : null;
	const hi = real.length > 1 ? Math.max(...real) : null;

	return sites.map((c, i) => {
		const p = prices[i];
		if (!p) return `<td class="num chbp-site chbp-none">—</td>`;
		const info = r.sites[c];
		const cls = p === hi ? 'hi' : (p === lo ? 'lo' : '');
		const title = `${c} · ${frappe.datetime.comment_when(info.captured_at)}`;
		return `<td class="num chbp-site ${cls}" title="${frappe.utils.escape_html(title)}">${
			info.url ? `<a href="${frappe.utils.escape_html(info.url)}" target="_blank" rel="noopener">${
				format_currency(p)}</a>` : format_currency(p)}</td>`;
	}).join('');
}

function spread_html(r, state) {
	const sites = state.data.competitors || [];
	const real = sites.map((c) => flt((r.sites || {})[c] && r.sites[c].price)).filter((p) => p > 0);
	if (real.length < 2) return '<span class="chbp-none">—</span>';
	const lo = Math.min(...real), hi = Math.max(...real);
	const pct = Math.round(((hi - lo) / lo) * 100);
	// A wide spread means the "market price" is not one number, so a median
	// planned against it deserves a second look.
	return `<span class="chbp-spread ${pct >= 25 ? 'wide' : ''}">${format_currency(hi - lo)}<br>
		<small>${pct}%</small></span>`;
}

function render_rows(page, state) {
	const { rows, total } = state.data;
	const $body = $(page.main).find('#chbp-body');
	const span = 8 + (state.data.competitors || []).length;
	render_head(page, state);

	if (!rows.length) {
		$body.html(`<tr><td colspan="${span}" class="chbp-empty">${
			__('No models match these filters.')}</td></tr>`);
	} else {
		$body.html(rows.map((r) => row_html(r, state)).join(''));
	}

	const from = total ? (state.page - 1) * state.page_length + 1 : 0;
	const to = Math.min(state.page * state.page_length, total);
	$(page.main).find('#chbp-range').text(
		__('Showing {0}–{1} of {2}', [from, to, format_number(total, null, 0)])
	);
	$(page.main).find('#chbp-prev').prop('disabled', state.page <= 1);
	$(page.main).find('#chbp-next').prop('disabled', to >= total);
	update_stage_button(page, state);
}

function row_html(r, state) {
	const esc = frappe.utils.escape_html;
	const key = `${r.item_code}::${state.data.benchmark_field}`;
	const staged = state.staged[key];

	return `
	<tr class="${staged ? 'staged' : ''}">
		<td>
			<div class="chbp-model">${esc(r.item_name || r.item_code)}
				<span class="code">${esc(r.item_code)}${r.brand ? ' · ' + esc(r.brand) : ''}</span>
			</div>
		</td>
		<td class="num">${r.sales_qty ? format_number(r.sales_qty, null, 0) : '—'}</td>
		<td class="num chbp-ours">${r.our_price ? format_currency(r.our_price) : `<span class="chbp-none">${__('not set')}</span>`}</td>
		${site_cells_html(r, state)}
		<td class="num">${spread_html(r, state)}</td>
		<td>${band_html(r)}</td>
		<td class="num">${gap_html(r)}</td>
		<td class="num">${r.suggested_price ? format_currency(r.suggested_price) : '—'}</td>
		<td class="num">
			<input type="number" class="chbp-plan-input" min="0" step="10"
			       data-item="${esc(r.item_code)}" data-field="${esc(state.data.benchmark_field)}"
			       value="${staged || ''}" placeholder="${r.suggested_price || ''}">
		</td>
		<td>${chips_html(r.insights)}</td>
	</tr>`;
}

function band_html(r) {
	if (!r.rollup || !r.market_median) {
		return `<span class="chbp-none">${__('no data')}</span>`;
	}

	const low = flt(r.market_low), high = flt(r.market_high), ours = flt(r.our_price);
	// Clamp so an out-of-band price still renders inside the track instead of
	// escaping the cell.
	const span = high - low;
	const pos = span > 0 && ours ? Math.min(Math.max((ours - low) / span, 0), 1) * 100 : null;

	return `
	<div class="chbp-band">
		<div class="nums">
			<span>${format_currency(low)}</span>
			<b>${format_currency(r.market_median)}</b>
			<span>${format_currency(high)}</span>
		</div>
		<div class="chbp-track">
			${pos === null ? '' : `<span class="ours" style="left:calc(${pos}% - 1px)"></span>`}
		</div>
		<div class="nums">
			<span>${r.competitor_count || 0} ${__('sources')}</span>
			<span>${r.is_stale ? __('stale') : frappe.datetime.comment_when(r.latest_captured_at)}</span>
		</div>
	</div>`;
}

function gap_html(r) {
	if (r.gap_pct === null || r.gap_pct === undefined) return '—';
	const pct = Math.round(flt(r.gap_pct) * 100);
	if (!pct) return '0%';
	return `<span class="chbp-gap ${pct > 0 ? 'pos' : 'neg'}">${pct > 0 ? '+' : ''}${pct}%</span>`;
}

function chips_html(insights) {
	if (!insights || !insights.length) return '';
	return `<div class="chbp-chips">${insights.map((i) =>
		`<span class="chbp-chip ${i.tone}">${frappe.utils.escape_html(i.label)}</span>`
	).join('')}</div>`;
}

/* ── Staging ─────────────────────────────────────────────────────────────── */

function stage_batch(page, state) {
	const changes = Object.keys(state.staged).map((key) => {
		const [item_code, field] = key.split('::');
		return { item_code, field, new_value: state.staged[key] };
	});

	if (!changes.length) return;

	const d = new frappe.ui.Dialog({
		title: __('Create Price Batch'),
		fields: [
			{
				fieldtype: 'HTML', fieldname: 'preview',
				options: `<p>${__('Staging {0} price change(s) as a Draft batch. Nothing is applied until it is approved.',
					[changes.length])}</p>`,
			},
			{
				fieldtype: 'Small Text', fieldname: 'reason', reqd: 1,
				label: __('Reason'),
				description: __('This is what the approver reads to judge the batch.'),
			},
		],
		primary_action_label: __('Create Draft Batch'),
		primary_action: (v) => {
			if (!(v.reason || '').trim()) {
				frappe.show_alert({ message: __('A reason is required'), indicator: 'orange' });
				return;
			}
			d.hide();
			frappe.xcall('ch_item_master.ch_item_master.buyback_planner_api.create_planner_batch', {
				changes: JSON.stringify(changes),
				reason: v.reason,
			}).then((res) => {
				state.staged = {};
				frappe.show_alert({
					message: __('Batch {0} created with {1} change(s)', [res.batch_name, res.total_changes]),
					indicator: 'green',
				});
				frappe.set_route('Form', 'CH Price Upload Batch', res.batch_name);
			});
		},
	});
	d.show();
}
