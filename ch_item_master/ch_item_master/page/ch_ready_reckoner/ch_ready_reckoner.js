/* ─── CH Ready Reckoner ──────────────────────────────────────────────────────────
 * Full-featured price management grid with inline editing.
 * Channels are dynamic (from CH Price Channel doctype).
 * Click any price cell → quick-edit dialog.
 * Click item name → full side drawer with tabs.
 * ─────────────────────────────────────────────────────────────────────────── */

frappe.pages['ch-ready-reckoner'].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __('CH Ready Reckoner'),
        single_column: true,
    });

    // ── Page-level action buttons ─────────────────────────────────────────
    page.add_action_item(__('Export Excel'), () => chpb_export(state));
    page.add_action_item(__('New Price Record'), () =>
        frappe.new_doc('CH Item Price')
    );
    page.add_action_item(__('New Offer'), () =>
        frappe.new_doc('CH Item Offer')
    );

    // Shared state
    const state = {
        filters: {
            item_search: '', category: '', sub_category: '',
            brand: '', model: '', channel: '',
            as_of_date: frappe.datetime.get_today(),
            tag_filter: '', price_status: '', company: '',
        },
        group_by_price_specs: 1,
        page: 1,
        page_length: 50,
        data: { items: [], channels: [], total: 0 },
        loading: false,
    };

    // ── Inject styles ─────────────────────────────────────────────────────
    frappe.dom.set_style(`
        .chpb-wrap { display: flex; flex-direction: column; height: calc(100vh - 120px); }
        .chpb-filters {
            background: var(--card-bg);
            border-bottom: 1px solid var(--border-color);
            padding: 10px 16px;
            display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end;
        }
        .chpb-filters .filter-group { display: flex; flex-direction: column; gap: 3px; }
        .chpb-filters label { font-size: 11px; font-weight: 600; color: var(--text-muted); margin: 0; text-transform: uppercase; }
        .chpb-filters .form-control, .chpb-filters .frappe-control input {
            height: 28px; font-size: 12px; min-width: 120px; max-width: 160px;
        }
        .chpb-stats {
            padding: 6px 16px; background: var(--subtle-accent);
            font-size: 12px; color: var(--text-muted);
            border-bottom: 1px solid var(--border-color);
            display: flex; justify-content: space-between; align-items: center;
        }
        .chpb-body { display: flex; flex: 1; overflow: hidden; }
        .chpb-table-wrap { flex: 1; overflow: auto; }
        .chpb-table {
            width: 100%; border-collapse: collapse; font-size: 12px;
            white-space: nowrap;
        }
        .chpb-table thead th {
            position: sticky; top: 0; z-index: 2;
            background: var(--subtle-fg); color: var(--text-muted);
            font-weight: 600; font-size: 11px; text-transform: uppercase;
            padding: 6px 10px; border: 1px solid var(--border-color);
        }
        .chpb-table thead th.ch-group {
            background: var(--blue-50, #e8f4fd); color: var(--blue-600, #2490ef);
        }
        .chpb-table tbody tr { cursor: pointer; }
        .chpb-table tbody tr:hover { background: var(--bg-light-gray, #f8f8f8); }
        .chpb-table tbody tr.selected { background: var(--blue-50, #e8f4fd); }
        .chpb-table td {
            padding: 5px 10px; border: 1px solid var(--border-color);
            vertical-align: middle;
        }
        .chpb-table td.item-code { font-weight: 600; color: var(--blue-600, #2490ef); }
        .chpb-table td.item-name { max-width: 180px; overflow: hidden; text-overflow: ellipsis; }
        .chpb-table td.price-cell {
            text-align: right; cursor: pointer; position: relative;
            min-width: 70px;
        }
        .chpb-table td.price-cell:hover { background: var(--yellow-50, #fffbeb); }
        .chpb-table td.price-cell.has-value { color: var(--text-color); }
        .chpb-table td.price-cell.no-value { color: var(--border-color); }
        .chpb-table td.price-cell .edit-hint {
            display: none; position: absolute; right: 2px; top: 2px;
            font-size: 9px; color: var(--text-muted);
        }
        .chpb-table td.price-cell:hover .edit-hint { display: block; }
        .chpb-table td.status-badge span {
            padding: 1px 6px; border-radius: 8px; font-size: 10px; font-weight: 600;
        }
        .chpb-table td.status-badge span.Active { background: #d4edda; color: #155724; }
        .chpb-table td.status-badge span.Scheduled { background: #cce5ff; color: #004085; }
        .chpb-table td.status-badge span.Expired { background: #f8d7da; color: #721c24; }
        .chpb-table td.status-badge span.Draft { background: #fff3cd; color: #856404; }
        .chpb-table .offer-cell { color: var(--green-600, #28a745); font-size: 11px; }
        .chpb-table .tag-cell { font-size: 10px; }
        .chpb-tag { display: inline-block; padding: 1px 6px; border-radius: 8px;
            font-size: 10px; font-weight: 600; margin: 1px;
            background: var(--gray-200); color: var(--text-muted); }
        .chpb-tag.EOL { background: #f8d7da; color: #721c24; }
        .chpb-tag.FAST\ MOVING { background: #d4edda; color: #155724; }
        .chpb-tag.SLOW\ MOVING { background: #fff3cd; color: #856404; }
        .chpb-tag.NEW { background: #cce5ff; color: #004085; }
        .chpb-tag.RESTRICTED { background: #f5c6cb; color: #721c24; }
        .chpb-tag.PROMO\ FOCUS { background: #e2d9f3; color: #4a235a; }

        /* Side Drawer */
        .chpb-drawer {
            width: 0; overflow: hidden;
            transition: width 0.25s ease;
            border-left: 1px solid var(--border-color);
            background: var(--card-bg);
            display: flex; flex-direction: column;
        }
        .chpb-drawer.open { width: 480px; }
        .chpb-drawer-head {
            padding: 12px 16px; border-bottom: 1px solid var(--border-color);
            display: flex; justify-content: space-between; align-items: center;
        }
        .chpb-drawer-head h5 { margin: 0; font-size: 14px; font-weight: 600; }
        .chpb-drawer-head .close-btn { cursor: pointer; color: var(--text-muted); font-size: 18px; }
        .chpb-drawer-tabs {
            display: flex; border-bottom: 1px solid var(--border-color);
            padding: 0 8px;
        }
        .chpb-drawer-tabs .tab {
            padding: 8px 14px; font-size: 12px; font-weight: 600;
            cursor: pointer; color: var(--text-muted);
            border-bottom: 2px solid transparent; margin-bottom: -1px;
        }
        .chpb-drawer-tabs .tab.active { color: var(--blue-500); border-bottom-color: var(--blue-500); }
        .chpb-drawer-body { flex: 1; overflow-y: auto; padding: 14px; }
        .chpb-price-card {
            border: 1px solid var(--border-color); border-radius: 6px;
            padding: 10px 14px; margin-bottom: 10px;
        }
        .chpb-price-card .card-head {
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 8px;
        }
        .chpb-price-card .channel-label {
            font-weight: 700; font-size: 12px; color: var(--blue-600, #2490ef);
        }
        .chpb-price-row { display: flex; gap: 16px; flex-wrap: wrap; }
        .chpb-price-row .pval { flex: 1; min-width: 80px; }
        .chpb-price-row .pval label {
            font-size: 10px; text-transform: uppercase; color: var(--text-muted);
            font-weight: 600; display: block; margin-bottom: 2px;
        }
        .chpb-price-row .pval .amount {
            font-size: 15px; font-weight: 700; color: var(--text-color);
        }
        .chpb-offer-row {
            display: flex; justify-content: space-between; align-items: center;
            padding: 8px 12px; border: 1px solid var(--border-color);
            border-radius: 6px; margin-bottom: 8px; font-size: 12px;
        }
        .chpb-offer-row .offer-type {
            font-weight: 600; font-size: 11px; text-transform: uppercase;
            padding: 2px 8px; border-radius: 8px; background: var(--blue-50); color: var(--blue-600);
        }
        .chpb-history-row {
            padding: 6px 0; border-bottom: 1px solid var(--border-color);
            font-size: 12px;
        }
        .chpb-empty { text-align: center; color: var(--text-muted); padding: 24px; font-size: 12px; }
        .chpb-add-btn {
            width: 100%; text-align: left; font-size: 11px; font-weight: 600;
            color: var(--blue-500); background: none; border: 1px dashed var(--border-color);
            border-radius: 6px; padding: 6px 12px; cursor: pointer; margin-top: 6px;
        }
        .chpb-add-btn:hover { background: var(--blue-50); }
        .chpb-pagination {
            display: flex; align-items: center; gap: 10px; justify-content: center;
            padding: 8px; border-top: 1px solid var(--border-color);
            font-size: 12px;
        }
    `);

    // ── Build layout ──────────────────────────────────────────────────────
    const $wrap = $(`
      <div class="chpb-wrap">
        <div class="chpb-filters" id="chpb-filters"></div>
        <div class="chpb-stats" id="chpb-stats">
          <span id="chpb-count">Loading…</span>
          <div style="display:flex;gap:8px;">
            <button class="btn btn-xs btn-default" id="chpb-prev">‹ Prev</button>
            <span id="chpb-page-info"></span>
            <button class="btn btn-xs btn-default" id="chpb-next">Next ›</button>
          </div>
        </div>
        <div class="chpb-body">
          <div class="chpb-table-wrap" id="chpb-table-wrap">
            <div class="chpb-empty">Loading…</div>
          </div>
          <div class="chpb-drawer" id="chpb-drawer"></div>
        </div>
      </div>`).appendTo($(wrapper).find('.page-content'));

    // ── Build filter bar (Frappe link/autocomplete controls) ──────────────
    _build_filters($wrap, state, () => { state.page = 1; _load($wrap, state); });

    // ── Pagination ────────────────────────────────────────────────────────
    $wrap.find('#chpb-prev').on('click', () => {
        if (state.page > 1) { state.page--; _load($wrap, state); }
    });
    $wrap.find('#chpb-next').on('click', () => {
        const max = Math.ceil(state.data.total / state.page_length);
        if (state.page < max) { state.page++; _load($wrap, state); }
    });

    // ── Initial load ──────────────────────────────────────────────────────
    _load($wrap, state);
};


