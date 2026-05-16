frappe.pages['location-hierarchy'].on_page_load = function(wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Location Hierarchy'),
		single_column: true
	});

	const view = new LocationHierarchyView(page);
	view.render();
};

class LocationHierarchyView {
	constructor(page) {
		this.page = page;
		this.company = null;
		this.warehouse_view = 'all';
		this.tree = [];
		this.companies = [];

		this.$container = $(`<div class="location-hierarchy-page"></div>`).appendTo(page.body);
		this.inject_styles();
		this.setup_toolbar();
	}

	inject_styles() {
		if (document.getElementById('location-hierarchy-styles')) return;
		const css = `
		.location-hierarchy-page { padding: 8px 4px; }
		.lh-company { border:1px solid var(--border-color); border-radius:8px; margin-bottom:14px; background:var(--card-bg); }
		.lh-company-header { padding:10px 14px; border-bottom:1px solid var(--border-color); display:flex; justify-content:space-between; align-items:center; background:var(--bg-light-gray); border-radius:8px 8px 0 0;}
		.lh-company-title { font-weight:600; font-size:14px; }
		.lh-city { border-top:1px solid var(--border-color); padding:8px 14px; }
		.lh-city:first-child { border-top:none; }
		.lh-city-header { display:flex; justify-content:space-between; align-items:center; cursor:pointer; }
		.lh-city-title { font-weight:600; }
		.lh-zone { margin:8px 0 8px 18px; padding:8px 12px; border:1px dashed var(--border-color); border-radius:6px; background:var(--fg-color); }
		.lh-zone-header { display:flex; justify-content:space-between; align-items:center; gap:6px; }
		.lh-zone-title { font-weight:600; }
		.lh-zone-meta { color:var(--text-muted); font-size:12px; margin-left:6px; }
		.lh-section-title { font-weight:500; font-size:12px; text-transform:uppercase; color:var(--text-muted); margin:8px 0 4px; letter-spacing:0.4px;}
		.lh-pill { display:inline-flex; align-items:center; gap:4px; background:var(--bg-light-gray); border:1px solid var(--border-color); padding:2px 8px; border-radius:14px; margin:2px 4px 2px 0; font-size:12px;}
		.lh-pill .lh-actions { display:none; margin-left:4px; }
		.lh-pill:hover .lh-actions { display:inline-flex; gap:4px;}
		.lh-pill .btn-link { padding:0; font-size:11px; }
		.lh-pill.lh-store { background:#eaf6ff; }
		.lh-pill.lh-warehouse { background:#fff7e6; }
		.lh-pill.lh-warehouse-zone { background:#ffe6cc; font-weight:500;}
		.lh-pill.lh-warehouse-hub { background:#ffd9b3; font-weight:600; border-color:#e69500;}
		.lh-pill.lh-warehouse-bin { background:#f2f2f2; color:var(--text-muted); font-size:11px;}
		.lh-pill.lh-office { background:#eaffea; }
		.lh-pill.lh-empty { background:transparent; border:1px dashed var(--border-color); color:var(--text-muted);}
		.lh-bins-toggle { cursor:pointer; color:var(--text-muted); font-size:11px; user-select:none;}
		.lh-bins-toggle:hover { color:var(--primary);}
		.lh-bins-collapsed .lh-bins-body { display:none;}
		.lh-actions a { margin-left:6px; font-size:12px; }
		.lh-empty-state { padding:40px; text-align:center; color:var(--text-muted); }
		.lh-add-btn { font-size:12px; }
		.lh-row { display:flex; flex-wrap:wrap; align-items:center; gap:4px;}
		`;
		const style = document.createElement('style');
		style.id = 'location-hierarchy-styles';
		style.innerHTML = css;
		document.head.appendChild(style);
	}