// ─── Filter bar ───────────────────────────────────────────────────────────────
function _build_filters($wrap, state, onchange) {
    const $bar = $wrap.find('#chpb-filters');

    const inputs = [
        { key: 'company',     label: 'Company',    type: 'link', doctype: 'Company' },
        { key: 'item_search', label: 'Search', type: 'text', placeholder: 'Item code / name…' },
        { key: 'category',    label: 'Category',    type: 'link', doctype: 'CH Category' },
        { key: 'sub_category',label: 'Sub Category',type: 'link', doctype: 'CH Sub Category' },
        { key: 'brand',       label: 'Brand',       type: 'link', doctype: 'Brand' },
        { key: 'model',       label: 'Model',       type: 'link', doctype: 'CH Model' },
        { key: 'channel',     label: 'Channel',     type: 'link', doctype: 'CH Price Channel' },
        { key: 'as_of_date',  label: 'As of Date',  type: 'date' },
        { key: 'tag_filter',  label: 'Tag', type: 'select',
          options: ['', 'EOL', 'FAST MOVING', 'SLOW MOVING', 'NEW', 'PROMO FOCUS', 'RESTRICTED'] },
        { key: 'price_status',label: 'Price Status', type: 'select',
          options: ['', 'Active', 'Scheduled', 'Expired', 'Draft'] },
    ];

    let debounce;
    inputs.forEach(inp => {
        const $grp = $(`<div class="filter-group"><label>${inp.label}</label></div>`).appendTo($bar);

        if (inp.type === 'text') {
            const $el = $(`<input class="form-control" placeholder="${inp.placeholder||''}" value="${state.filters[inp.key]||''}">`)
                .appendTo($grp)
                .on('input', function () {
                    state.filters[inp.key] = this.value;
                    clearTimeout(debounce);
                    debounce = setTimeout(onchange, 400);
                });
        } else if (inp.type === 'date') {
            $(`<input type="date" class="form-control" value="${state.filters[inp.key]||''}">`)
                .appendTo($grp)
                .on('change', function () { state.filters[inp.key] = this.value; onchange(); });
        } else if (inp.type === 'select') {
            const $sel = $(`<select class="form-control"></select>`).appendTo($grp);
            inp.options.forEach(o => $sel.append(`<option value="${o}">${o || 'All'}</option>`));
            $sel.val(state.filters[inp.key] || '');
            $sel.on('change', function () { state.filters[inp.key] = this.value; onchange(); });
        } else if (inp.type === 'link') {
            // Use Frappe's awesomplete link field
            const $el = $(`<input class="form-control" type="text" placeholder="${inp.label}…">`).appendTo($grp);
            frappe.utils.make_event_emitter($el[0]);
            const ctrl = new frappe.ui.form.ControlLink({
                parent: $grp[0],
                df: { label: inp.label, fieldname: inp.key, options: inp.doctype, fieldtype: 'Link' },
                only_input: true,
            });
            ctrl.value = state.filters[inp.key] || '';
            ctrl.refresh();
            ctrl.$input.css({ height: '28px', fontSize: '12px', minWidth: '110px', maxWidth: '150px' });
            ctrl.$input.on('change', function () {
                state.filters[inp.key] = this.value;
                onchange();
            });
            ctrl.$input.on('awesomplete-selectcomplete', function () {
                state.filters[inp.key] = this.value;
                onchange();
            });
            // Remove the auto-generated label since we add our own
            $grp.find('.control-label').remove();
            $el.remove();
            return;
        }
    });

    // Group by price specs toggle
    $(`<div class="filter-group" style="display:flex;align-items:flex-end;gap:6px">
       <label style="font-size:11px;white-space:nowrap"><input type="checkbox" id="chpb-group-toggle" checked style="margin-right:4px">Group variants</label>
     </div>`).appendTo($bar);
    $bar.find('#chpb-group-toggle').on('change', function() {
        state.group_by_price_specs = this.checked ? 1 : 0;
        onchange();
    });

    // Refresh button
    $(`<div class="filter-group"><label>&nbsp;</label>
       <button class="btn btn-sm btn-primary" id="chpb-refresh">⟳ Refresh</button>
     </div>`).appendTo($bar);
    $bar.find('#chpb-refresh').on('click', onchange);
}


// ─── Load data ────────────────────────────────────────────────────────────────
function _load($wrap, state) {
    if (state.loading) return;
    state.loading = true;
    $wrap.find('#chpb-table-wrap').html(`<div class="chpb-empty">
        <div class="spinner-border spinner-border-sm text-muted" role="status"></div>
        &nbsp; Loading…</div>`);

    frappe.call({
        method: 'ch_item_master.ch_item_master.ready_reckoner_api.get_ready_reckoner_data',
        args: {
            ...state.filters,
            page_length: state.page_length,
            page: state.page,
            group_by_price_specs: state.group_by_price_specs,
        },
        callback(r) {
            state.loading = false;
            state.data = r.message || { items: [], channels: [], total: 0 };
            _render_table($wrap, state);
            _update_stats($wrap, state);
        },
        error() { state.loading = false; },
    });
}


// ─── Render grid ──────────────────────────────────────────────────────────────
function _render_table($wrap, state) {
    const { items, channels } = state.data;

    if (!items.length) {
        $wrap.find('#chpb-table-wrap').html(`<div class="chpb-empty">No items found. Adjust filters and try again.</div>`);
        return;
    }

    let head = `<thead><tr>
        <th>Item Code</th>
        <th>Item Name</th>
        <th>Sub Category</th>
        <th>Brand</th>`;

    channels.forEach(ch => {
        head += `<th colspan="3" class="ch-group" style="text-align:center">${ch}</th>`;
    });
    head += `<th>Offers</th><th>Tags</th><th></th></tr>
    <tr>
        <th></th><th></th><th></th><th></th>`;
    channels.forEach(() => {
        head += `<th style="font-size:10px">MRP</th>
                 <th style="font-size:10px">MOP</th>
                 <th style="font-size:10px">Selling</th>`;
    });
    head += `<th></th><th></th><th></th></tr></thead>`;

    let rows = '';
    const esc = frappe.utils.escape_html;
    items.forEach(row => {
        const item_name = esc(row.item_name || '');
        rows += `<tr data-item="${esc(row.item_code)}" data-variant-count="${row.variant_count || 1}">
            <td class="item-code">${esc(row.item_code)}</td>
            <td class="item-name" title="${item_name}">${item_name}${row.variant_count > 1 ? ` <span class="badge badge-info" style="font-size:9px;vertical-align:middle">${row.variant_count} colors</span>` : ''}</td>
            <td style="font-size:11px">${esc((row.ch_sub_category||'').split('-').pop())}</td>
            <td style="font-size:11px">${esc(row.brand||'')}</td>`;

        channels.forEach(ch => {
            const mrp    = row[ch+'__mrp'];
            const mop    = row[ch+'__mop'];
            const sp     = row[ch+'__selling_price'];
            const pname  = row[ch+'__price_name'];
            const status = row[ch+'__status'];

            rows += _price_cell(mrp,  ch, 'mrp',           row.item_code, pname, status);
            rows += _price_cell(mop,  ch, 'mop',           row.item_code, pname, status);
            rows += _price_cell(sp,   ch, 'selling_price', row.item_code, pname, status);
        });

        const offerHtml = row.active_offer_label
            ? `<span class="offer-cell">${row.active_offer_label}</span>` : '—';

        const tagsHtml = (row.tags || '').split(',').filter(Boolean)
            .map(t => `<span class="chpb-tag ${t.trim()}">${t.trim()}</span>`).join('') || '—';

        rows += `<td class="offer-cell">${offerHtml}</td>
            <td class="tag-cell">${tagsHtml}</td>
            <td style="min-width:70px">
              <button class="btn btn-xs btn-default chpb-open-btn" data-item="${row.item_code}" title="Open details">⋯</button>
              <button class="btn btn-xs btn-primary chpb-add-price-btn" data-item="${row.item_code}" title="Add/Edit Price">₹</button>
            </td>
        </tr>`;
    });

    const $t = $(`<table class="chpb-table">${head}<tbody>${rows}</tbody></table>`);

    // ── Click handlers ────────────────────────────────────────────────────
    $t.find('.price-cell[data-price-name]').on('click', function () {
        const pname = $(this).data('price-name');
        frappe.set_route('Form', 'CH Item Price', pname);
    });

    $t.find('.price-cell.no-value').on('click', function (e) {
        e.stopPropagation();
        const item = $(this).data('item');
        const ch   = $(this).data('ch');
        const $row = $(this).closest('tr');
        const variant_count = parseInt($row.attr('data-variant-count') || '1');
        const is_grouped = variant_count > 1;
        _quick_price_dialog(item, ch, null, () => _load($wrap, state), is_grouped, variant_count);
    });

    $t.find('.chpb-open-btn').on('click', function (e) {
        e.stopPropagation();
        _open_drawer($wrap, state, $(this).data('item'));
    });

    $t.find('.chpb-add-price-btn').on('click', function (e) {
        e.stopPropagation();
        const $row = $(this).closest('tr');
        const variant_count = parseInt($row.attr('data-variant-count') || '1');
        const is_grouped = variant_count > 1;
        _quick_price_dialog($(this).data('item'), null, null, () => _load($wrap, state), is_grouped, variant_count);
    });

    $t.find('tr[data-item]').on('click', function () {
        $t.find('tr.selected').removeClass('selected');
        $(this).addClass('selected');
        _open_drawer($wrap, state, $(this).data('item'));
    });

    $wrap.find('#chpb-table-wrap').html('').append($t);
}