	setup_toolbar() {
		this.page.set_primary_action(__('Add City'), () => this.add_city(), 'add');
		this.page.add_menu_item(__('Add Zone'), () => this.add_zone());
		this.page.add_menu_item(__('Assign Warehouse'), () => this.assign_warehouse_dialog());
		this.page.add_menu_item(__('Assign Office'), () => this.assign_office_dialog());
		this.page.add_menu_item(__('Add Office (Branch)'), () => this.create_office_dialog());
		this.page.add_menu_item(__('Refresh'), () => this.render());

		this.company_field = this.page.add_field({
			label: __('Company'),
			fieldtype: 'Link',
			fieldname: 'company',
			options: 'Company',
			change: () => {
				this.company = this.company_field.get_value();
				this.render();
			}
		});

		this.warehouse_view_field = this.page.add_field({
			label: __('Show'),
			fieldtype: 'Select',
			fieldname: 'warehouse_view',
			options: [
				{ label: __('All warehouses'),         value: 'all' },
				{ label: __('Hubs & store warehouses'), value: 'location' },
				{ label: __('Operational only'),       value: 'operational' },
			],
			default: 'all',
			change: () => {
				this.warehouse_view = this.warehouse_view_field.get_value() || 'all';
				this.render();
			}
		});
	}

	async render() {
		this.$container.html('<div class="text-muted" style="padding:30px;">Loading…</div>');
		const r = await frappe.call({
			method: 'ch_item_master.ch_core.location_hierarchy.get_company_location_tree',
			args: { company: this.company || null, warehouse_view: this.warehouse_view || 'all' }
		});
		this.tree = r.message || [];
		this.draw();
	}

	draw() {
		if (!this.tree.length) {
			this.$container.html(`<div class="lh-empty-state">
				<h4>${__('No location data yet')}</h4>
				<p>${__('Start by adding a city, then add zones and assign warehouses / stores / offices.')}</p>
				<button class="btn btn-primary btn-sm" id="lh-first-city">${__('Add First City')}</button>
			</div>`);
			this.$container.find('#lh-first-city').on('click', () => this.add_city());
			return;
		}

		this.$container.empty();
		for (const company of this.tree) {
			const $c = $(`<div class="lh-company"></div>`).appendTo(this.$container);
			$(`<div class="lh-company-header">
				<div class="lh-company-title"><i class="fa fa-building"></i> ${frappe.utils.escape_html(company.company)}</div>
				<div>
					<button class="btn btn-xs btn-default lh-add-btn" data-act="add-city">+ ${__('City')}</button>
				</div>
			</div>`).appendTo($c).find('[data-act="add-city"]').on('click', () => this.add_city(company.company));

			const $body = $(`<div></div>`).appendTo($c);
			if (!company.cities.length) {
				$body.append(`<div class="lh-city text-muted">${__('No cities yet')}</div>`);
				continue;
			}
			for (const city of company.cities) {
				this.draw_city($body, company.company, city);
			}
		}
	}

	draw_city($parent, company, city) {
		const $city = $(`<div class="lh-city"></div>`).appendTo($parent);
		const $header = $(`<div class="lh-city-header">
			<div class="lh-city-title"><i class="fa fa-map-marker"></i> ${frappe.utils.escape_html(city.city_name)} <span class="lh-zone-meta">${city.state || ''}</span></div>
			<div class="lh-actions">
				<button class="btn btn-xs btn-default" data-act="add-zone">+ ${__('Zone')}</button>
				<a href="#" data-act="edit-city">${__('Edit')}</a>
				<a href="#" data-act="del-city" class="text-danger">${__('Delete')}</a>
			</div>
		</div>`).appendTo($city);

		$header.find('[data-act="add-zone"]').on('click', () => this.add_zone(company, city.city));
		$header.find('[data-act="edit-city"]').on('click', (e) => { e.preventDefault(); this.edit_city(city.city); });
		$header.find('[data-act="del-city"]').on('click', (e) => { e.preventDefault(); this.delete_city(city.city); });

		const $zones = $(`<div></div>`).appendTo($city);
		if (!city.zones.length) {
			$zones.append(`<div class="text-muted" style="margin-left:18px;">${__('No zones')}</div>`);
			return;
		}
		for (const zone of city.zones) {
			this.draw_zone($zones, company, city, zone);
		}
	}