function _price_cell(val, ch, field, item_code, pname, status) {
    if (val) {
        const fmt = frappe.format(val, { fieldtype: 'Currency' });
        const badge = `<span class="${status}">${status||''}</span>`;
        return `<td class="price-cell has-value status-badge" data-ch="${ch}"
                    data-field="${field}" data-item="${item_code}" data-price-name="${pname}"
                    title="Click to open price record">${fmt}
                    <span class="edit-hint">✎</span>
                </td>`;
    }
    return `<td class="price-cell no-value" data-ch="${ch}" data-field="${field}"
                data-item="${item_code}" title="Click to add price for ${ch}">—
                <span class="edit-hint">+ add</span>
            </td>`;
}

function _update_stats($wrap, state) {
    const { total, page, page_length, items } = {
        total: state.data.total,
        page: state.page,
        page_length: state.page_length,
        items: state.data.items,
    };
    const from = (page - 1) * page_length + 1;
    const to   = Math.min(page * page_length, total);
    $wrap.find('#chpb-count').text(`Showing ${from}–${to} of ${total} items`);
    $wrap.find('#chpb-page-info').text(`Page ${page} / ${Math.ceil(total / page_length) || 1}`);
}


// ─── Quick price dialog ───────────────────────────────────────────────────────
function _quick_price_dialog(item_code, prefill_channel, prefill_price_name, on_success, is_grouped, variant_count) {
    if (prefill_price_name) {
        // Open the existing record
        frappe.set_route('Form', 'CH Item Price', prefill_price_name);
        return;
    }

    is_grouped = !!is_grouped;
    variant_count = variant_count || 1;

    if (is_grouped) {
        // Grouped view: always propagate, no need to fetch siblings
        _show_price_dialog(item_code, prefill_channel, on_success, {
            is_grouped: true,
            count: variant_count,
        });
    } else {
        // Ungrouped view: fetch sibling count to offer optional propagation
        frappe.call({
            method: 'ch_item_master.ch_item_master.ready_reckoner_api.get_sibling_items',
            args: { item_code },
            callback(r) {
                const info = r.message || { count: 1 };
                _show_price_dialog(item_code, prefill_channel, on_success, {
                    is_grouped: false,
                    count: info.count || 1,
                });
            },
            error() {
                _show_price_dialog(item_code, prefill_channel, on_success, {
                    is_grouped: false, count: 1,
                });
            }
        });
    }
}

function _show_price_dialog(item_code, prefill_channel, on_success, ctx) {
    const is_grouped = ctx.is_grouped;
    const sibling_count = ctx.count;
    const has_siblings = sibling_count > 1;

    const fields = [
        {
            fieldtype: 'Link', fieldname: 'channel', label: 'Channel',
            options: 'CH Price Channel', reqd: 1,
            default: prefill_channel,
        },
        { fieldtype: 'Column Break' },
        {
            fieldtype: 'Date', fieldname: 'effective_from', label: 'Effective From',
            reqd: 1, default: frappe.datetime.get_today(),
        },
        { fieldtype: 'Section Break', label: 'Prices' },
        { fieldtype: 'Currency', fieldname: 'mrp',           label: 'MRP' },
        { fieldtype: 'Currency', fieldname: 'mop',           label: 'MOP' },
        { fieldtype: 'Currency', fieldname: 'selling_price', label: 'Selling Price', reqd: 1 },
        { fieldtype: 'Section Break' },
        { fieldtype: 'Small Text', fieldname: 'notes', label: 'Notes / Reason' },
    ];

    if (is_grouped) {
        // Grouped mode — always propagate, just show an info message
        fields.push({ fieldtype: 'Section Break' });
        fields.push({
            fieldtype: 'HTML', fieldname: 'propagation_info',
            options: `<div class="text-muted" style="font-size:12px;padding:4px 0;">
                <span class="indicator-pill green" style="font-size:10px;padding:2px 8px">
                    Applies to all ${sibling_count} colour variants
                </span>
                <div style="margin-top:4px">To update a single colour, uncheck <b>Group variants</b> in the filter bar and edit the specific item.</div>
            </div>`,
        });
    } else if (has_siblings) {
        // Ungrouped mode with siblings — offer optional propagation (default OFF)
        fields.push({ fieldtype: 'Section Break', label: 'Price Propagation' });
        fields.push({
            fieldtype: 'Check',
            fieldname: 'propagate',
            label: __('Also apply to all {0} colour variants', [sibling_count]),
            default: 0,
            description: __('Check this to apply the same price to all colour variants with the same price specs'),
        });
    }

    const title = is_grouped
        ? __('Set Price — {0} ({1} colours)', [item_code, sibling_count])
        : __('Set Price — {0}', [item_code]);

    const d = new frappe.ui.Dialog({
        title: title,
        size: 'small',
        fields: fields,
        primary_action_label: __('Save Price'),
        primary_action(vals) {
            if (!vals) return;

            // Grouped mode → always propagate
            // Ungrouped mode → propagate only if user checks the box
            const should_propagate = is_grouped ? 1 : (has_siblings && vals.propagate ? 1 : 0);

            frappe.call({
                method: 'ch_item_master.ch_item_master.ready_reckoner_api.save_price_with_propagation',
                args: {
                    item_code,
                    channel:        vals.channel,
                    mrp:            vals.mrp || 0,
                    mop:            vals.mop || 0,
                    selling_price:  vals.selling_price,
                    effective_from: vals.effective_from,
                    notes:          vals.notes || '',
                    propagate:      should_propagate,
                    status:         'Active',
                },
                callback(r) {
                    d.hide();
                    const result = r.message || {};
                    const total = result.total_items || 1;
                    if (total > 1) {
                        frappe.show_alert({
                            message: __('Price applied to {0} colour variants', [total]),
                            indicator: 'green',
                        });
                    } else {
                        frappe.show_alert({ message: __('Price saved'), indicator: 'green' });
                    }
                    on_success && on_success();
                },
            });
        },
    });
    d.show();
}