	draw_zone($parent, company, city, zone) {
		const $z = $(`<div class="lh-zone"></div>`).appendTo($parent);
		const isSynthetic = zone.zone === 'Unassigned';
		$(`<div class="lh-zone-header">
			<div>
				<span class="lh-zone-title"><i class="fa fa-th-large"></i> ${frappe.utils.escape_html(zone.zone_name || zone.zone)}</span>
				<span class="lh-zone-meta">${zone.source_warehouse ? 'Source: ' + frappe.utils.escape_html(zone.source_warehouse) : ''}</span>
			</div>
			${isSynthetic ? '' : `<div class="lh-actions">
				<a href="#" data-act="edit-zone">${__('Edit')}</a>
				<a href="#" data-act="del-zone" class="text-danger">${__('Delete')}</a>
			</div>`}
		</div>`).appendTo($z);

		if (!isSynthetic) {
			$z.find('[data-act="edit-zone"]').on('click', (e) => { e.preventDefault(); this.edit_zone(zone.zone, company, city.city); });
			$z.find('[data-act="del-zone"]').on('click', (e) => { e.preventDefault(); this.delete_zone(zone.zone); });
		} else {
			$(`<div class="alert alert-warning" style="font-size:12px;margin:6px 0 4px;padding:6px 10px;">
				<i class="fa fa-exclamation-triangle"></i>
				${__('These items have no zone assigned. Use <b>→ Assign Zone</b> on each item to move them to the correct zone.')}
			</div>`).appendTo($z);
		}

		// Split warehouses by retail role (ch_location_type).
		// "Hub" / "DC" sit at zone level (back-end); "Store Warehouse" is
		// the customer-facing outlet container; "Store Bin" is the leaf
		// stock-state bucket (Sellable/Reserved/...). Everything else is
		// labelled generically.
		const buckets = { hub: [], store: [], bin: [], other: [] };
		for (const w of zone.warehouses) {
			const t = (w.ch_location_type || '').trim();
			if (t === 'Zone Warehouse' || t === 'Transit Warehouse' || t === 'Service Warehouse') {
				buckets.hub.push(w);
			} else if (t === 'Store Warehouse') {
				buckets.store.push(w);
			} else if (t === 'Store Bin') {
				buckets.bin.push(w);
			} else {
				buckets.other.push(w);
			}
		}

		// ── Distribution Hub ─────────────────────────────────────────
		const assignHubLink = isSynthetic ? '' : `<a href="#" data-act="assign-wh-hub" class="lh-add-btn"> + ${__('Assign')}</a>`;
		const $hSec = $(`<div><div class="lh-section-title">${__('Distribution Hub')}${assignHubLink}</div><div class="lh-row"></div></div>`).appendTo($z);
		const $hRow = $hSec.find('.lh-row');
		if (!isSynthetic) $hSec.find('[data-act="assign-wh-hub"]').on('click', (e) => { e.preventDefault(); this.assign_warehouse_dialog(company, city.city, zone.zone); });
		if (!buckets.hub.length) {
			$hRow.append(`<span class="lh-pill lh-empty">${__('None')}</span>`);
		}
		for (const w of buckets.hub) {
			this._draw_warehouse_pill($hRow, w, 'lh-warehouse-hub', company, city, zone);
		}

		// ── Store Warehouses (customer-facing outlets) ───────────────
		// Render only when present — the "Stores" section below (driven by
		// CH Store records) already lists outlets for most users.
		if (buckets.store.length) {
			const $swSec = $(`<div><div class="lh-section-title">${__('Store Warehouses')}</div><div class="lh-row"></div></div>`).appendTo($z);
			const $swRow = $swSec.find('.lh-row');
			for (const w of buckets.store) {
				this._draw_warehouse_pill($swRow, w, 'lh-warehouse', company, city, zone);
			}
		}

		// ── Stock Bins (collapsed by default) ────────────────────────
		if (buckets.bin.length) {
			const $bWrap = $(`<div class="lh-bins-collapsed"></div>`).appendTo($z);
			const $bHdr = $(`<div class="lh-section-title">
				<span class="lh-bins-toggle">▸ ${__('Stock Bins')} (${buckets.bin.length})</span>
			</div>`).appendTo($bWrap);
			const $bBody = $(`<div class="lh-bins-body lh-row"></div>`).appendTo($bWrap);
			$bHdr.find('.lh-bins-toggle').on('click', () => {
				$bWrap.toggleClass('lh-bins-collapsed');
				$bHdr.find('.lh-bins-toggle').text(
					($bWrap.hasClass('lh-bins-collapsed') ? '▸ ' : '▾ ')
					+ __('Stock Bins') + ` (${buckets.bin.length})`
				);
			});
			for (const w of buckets.bin) {
				this._draw_warehouse_pill($bBody, w, 'lh-warehouse-bin', company, city, zone);
			}
		}

		// ── Other / unclassified warehouses ──────────────────────────
		if (buckets.other.length) {
			const $oSec = $(`<div><div class="lh-section-title">${__('Other Warehouses')}
				<a href="#" data-act="assign-wh-other" class="lh-add-btn"> + ${__('Assign')}</a></div><div class="lh-row"></div></div>`).appendTo($z);
			const $oWhRow = $oSec.find('.lh-row');
			$oSec.find('[data-act="assign-wh-other"]').on('click', (e) => { e.preventDefault(); this.assign_warehouse_dialog(company, city.city, zone.zone); });
			for (const w of buckets.other) {
				this._draw_warehouse_pill($oWhRow, w, 'lh-warehouse', company, city, zone);
			}
		}

		// Stores
		const addStoreLink = isSynthetic ? '' : `<a href="#" data-act="add-store" class="lh-add-btn"> + ${__('Add')}</a>`;
		const $sSec = $(`<div><div class="lh-section-title">${__('Stores')}${addStoreLink}</div><div class="lh-row"></div></div>`).appendTo($z);
		const $sRow = $sSec.find('.lh-row');
		if (!isSynthetic) $sSec.find('[data-act="add-store"]').on('click', (e) => { e.preventDefault(); this.add_store_dialog(company, city.city, zone.zone); });
		if (!zone.stores.length) {
			$sRow.append(`<span class="lh-pill lh-empty">${__('None')}</span>`);
		}
		for (const s of zone.stores) {
			const storeActions = isSynthetic
				? `<a href="#" data-act="move-store" class="btn-link text-primary">${__('→ Assign Zone')}</a>`
				: `<a href="#" data-act="del-store" class="btn-link text-danger">×</a>`;
			const $p = $(`<span class="lh-pill lh-store">
				<a href="/app/ch-store/${encodeURIComponent(s.name)}" target="_blank">${frappe.utils.escape_html(s.store_code || s.name)}</a>
				<small class="text-muted">${s.store_name ? '· ' + s.store_name : ''}</small>
				<span class="lh-actions">${storeActions}</span>
			</span>`).appendTo($sRow);
			if (isSynthetic) {
				$p.find('[data-act="move-store"]').on('click', (e) => { e.preventDefault(); this.move_store_zone_dialog(s, company); });
			} else {
				$p.find('[data-act="del-store"]').on('click', (e) => { e.preventDefault(); this.delete_store(s.name); });
			}
		}

		// Offices
		const assignOfficeLink = isSynthetic ? '' : `<a href="#" data-act="assign-office" class="lh-add-btn"> + ${__('Assign')}</a>`;
		const $oSec = $(`<div><div class="lh-section-title">${__('Offices')}${assignOfficeLink}</div><div class="lh-row"></div></div>`).appendTo($z);
		const $oRow = $oSec.find('.lh-row');
		if (!isSynthetic) $oSec.find('[data-act="assign-office"]').on('click', (e) => { e.preventDefault(); this.assign_office_dialog(company, city.city, zone.zone); });
		if (!zone.offices.length) {
			$oRow.append(`<span class="lh-pill lh-empty">${__('None')}</span>`);
		}
		for (const o of zone.offices) {
			const $p = $(`<span class="lh-pill lh-office">
				<a href="/app/branch/${encodeURIComponent(o.name)}" target="_blank">${frappe.utils.escape_html(o.branch || o.name)}</a>
				<span class="lh-actions">
					<a href="#" data-act="unassign-office" class="btn-link text-danger">×</a>
				</span>
			</span>`).appendTo($oRow);
			$p.find('[data-act="unassign-office"]').on('click', (e) => { e.preventDefault(); this.unassign_office(o.name); });
		}
	}