// ─── Side Drawer ──────────────────────────────────────────────────────────────
function _open_drawer($wrap, state, item_code) {
    const $drawer = $wrap.find('#chpb-drawer');
    if ($drawer.data('item') === item_code && $drawer.hasClass('open')) {
        return; // Already open for this item
    }
    $drawer.data('item', item_code).addClass('open').html(`
        <div class="chpb-drawer-head">
          <h5 id="chpb-drawer-title">Loading…</h5>
          <span class="close-btn" id="chpb-drawer-close">×</span>
        </div>
        <div class="chpb-drawer-tabs">
          <div class="tab active" data-tab="prices">Prices</div>
          <div class="tab" data-tab="offers">Offers</div>
          <div class="tab" data-tab="warranty">Warranty</div>
          <div class="tab" data-tab="tags">Tags</div>
          <div class="tab" data-tab="history">History</div>
        </div>
        <div class="chpb-drawer-body" id="chpb-drawer-body">
          <div class="chpb-empty">Loading…</div>
        </div>`);

    $drawer.find('#chpb-drawer-close').on('click', () => {
        $drawer.removeClass('open').data('item', null);
    });

    $drawer.find('.tab').on('click', function () {
        $drawer.find('.tab').removeClass('active');
        $(this).addClass('active');
        _render_drawer_tab($drawer, $(this).data('tab'), item_code, state);
    });

    frappe.call({
        method: 'ch_item_master.ch_item_master.ready_reckoner_api.get_item_price_detail',
        args: { item_code, company: state.filters.company || '' },
        callback(r) {
            const detail = r.message || {};
            $drawer.data('detail', detail);
            $drawer.find('#chpb-drawer-title').text(item_code);
            _render_drawer_tab($drawer, 'prices', item_code, state);
        },
    });
}