	_draw_warehouse_pill($parent, w, extraClass, company, city, zone) {
		const isSynthetic = zone.zone === 'Unassigned';
		const typeLbl = w.ch_location_type ? '· ' + w.ch_location_type : '';
		const editLabel = isSynthetic ? __('→ Assign Zone') : __('Edit');
		const $p = $(`<span class="lh-pill ${extraClass}">
			<a href="/app/warehouse/${encodeURIComponent(w.name)}" target="_blank">${frappe.utils.escape_html(w.warehouse_name || w.name)}</a>
			<small class="text-muted">${frappe.utils.escape_html(typeLbl)}</small>
			<span class="lh-actions">
				<a href="#" data-act="edit-wh" class="btn-link${isSynthetic ? ' text-primary' : ''}">${editLabel}</a>
				${isSynthetic ? '' : '<a href="#" data-act="unassign-wh" class="btn-link text-danger">×</a>'}
			</span>
		</span>`).appendTo($parent);
		$p.find('[data-act="edit-wh"]').on('click', (e) => { e.preventDefault(); this.assign_warehouse_dialog(company, city.city, zone.zone, w); });
		if (!isSynthetic) $p.find('[data-act="unassign-wh"]').on('click', (e) => { e.preventDefault(); this.unassign_warehouse(w.name); });
		return $p;
	}

	// ----------------- Dialogs -----------------

	add_city(company) {
		this._city_dialog({ company: company || this.company }, !!company);
	}
	edit_city(name) {
		frappe.db.get_doc('CH City', name).then(doc => this._city_dialog(doc, false));
	}
	_city_dialog(doc, lockCompany) {
		const ctxHTML = lockCompany ? `<div class="text-muted small" style="margin:-4px 0 8px;"><b>Company:</b> ${frappe.utils.escape_html(doc.company)}</div>` : '';
		const fields = lockCompany ? [
			{ fieldtype: 'HTML', options: ctxHTML },
			{ fieldtype: 'Data', fieldname: 'city_name', label: 'City Name', reqd: 1, default: doc.city_name },
			{ fieldtype: 'Data', fieldname: 'state', label: 'State', default: doc.state },
		] : [
			{ fieldtype: 'Link', fieldname: 'company', label: 'Company', options: 'Company', reqd: 1, default: doc.company || this.company },
			{ fieldtype: 'Data', fieldname: 'city_name', label: 'City Name', reqd: 1, default: doc.city_name },
			{ fieldtype: 'Data', fieldname: 'state', label: 'State', default: doc.state },
			{ fieldtype: 'Check', fieldname: 'disabled', label: 'Disabled', default: doc.disabled || 0 },
			{ fieldtype: 'Small Text', fieldname: 'description', label: 'Description', default: doc.description },
		];
		const d = new frappe.ui.Dialog({
			title: doc && doc.name ? __('Edit City') : __('Add City'),
			fields,
			primary_action_label: __('Save'),
			primary_action: (v) => {
				const args = lockCompany ? { company: doc.company, city_name: v.city_name, state: v.state, name: doc.name || null }
					: { ...v, name: doc.name || null };
				frappe.call({
					method: 'ch_item_master.ch_core.location_hierarchy.save_city',
					args,
					callback: () => { d.hide(); frappe.show_alert({message: __('City saved'), indicator:'green'}); this.render(); }
				});
			}
		});
		d.show();
	}
	delete_city(name) {
		frappe.confirm(__('Delete city {0}?', [name]), () => {
			frappe.call({
				method: 'ch_item_master.ch_core.location_hierarchy.delete_city',
				args: { name },
				callback: () => { frappe.show_alert({message: __('Deleted'), indicator:'red'}); this.render(); }
			});
		});
	}