function _render_drawer_tab($drawer, tab, item_code, state) {
    const detail = $drawer.data('detail') || {};
    const $body = $drawer.find('#chpb-drawer-body').empty();

    if (tab === 'prices') {
        const prices = detail.prices || [];
        if (!prices.length) {
            $body.html(`<div class="chpb-empty">No price records yet.</div>`);
        } else {
            prices.forEach(p => {
                const erpBadge = p.erp_item_price
                    ? `<a href="/app/item-price/${p.erp_item_price}" target="_blank"
                          style="font-size:10px;padding:1px 6px;border-radius:8px;
                          background:#d4edda;color:#155724;text-decoration:none;"
                          title="Synced to ERPNext Item Price">
                          ✓ ERP Synced ↗
                        </a>`
                    : `<span style="font-size:10px;padding:1px 6px;border-radius:8px;
                          background:#fff3cd;color:#856404;">
                          ⚠ Not synced
                        </span>`;
                const plBadge = p.price_list
                    ? `<span style="font-size:10px;color:var(--text-muted)">Price List: ${p.price_list}</span>`
                    : '';
                $body.append(`
                  <div class="chpb-price-card">
                    <div class="card-head">
                      <span class="channel-label">${p.channel}</span>
                      <span style="display:flex;align-items:center;gap:6px;">
                        <span class="chpb-tag ${p.status}">${p.status}</span>
                        ${erpBadge}
                        <a href="/app/ch-item-price/${p.name}" target="_blank" style="font-size:11px">CH ↗</a>
                      </span>
                    </div>
                    <div class="chpb-price-row">
                      ${_pval('MRP', p.mrp)} ${_pval('MOP', p.mop)} ${_pval('Selling', p.selling_price)}
                    </div>
                    <div style="font-size:10px;color:var(--text-muted);margin-top:6px;display:flex;justify-content:space-between">
                      <span>${p.effective_from} → ${p.effective_to || 'Open-ended'} &nbsp;|&nbsp; Updated by ${p.modified_by || '—'}</span>
                      ${plBadge}
                    </div>
                  </div>`);
            });
        }
        const $add = $(`<button class="chpb-add-btn">+ Add Price Record</button>`)
            .on('click', () => _quick_price_dialog(item_code, null, null, () => {
                $drawer.data('detail', null);
                _open_drawer($drawer.closest('.chpb-wrap'), state, item_code);
                _load($drawer.closest('.chpb-wrap'), state);
            }));
        $body.append($add);

    } else if (tab === 'offers') {
        const offers = detail.offers || [];
        if (!offers.length) $body.html(`<div class="chpb-empty">No offers yet.</div>`);
        offers.forEach(o => {
            const valStr = o.value_type === 'Percentage' ? `${o.value}%`
                : `₹${frappe.format(o.value, { fieldtype: 'Currency' })}`;
            const levelBadge = o.offer_level === 'Bill'
                ? `<span style="font-size:9px;padding:1px 5px;border-radius:6px;background:#e2d9f3;color:#4a235a;margin-left:4px">BILL</span>`
                : '';
            const erpPRLink = o.erp_pricing_rule
                ? `<a href="/app/pricing-rule/${o.erp_pricing_rule}" target="_blank"
                      style="font-size:10px;padding:1px 6px;border-radius:8px;
                      background:#d4edda;color:#155724;text-decoration:none;"
                      title="View ERPNext Pricing Rule">
                      ✓ Pricing Rule ↗
                    </a>`
                : `<span style="font-size:10px;padding:1px 6px;border-radius:8px;
                      background:#fff3cd;color:#856404;" title="Approve to create ERP Pricing Rule">
                      ⚠ Not synced
                    </span>`;
            $body.append(`
              <div class="chpb-offer-row">
                <div>
                  <div><strong>${o.offer_name}</strong>${levelBadge}</div>
                  <div style="font-size:11px;color:var(--text-muted)">${o.start_date} → ${o.end_date}</div>
                  ${o.bank_name ? `<div style="font-size:11px">Bank: ${o.bank_name} ${o.card_type||''}</div>` : ''}
                  <div style="margin-top:4px">${erpPRLink}</div>
                </div>
                <div style="text-align:right">
                  <span class="offer-type">${o.offer_type}</span><br>
                  <strong>${valStr}</strong><br>
                  <span class="chpb-tag ${o.status}">${o.status}</span>
                  <a href="/app/ch-item-offer/${o.name}" target="_blank" style="font-size:11px">↗</a>
                </div>
              </div>`);
        });
        $(`<button class="chpb-add-btn">+ Add Offer</button>`)
            .on('click', () => frappe.new_doc('CH Item Offer', { item_code })).appendTo($body);

    } else if (tab === 'warranty') {
        // Fetch applicable warranty plans for this item
        const item_group = (state.data.items || []).find(i => i.item_code === item_code)?.item_group || '';
        frappe.call({
            method: 'frappe.client.get_list',
            args: {
                doctype: 'CH Warranty Plan',
                filters: { status: 'Active' },
                fields: ['name', 'plan_name', 'plan_type', 'service_item', 'price',
                         'pricing_mode', 'duration_months', 'attach_level', 'coverage_description'],
                limit_page_length: 20,
            },
            callback(r) {
                const plans = r.message || [];
                if (!plans.length) {
                    $body.html(`<div class="chpb-empty">No warranty/VAS plans configured.</div>`);
                } else {
                    plans.forEach(wp => {
                        const typeColors = {
                            'Own Warranty': '#d4edda;color:#155724',
                            'Extended Warranty': '#cce5ff;color:#004085',
                            'Value Added Service': '#e2d9f3;color:#4a235a',
                            'Protection Plan': '#fff3cd;color:#856404',
                        };
                        const bgCol = typeColors[wp.plan_type] || '#f0f0f0;color:#333';
                        $body.append(`
                          <div class="chpb-offer-row">
                            <div>
                              <div><strong>${wp.plan_name}</strong></div>
                              <div style="font-size:11px;color:var(--text-muted)">
                                ${wp.duration_months ? wp.duration_months + ' months' : 'One-time'}
                                &nbsp;|&nbsp; ${wp.attach_level}
                              </div>
                              ${wp.coverage_description ? `<div style="font-size:11px;margin-top:4px">${wp.coverage_description}</div>` : ''}
                            </div>
                            <div style="text-align:right">
                              <span style="padding:2px 8px;border-radius:8px;font-size:10px;font-weight:600;background:${bgCol}">${wp.plan_type}</span><br>
                              <strong>₹${frappe.format(wp.price, {fieldtype:'Currency'})}</strong><br>
                              <a href="/app/ch-warranty-plan/${wp.name}" target="_blank" style="font-size:11px">↗</a>
                            </div>
                          </div>`);
                    });
                }
                $(`<button class="chpb-add-btn">+ Add Warranty Plan</button>`)
                    .on('click', () => frappe.new_doc('CH Warranty Plan')).appendTo($body);
            },
        });

    } else if (tab === 'tags') {
        const tags = detail.tags || [];
        if (!tags.length) $body.html(`<div class="chpb-empty">No commercial tags.</div>`);
        tags.forEach(t => {
            $body.append(`
              <div class="chpb-offer-row">
                <div>
                  <span class="chpb-tag ${t.tag}">${t.tag}</span>
                  <div style="font-size:11px;color:var(--text-muted);margin-top:4px">
                    ${t.effective_from||'—'} → ${t.effective_to||'Open'} &nbsp;|&nbsp;
                    <span class="chpb-tag ${t.status}">${t.status}</span>
                  </div>
                  ${t.reason ? `<div style="font-size:11px">${t.reason}</div>` : ''}
                </div>
                <a href="/app/ch-item-commercial-tag/${t.name}" target="_blank" style="font-size:11px">↗</a>
              </div>`);
        });
        $(`<button class="chpb-add-btn">+ Add Tag</button>`)
            .on('click', () => frappe.new_doc('CH Item Commercial Tag', { item_code })).appendTo($body);

    } else if (tab === 'history') {
        const hist = detail.history || [];
        if (!hist.length) $body.html(`<div class="chpb-empty">No history yet.</div>`);
        hist.forEach(h => {
            $body.append(`
              <div class="chpb-history-row">
                <strong>${h.ref_doctype} — ${h.docname}</strong><br>
                <span style="font-size:11px;color:var(--text-muted)">${h.owner} · ${frappe.datetime.str_to_user(h.creation)}</span>
              </div>`);
        });
    }
}

function _pval(label, val) {
    const fmt = val ? frappe.format(val, { fieldtype: 'Currency' }) : '—';
    return `<div class="pval"><label>${label}</label><div class="amount">${fmt}</div></div>`;
}


// ─── Excel export ─────────────────────────────────────────────────────────────
function chpb_export(state) {
    const args = { ...state.filters };
    const url = `/api/method/ch_item_master.ch_item_master.ready_reckoner_api.export_ready_reckoner?`
        + Object.entries(args).filter(([, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
    window.open(url, '_blank');
}