	add_zone(company, city) {
		this._zone_dialog({ company: company || this.company, city }, !!(company && city));
	}
	edit_zone(name) {
		frappe.db.get_doc('CH Store Zone', name).then(doc => this._zone_dialog(doc, false));
	}
	_zone_dialog(doc, lockContext) {
		const ctxHTML = lockContext ? `<div class="text-muted small" style="margin:-4px 0 8px;">
			<b>Company:</b> ${frappe.utils.escape_html(doc.company)} &nbsp;·&nbsp; 
			<b>City:</b> ${frappe.utils.escape_html(doc.city)}
		</div>` : '';
		const fields = lockContext ? [
			{ fieldtype: 'HTML', options: ctxHTML },
			{ fieldtype: 'Data', fieldname: 'zone_name', label: 'Zone Name', reqd: 1 },
			{ fieldtype: 'Link', fieldname: 'source_warehouse', label: 'Source Warehouse', options: 'Warehouse',
				get_query: () => ({ filters: { company: doc.company, is_group: 0 } }) },
		] : [
			{ fieldtype: 'Link', fieldname: 'company', label: 'Company', options: 'Company', reqd: 1, default: doc.company || this.company },
			{ fieldtype: 'Link', fieldname: 'city', label: 'City', options: 'CH City', reqd: 1, default: doc.city,
				get_query: () => ({ filters: { company: d.get_value('company') } }) },
			{ fieldtype: 'Data', fieldname: 'zone_name', label: 'Zone Name', reqd: 1, default: doc.zone_name },
			{ fieldtype: 'Link', fieldname: 'source_warehouse', label: 'Source Warehouse', options: 'Warehouse', default: doc.source_warehouse,
				get_query: () => ({ filters: { company: d.get_value('company'), is_group: 0 } }) },
		];
		const d = new frappe.ui.Dialog({
			title: doc.name ? __('Edit Zone') : __('Add Zone'),
			fields,
			primary_action_label: __('Save'),
			primary_action: (v) => {
				const args = lockContext
					? { company: doc.company, city: doc.city, zone_name: v.zone_name, source_warehouse: v.source_warehouse, name: doc.name || null }
					: { ...v, name: doc.name || null };
				frappe.call({
					method: 'ch_item_master.ch_core.location_hierarchy.save_zone',
					args,
					callback: () => { d.hide(); frappe.show_alert({message: __('Zone saved'), indicator:'green'}); this.render(); }
				});
			}
		});
		d.show();
	}
	delete_zone(name) {
		frappe.confirm(__('Delete zone {0}?', [name]), () => {
			frappe.call({
				method: 'ch_item_master.ch_core.location_hierarchy.delete_zone',
				args: { name },
				callback: () => { frappe.show_alert({message: __('Deleted'), indicator:'red'}); this.render(); }
			});
		});
	}

	assign_warehouse_dialog(company, city, zone, existing) {
		const e = existing || {};
		const hasContext = !!(company && city && zone) && !existing;
		const ctxHTML = hasContext ? `<div class="text-muted small" style="margin:-4px 0 8px;">
			<b>Company:</b> ${frappe.utils.escape_html(company)} &nbsp;·&nbsp; 
			<b>City:</b> ${frappe.utils.escape_html(city)} &nbsp;·&nbsp; 
			<b>Zone:</b> ${frappe.utils.escape_html(zone)}
		</div>` : '';

		const fields = [];
		if (hasContext) {
			fields.push({ fieldtype: 'HTML', options: ctxHTML });
			fields.push({ fieldtype: 'Link', fieldname: 'warehouse', label: 'Warehouse', options: 'Warehouse', reqd: 1,
				get_query: () => ({ filters: { is_group: 0, company } }) });
			fields.push({ fieldtype: 'Select', fieldname: 'location_type', label: 'Location Type',
				options: 'Store Warehouse\nZone Warehouse\nTransit Warehouse\nService Warehouse\nOther',
				default: 'Store Warehouse' });
		} else {
			fields.push({ fieldtype: 'Link', fieldname: 'warehouse', label: 'Warehouse', options: 'Warehouse', reqd: 1,
				default: e.name,
				get_query: () => ({ filters: { is_group: 0, company: d.get_value('company') || undefined } }) });
			fields.push({ fieldtype: 'Link', fieldname: 'company', label: 'Company', options: 'Company', reqd: 1, default: e.company || this.company });
			fields.push({ fieldtype: 'Link', fieldname: 'city', label: 'City', options: 'CH City', default: e.ch_city,
				get_query: () => ({ filters: { company: d.get_value('company') } }) });
			fields.push({ fieldtype: 'Link', fieldname: 'zone', label: 'Zone', options: 'CH Store Zone', default: e.ch_zone,
				get_query: () => ({ filters: { company: d.get_value('company'), city: d.get_value('city') } }) });
			fields.push({ fieldtype: 'Select', fieldname: 'location_type', label: 'Location Type',
				options: '\nStore Warehouse\nZone Warehouse\nTransit Warehouse\nService Warehouse\nOther',
				default: e.ch_location_type });
		}

		const d = new frappe.ui.Dialog({
			title: existing ? __('Edit Warehouse Assignment') : __('Assign Warehouse'),
			fields,
			primary_action_label: __('Save'),
			primary_action: (v) => {
				const args = hasContext ? { warehouse: v.warehouse, company, city, zone, location_type: v.location_type } : v;
				frappe.call({
					method: 'ch_item_master.ch_core.location_hierarchy.assign_warehouse',
					args,
					callback: () => { d.hide(); frappe.show_alert({message: __('Warehouse assigned'), indicator:'green'}); this.render(); }
				});
			}
		});
		d.show();
	}
	unassign_warehouse(name) {
		frappe.confirm(__('Remove warehouse {0} from this zone?', [name]), () => {
			frappe.call({
				method: 'ch_item_master.ch_core.location_hierarchy.unassign_warehouse',
				args: { warehouse: name },
				callback: () => { this.render(); }
			});
		});
	}

	assign_office_dialog(company, city, zone) {
		const hasContext = !!(company && city && zone);
		const ctxHTML = hasContext ? `<div class="text-muted small" style="margin:-4px 0 8px;">
			<b>Company:</b> ${frappe.utils.escape_html(company)} &nbsp;·&nbsp; 
			<b>City:</b> ${frappe.utils.escape_html(city)} &nbsp;·&nbsp; 
			<b>Zone:</b> ${frappe.utils.escape_html(zone)}
		</div>` : '';

		const fields = hasContext ? [
			{ fieldtype: 'HTML', options: ctxHTML },
			{ fieldtype: 'Link', fieldname: 'branch', label: 'Branch / Office', options: 'Branch', reqd: 1 },
		] : [
			{ fieldtype: 'Link', fieldname: 'branch', label: 'Branch', options: 'Branch', reqd: 1 },
			{ fieldtype: 'Link', fieldname: 'company', label: 'Company', options: 'Company', reqd: 1, default: this.company },
			{ fieldtype: 'Link', fieldname: 'city', label: 'City', options: 'CH City',
				get_query: () => ({ filters: { company: d.get_value('company') } }) },
			{ fieldtype: 'Link', fieldname: 'zone', label: 'Zone', options: 'CH Store Zone',
				get_query: () => ({ filters: { company: d.get_value('company'), city: d.get_value('city') } }) },
		];

		const d = new frappe.ui.Dialog({
			title: __('Assign Office'),
			fields,
			primary_action_label: __('Save'),
			primary_action: (v) => {
				const args = hasContext ? { branch: v.branch, company, city, zone } : v;
				frappe.call({
					method: 'ch_item_master.ch_core.location_hierarchy.assign_office',
					args,
					callback: () => { d.hide(); frappe.show_alert({message: __('Office assigned'), indicator:'green'}); this.render(); }
				});
			}
		});
		d.show();
	}
	unassign_office(branch) {
		frappe.confirm(__('Remove office {0} from this zone?', [branch]), () => {
			frappe.call({
				method: 'ch_item_master.ch_core.location_hierarchy.unassign_office',
				args: { branch },
				callback: () => { this.render(); }
			});
		});
	}
	create_office_dialog() {
		const d = new frappe.ui.Dialog({
			title: __('Add Office (Branch)'),
			fields: [
				{ fieldtype: 'Data', fieldname: 'branch', label: 'Branch Name', reqd: 1 },
				{ fieldtype: 'Link', fieldname: 'company', label: 'Company', options: 'Company', reqd: 1, default: this.company },
				{ fieldtype: 'Link', fieldname: 'city', label: 'City', options: 'CH City',
					get_query: () => ({ filters: { company: d.get_value('company') } }) },
				{ fieldtype: 'Link', fieldname: 'zone', label: 'Zone', options: 'CH Store Zone',
					get_query: () => ({ filters: { company: d.get_value('company'), city: d.get_value('city') } }) },
			],
			primary_action_label: __('Create'),
			primary_action: (v) => {
				frappe.call({
					method: 'ch_item_master.ch_core.location_hierarchy.create_office',
					args: v,
					callback: () => { d.hide(); frappe.show_alert({message: __('Office created'), indicator:'green'}); this.render(); }
				});
			}
		});
		d.show();
	}

	add_store_dialog(company, city, zone) {
		const hasContext = !!(company && city && zone);
		const ctxHTML = hasContext ? `<div class="text-muted small" style="margin:-4px 0 8px;">
			<b>Company:</b> ${frappe.utils.escape_html(company)} &nbsp;·&nbsp; 
			<b>City:</b> ${frappe.utils.escape_html(city)} &nbsp;·&nbsp; 
			<b>Zone:</b> ${frappe.utils.escape_html(zone)}
		</div>` : '';

		const fields = hasContext ? [
			{ fieldtype: 'HTML', options: ctxHTML },
			{ fieldtype: 'Data', fieldname: 'store_name', label: 'Store Name', reqd: 1 },
			{ fieldtype: 'Column Break' },
			{ fieldtype: 'Link', fieldname: 'warehouse', label: 'Default Warehouse (optional)', options: 'Warehouse',
				get_query: () => ({ filters: { company, is_group: 0 } }) },
			{ fieldtype: 'Section Break' },
			{ fieldtype: 'HTML', options: '<div class="text-muted small">Store Code is auto-generated. Add Address & Contacts after creating.</div>' },
		] : [
			{ fieldtype: 'Link', fieldname: 'company', label: 'Company', options: 'Company', reqd: 1, default: this.company },
			{ fieldtype: 'Link', fieldname: 'city', label: 'City', options: 'CH City', reqd: 1,
				get_query: () => ({ filters: { company: d.get_value('company') } }) },
			{ fieldtype: 'Link', fieldname: 'zone', label: 'Zone', options: 'CH Store Zone', reqd: 1,
				get_query: () => ({ filters: { company: d.get_value('company'), city: d.get_value('city') } }) },
			{ fieldtype: 'Data', fieldname: 'store_name', label: 'Store Name', reqd: 1 },
			{ fieldtype: 'Link', fieldname: 'warehouse', label: 'Default Warehouse (optional)', options: 'Warehouse',
				get_query: () => ({ filters: { company: d.get_value('company'), is_group: 0 } }) },
		];

		const d = new frappe.ui.Dialog({
			title: __('Add Store'),
			fields,
			primary_action_label: __('Create'),
			primary_action: (v) => {
				const args = hasContext
					? { company, city, zone, store_name: v.store_name, warehouse: v.warehouse }
					: v;
				frappe.call({
					method: 'ch_item_master.ch_core.location_hierarchy.save_store',
					args,
					callback: () => { d.hide(); frappe.show_alert({message: __('Store created'), indicator:'green'}); this.render(); }
				});
			}
		});
		d.show();
	}
	delete_store(name) {
		frappe.confirm(__('Delete store {0}?', [name]), () => {
			frappe.call({
				method: 'ch_item_master.ch_core.location_hierarchy.delete_store',
				args: { name },
				callback: () => { frappe.show_alert({message: __('Deleted'), indicator:'red'}); this.render(); }
			});
		});
	}

	move_store_zone_dialog(store, company) {
		const d = new frappe.ui.Dialog({
			title: __('Assign Zone for {0}', [store.store_name || store.name]),
			fields: [
				{ fieldtype: 'HTML', options: `<div class="text-muted small" style="margin:-4px 0 8px;">${__('Select the city and zone this store belongs to. This corrects the missing zone assignment.')}</div>` },
				{ fieldtype: 'Link', fieldname: 'city', label: 'City', options: 'CH City', reqd: 1,
					get_query: () => ({ filters: { company } }) },
				{ fieldtype: 'Link', fieldname: 'zone', label: 'Zone', options: 'CH Store Zone', reqd: 1,
					get_query: () => ({ filters: { company, city: d.get_value('city') } }) },
			],
			primary_action_label: __('Move to Zone'),
			primary_action: (v) => {
				frappe.call({
					method: 'ch_item_master.ch_core.location_hierarchy.save_store',
					args: { name: store.name, company, city: v.city, zone: v.zone, store_name: store.store_name || store.name },
					callback: () => { d.hide(); frappe.show_alert({message: __('Store moved to zone'), indicator: 'green'}); this.render(); }
				});
			}
		});
		d.show();
	}
}
