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
		const STYLE_ID = 'location-hierarchy-styles-v2';
		// Remove old style tag if a previous version is loaded.
		const old = document.getElementById('location-hierarchy-styles');
		if (old) old.remove();
		if (document.getElementById(STYLE_ID)) return;
		const css = `
		.location-hierarchy-page { padding: 8px 4px; }
		.lh-company { border:1px solid var(--border-color); border-radius:8px; margin-bottom:14px; background:var(--card-bg); }
		.lh-company-header { padding:10px 14px; border-bottom:1px solid var(--border-color); display:flex; justify-content:space-between; align-items:center; background:var(--bg-light-gray); border-radius:8px 8px 0 0;}
		.lh-company-title { font-weight:600; font-size:14px; }
		.lh-city { border-top:1px solid var(--border-color); padding:8px 14px; }
		.lh-city:first-child { border-top:none; }
		.lh-city-header { display:flex; justify-content:space-between; align-items:center; cursor:pointer; }
		.lh-city-title { font-weight:600; }
		.lh-zone { margin:10px 0 10px 18px; padding:10px 14px; border:1px solid var(--border-color); border-radius:8px; background:var(--fg-color); }
		.lh-zone-header { display:flex; justify-content:space-between; align-items:flex-start; gap:6px; margin-bottom:6px; }
		.lh-zone-title { font-weight:600; font-size:13px; }
		.lh-zone-meta { color:var(--text-muted); font-size:11px; margin-left:6px; }
		.lh-zone-stats { display:flex; flex-wrap:wrap; gap:6px; margin:4px 0 8px; font-size:11px; color:var(--text-muted); }
		.lh-zone-stat { display:inline-flex; align-items:center; gap:3px; padding:1px 8px; border-radius:10px; background:var(--bg-light-gray); }
		.lh-zone-stat b { color:var(--text-color); font-weight:600; }
		.lh-zone-stat.lh-zero { opacity:0.45; }
		.lh-section { margin:6px 0; }
		.lh-section-title { font-weight:500; font-size:11px; text-transform:uppercase; color:var(--text-muted); margin:6px 0 3px; letter-spacing:0.5px; display:flex; justify-content:space-between; align-items:center; }
		.lh-section-empty { display:flex; justify-content:space-between; align-items:center; padding:2px 0; font-size:11px; color:var(--text-muted); }
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
		.lh-bins-toggle { cursor:pointer; color:var(--text-muted); font-size:11px; user-select:none;}
		.lh-bins-toggle:hover { color:var(--primary);}
		.lh-bins-collapsed .lh-bins-body { display:none;}
		.lh-bin-store-group { margin:6px 0 8px; padding:6px 10px; border-left:2px solid var(--border-color); background:var(--bg-light-gray); border-radius:0 6px 6px 0;}
		.lh-bin-store-group.lh-bin-orphan { border-left-color:var(--text-muted); background:transparent;}
		.lh-bin-store-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; }
		.lh-bin-store-name { font-size:11px; font-weight:600; color:var(--text-color); text-transform:uppercase; letter-spacing:0.4px;}
		.lh-bin-count { color:var(--text-muted); font-weight:400; margin-left:4px;}
		.lh-bin-row { gap:4px; }
		.lh-bin-row .lh-pill.lh-warehouse-bin .lh-bin-type-tag { font-size:10px; padding:0 5px; border-radius:8px; background:var(--fg-color); border:1px solid var(--border-color); color:var(--text-color); margin-right:4px; text-transform:uppercase; letter-spacing:0.3px;}
		.lh-actions a { margin-left:6px; font-size:12px; }
		.lh-empty-state { padding:40px; text-align:center; color:var(--text-muted); }
		.lh-add-btn { font-size:11px; color:var(--primary); cursor:pointer; }
		.lh-add-btn:hover { text-decoration:underline; }
		.lh-row { display:flex; flex-wrap:wrap; align-items:center; gap:4px;}
		`;
		const style = document.createElement('style');
		style.id = STYLE_ID;
		style.innerHTML = css;
		document.head.appendChild(style);
	}

	setup_toolbar() {
		this.page.set_primary_action(__('Add City'), () => this.add_city(), 'add');
		this.page.add_menu_item(__('Add State'), () => this.add_state());
		this.page.add_menu_item(__('Add City to Master'), () => this._city_master_dialog({}, false, null));
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
		// The "Unassigned" city is a synthetic bucket produced server-side for
		// rows whose ch_city is NULL/empty. There is no CH City record behind
		// it, so Edit / Delete / + Zone would all either no-op or delete an
		// unrelated record. Hide those actions; users must assign a real city
		// on the underlying Warehouse / Branch / Store to clear the bucket.
		const isSynthetic = city.city === 'Unassigned';
		const stateLabel = city.state_code
			? `${frappe.utils.escape_html(city.state_code)} · ${frappe.utils.escape_html(city.state || '')}`
			: frappe.utils.escape_html(city.state || '');
		const $header = $(`<div class="lh-city-header">
			<div class="lh-city-title"><i class="fa fa-map-marker"></i> ${frappe.utils.escape_html(city.city_name)} <span class="lh-zone-meta">${stateLabel}</span></div>
			${isSynthetic ? '' : `<div class="lh-actions">
				<button class="btn btn-xs btn-default" data-act="add-zone">+ ${__('Zone')}</button>
				<a href="#" data-act="edit-city">${__('Edit')}</a>
				<a href="#" data-act="del-city" class="text-danger">${__('Delete')}</a>
			</div>`}
		</div>`).appendTo($city);

		if (!isSynthetic) {
			$header.find('[data-act="add-zone"]').on('click', () => this.add_zone(company, city.city, city.city_name));
			$header.find('[data-act="edit-city"]').on('click', (e) => { e.preventDefault(); this.edit_city(city.city); });
			$header.find('[data-act="del-city"]').on('click', (e) => { e.preventDefault(); this.delete_city(city.city); });
		} else {
			$(`<div class="alert alert-warning" style="font-size:12px;margin:6px 0 4px;padding:6px 10px;">
				<i class="fa fa-exclamation-triangle"></i>
				${__('These items have no city assigned. Set a City on the underlying Warehouse / Branch / Store to remove them from this bucket.')}
			</div>`).appendTo($city);
		}

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

		// Split warehouses by retail role (ch_location_type).
		// Post-v12 the store base warehouse IS the Sellable bin and is
		// already represented by the CH Store record below, so we omit
		// a separate "Store Warehouses" section to avoid duplication.
		const buckets = { hub: [], bin: [], other: [] };
		for (const w of zone.warehouses) {
			const t = (w.ch_location_type || '').trim();
			if (t === 'Zone Warehouse' || t === 'Transit Warehouse' || t === 'Service Warehouse') {
				buckets.hub.push(w);
			} else if (t === 'Store Bin') {
				buckets.bin.push(w);
			} else if (t === 'Store Warehouse') {
				// Skip — already covered by the CH Store pill below.
				continue;
			} else {
				buckets.other.push(w);
			}
		}

		// ── Header with counts ───────────────────────────────────────
		$(`<div class="lh-zone-header">
			<div>
				<span class="lh-zone-title"><i class="fa fa-th-large"></i> ${frappe.utils.escape_html(zone.zone_name || zone.zone)}</span>
			</div>
			${isSynthetic ? '' : `<div class="lh-actions">
				<a href="#" data-act="edit-zone">${__('Edit')}</a>
				<a href="#" data-act="del-zone" class="text-danger">${__('Delete')}</a>
			</div>`}
		</div>`).appendTo($z);

		const stats = [
			{ icon: 'fa-shopping-bag', label: __('Stores'),  n: zone.stores.length },
			{ icon: 'fa-cube',         label: __('Hub'),     n: buckets.hub.length },
			{ icon: 'fa-archive',      label: __('Bins'),    n: buckets.bin.length },
			{ icon: 'fa-briefcase',    label: __('Offices'), n: zone.offices.length },
		];
		const $stats = $(`<div class="lh-zone-stats"></div>`).appendTo($z);
		for (const s of stats) {
			$stats.append(`<span class="lh-zone-stat${s.n ? '' : ' lh-zero'}"><i class="fa ${s.icon}"></i> <b>${s.n}</b> ${s.label}</span>`);
		}
		if (buckets.other.length) {
			$stats.append(`<span class="lh-zone-stat"><i class="fa fa-warehouse"></i> <b>${buckets.other.length}</b> ${__('Other')}</span>`);
		}

		if (!isSynthetic) {
			$z.find('[data-act="edit-zone"]').on('click', (e) => { e.preventDefault(); this.edit_zone(zone.zone, company, city.city); });
			$z.find('[data-act="del-zone"]').on('click', (e) => { e.preventDefault(); this.delete_zone(zone.zone); });
		} else {
			$(`<div class="alert alert-warning" style="font-size:12px;margin:6px 0 4px;padding:6px 10px;">
				<i class="fa fa-exclamation-triangle"></i>
				${__('These items have no zone assigned. Use <b>→ Assign Zone</b> on each item to move them to the correct zone.')}
			</div>`).appendTo($z);
		}

		// ── Stores (primary list) ────────────────────────────────────
		if (zone.stores.length || !isSynthetic) {
			const $sSec = $(`<div class="lh-section"></div>`).appendTo($z);
			const addLink = isSynthetic ? '' : `<span class="lh-add-btn" data-act="add-store">+ ${__('Add Store')}</span>`;
			$sSec.append(`<div class="lh-section-title"><span>${__('Stores')}</span>${addLink}</div>`);
			if (!isSynthetic) $sSec.find('[data-act="add-store"]').on('click', () => this.add_store_dialog(company, city.city, zone.zone));
			if (zone.stores.length) {
				const $sRow = $(`<div class="lh-row"></div>`).appendTo($sSec);
				for (const s of zone.stores) {
					const storeActions = isSynthetic
						? `<a href="#" data-act="move-store" class="btn-link text-primary">${__('→ Assign Zone')}</a>`
						: `<a href="#" data-act="del-store" class="btn-link text-danger">×</a>`;
					// Show the CH Store identity in the requested format:
					//   "<name> · <store_name>"   →  e.g. "Doveton · Doveton"
					//                                  or "Doveton · Kelambakkam Store"
					// The store_code (STO-BMPL-CHENNA-…) is dropped from the
					// visible label and demoted to a tooltip — it was visually
					// noisy and didn't help users identify the store at a glance.
					const primary   = s.name || s.store_code;
					const secondary = s.store_name || s.name;
					const $p = $(`<span class="lh-pill lh-store" title="${frappe.utils.escape_html(s.store_code || s.name)}">
						<a href="/app/ch-store/${encodeURIComponent(s.name)}" target="_blank">${frappe.utils.escape_html(primary)}</a>
						<small class="text-muted">· ${frappe.utils.escape_html(secondary)}</small>
						<span class="lh-actions">${storeActions}</span>
					</span>`).appendTo($sRow);
					if (isSynthetic) {
						$p.find('[data-act="move-store"]').on('click', (e) => { e.preventDefault(); this.move_store_zone_dialog(s, company); });
					} else {
						$p.find('[data-act="del-store"]').on('click', (e) => { e.preventDefault(); this.delete_store(s.name); });
					}
				}
			}
		}

		// ── Hub (only when present, or single inline assign link) ────
		if (buckets.hub.length) {
			const $hSec = $(`<div class="lh-section"></div>`).appendTo($z);
			const assignLink = isSynthetic ? '' : `<span class="lh-add-btn" data-act="assign-hub">+ ${__('Assign')}</span>`;
			$hSec.append(`<div class="lh-section-title"><span>${__('Hub')}</span>${assignLink}</div>`);
			if (!isSynthetic) $hSec.find('[data-act="assign-hub"]').on('click', () => this.assign_warehouse_dialog(company, city.city, zone.zone));
			const $hRow = $(`<div class="lh-row"></div>`).appendTo($hSec);
			for (const w of buckets.hub) {
				this._draw_warehouse_pill($hRow, w, 'lh-warehouse-hub', company, city, zone);
			}
		}

		// ── Other / unclassified warehouses ──────────────────────────
		if (buckets.other.length) {
			const $oSec = $(`<div class="lh-section"></div>`).appendTo($z);
			const assignLink = isSynthetic ? '' : `<span class="lh-add-btn" data-act="assign-other">+ ${__('Assign')}</span>`;
			$oSec.append(`<div class="lh-section-title"><span>${__('Other Warehouses')}</span>${assignLink}</div>`);
			if (!isSynthetic) $oSec.find('[data-act="assign-other"]').on('click', () => this.assign_warehouse_dialog(company, city.city, zone.zone));
			const $oWhRow = $(`<div class="lh-row"></div>`).appendTo($oSec);
			for (const w of buckets.other) {
				this._draw_warehouse_pill($oWhRow, w, 'lh-warehouse', company, city, zone);
			}
		}

		// ── Stock Bins — grouped by their parent CH Store ────────────
		// Each store gets a sub-block listing its bins + a "+ Add Bin" action.
		// Bins not linked to any store in this zone fall into an "Unassigned"
		// sub-block (legacy / mis-stamped data).
		if (buckets.bin.length || zone.stores.length) {
			const storeBins = new Map();   // store.name -> [warehouse, ...]
			const orphanBins = [];
			const storeNames = new Set((zone.stores || []).map(s => s.name));
			for (const w of buckets.bin) {
				if (w.ch_store && storeNames.has(w.ch_store)) {
					if (!storeBins.has(w.ch_store)) storeBins.set(w.ch_store, []);
					storeBins.get(w.ch_store).push(w);
				} else {
					orphanBins.push(w);
				}
			}

			const totalBins = buckets.bin.length;
			const $bWrap = $(`<div class="lh-section lh-bins-collapsed"></div>`).appendTo($z);
			const $bHdr = $(`<div class="lh-section-title">
				<span class="lh-bins-toggle">▸ ${__('Stock Bins')} (${totalBins})</span>
			</div>`).appendTo($bWrap);
			const $bBody = $(`<div class="lh-bins-body"></div>`).appendTo($bWrap);
			$bHdr.find('.lh-bins-toggle').on('click', () => {
				$bWrap.toggleClass('lh-bins-collapsed');
				$bHdr.find('.lh-bins-toggle').text(
					($bWrap.hasClass('lh-bins-collapsed') ? '▸ ' : '▾ ')
					+ __('Stock Bins') + ` (${totalBins})`
				);
			});

			const _draw_store_group = (store, bins) => {
				const $grp = $(`<div class="lh-bin-store-group"></div>`).appendTo($bBody);
				const storeLabel = frappe.utils.escape_html(
					(store.store_code ? `${store.store_code} · ` : '') +
					(store.store_name || store.name)
				);
				const $h = $(`<div class="lh-bin-store-header">
					<span class="lh-bin-store-name">${storeLabel}
						<span class="lh-bin-count">(${bins.length})</span>
					</span>
					<span class="lh-add-btn" data-act="add-bin">+ ${__('Add Bin')}</span>
				</div>`).appendTo($grp);
				$h.find('[data-act="add-bin"]').on('click', () => {
					this.add_bin_dialog(store, company, city, zone);
				});
				const $row = $(`<div class="lh-row lh-bin-row"></div>`).appendTo($grp);
				if (!bins.length) {
					$row.append(`<span class="text-muted small">${__('No bins yet.')}</span>`);
				} else {
					// Sort bins by ch_bin_type for stable ordering.
					bins.sort((a, b) =>
						String(a.ch_bin_type || '').localeCompare(String(b.ch_bin_type || ''))
					);
					for (const w of bins) {
						this._draw_bin_pill($row, w, company, city, zone);
					}
				}
			};

			// One sub-block per store in the zone (even if it has 0 extra bins,
			// so the "+ Add Bin" action is always reachable).
			for (const store of (zone.stores || [])) {
				_draw_store_group(store, storeBins.get(store.name) || []);
			}

			// Bins whose ch_store isn't in this zone — legacy data hatch.
			if (orphanBins.length) {
				const $grp = $(`<div class="lh-bin-store-group lh-bin-orphan"></div>`).appendTo($bBody);
				$grp.append(`<div class="lh-bin-store-header">
					<span class="lh-bin-store-name text-muted">${__('Unassigned bins')}
						<span class="lh-bin-count">(${orphanBins.length})</span>
					</span>
				</div>`);
				const $row = $(`<div class="lh-row lh-bin-row"></div>`).appendTo($grp);
				for (const w of orphanBins) {
					this._draw_bin_pill($row, w, company, city, zone);
				}
			}

			if (!zone.stores.length && !orphanBins.length) {
				// Nothing to show — keep DOM tidy by removing the wrapper.
				$bWrap.remove();
			}
		}

		// ── Offices (only when present, or footer link) ──────────────
		if (zone.offices.length) {
			const $oSec = $(`<div class="lh-section"></div>`).appendTo($z);
			const assignLink = isSynthetic ? '' : `<span class="lh-add-btn" data-act="assign-office">+ ${__('Assign')}</span>`;
			$oSec.append(`<div class="lh-section-title"><span>${__('Offices')}</span>${assignLink}</div>`);
			if (!isSynthetic) $oSec.find('[data-act="assign-office"]').on('click', () => this.assign_office_dialog(company, city.city, zone.zone));
			const $oRow = $(`<div class="lh-row"></div>`).appendTo($oSec);
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

		// ── Footer: quick add-links for whatever is missing ──────────
		if (!isSynthetic) {
			const missing = [];
			if (!buckets.hub.length)    missing.push({ label: __('+ Hub'),    act: () => this.assign_warehouse_dialog(company, city.city, zone.zone) });
			if (!zone.offices.length)   missing.push({ label: __('+ Office'), act: () => this.assign_office_dialog(company, city.city, zone.zone) });
			if (missing.length) {
				const $footer = $(`<div class="lh-section-empty"><span class="text-muted">${__('Add to zone:')}</span><span></span></div>`).appendTo($z);
				const $right = $footer.find('span').last();
				for (const m of missing) {
					const $a = $(`<span class="lh-add-btn" style="margin-left:10px;">${m.label}</span>`).appendTo($right);
					$a.on('click', m.act);
				}
			}
		}
	}

	_draw_warehouse_pill($parent, w, extraClass, company, city, zone) {
		const isSynthetic = zone.zone === 'Unassigned';
		const editLabel = isSynthetic ? __('→ Assign Zone') : __('Edit');
		const $p = $(`<span class="lh-pill ${extraClass}" title="${frappe.utils.escape_html(w.name)}">
			<a href="/app/warehouse/${encodeURIComponent(w.name)}" target="_blank">${frappe.utils.escape_html(w.warehouse_name || w.name)}</a>
			<span class="lh-actions">
				<a href="#" data-act="edit-wh" class="btn-link${isSynthetic ? ' text-primary' : ''}">${editLabel}</a>
				${isSynthetic ? '' : '<a href="#" data-act="unassign-wh" class="btn-link text-danger">×</a>'}
			</span>
		</span>`).appendTo($parent);
		$p.find('[data-act="edit-wh"]').on('click', (e) => { e.preventDefault(); this.assign_warehouse_dialog(company, city.city, zone.zone, w); });
		if (!isSynthetic) $p.find('[data-act="unassign-wh"]').on('click', (e) => { e.preventDefault(); this.unassign_warehouse(w.name); });
		return $p;
	}

	_draw_bin_pill($parent, w, company, city, zone) {
		// Specialised pill for Store Bin warehouses. We deliberately do NOT
		// render the verbose warehouse name (e.g. "STO-BMPL-CHENNA-0003-Buyback")
		// because the bin_type tag already conveys the stock-state bucket and
		// the CH Store identity is what users actually need to scan for.
		//
		// Layout:  [BUYBACK]  Doveton (Kelambakkam Store)
		//          ^tag       ^ch_store  ^ch_store_name (parens only when distinct)
		//
		// Falls back gracefully when the bin isn't linked to a CH Store —
		// shows the warehouse_name / name so legacy data stays visible.
		const binType = w.ch_bin_type || '';
		const tag = binType
			? `<span class="lh-bin-type-tag">${frappe.utils.escape_html(binType)}</span>`
			: '';

		// Resolve the display label from the in-memory CH Store join the
		// server performs in get_company_location_tree.
		const storeId   = w.ch_store || '';
		const storeName = w.ch_store_name || '';
		let label;
		if (storeId && storeName && storeId !== storeName) {
			// Distinct short id + long name → render both: "Doveton (Kelambakkam Store)"
			label = `${frappe.utils.escape_html(storeId)} <span class="text-muted">(${frappe.utils.escape_html(storeName)})</span>`;
		} else if (storeId || storeName) {
			// Only one of the two is set, or they're identical — show once.
			label = frappe.utils.escape_html(storeId || storeName);
		} else {
			// Orphan bin (no CH Store linked) — fall back to warehouse identity.
			label = frappe.utils.escape_html(w.warehouse_name || w.name);
		}

		const $p = $(`<span class="lh-pill lh-warehouse-bin" title="${frappe.utils.escape_html(w.name)}">
			${tag}<a href="/app/warehouse/${encodeURIComponent(w.name)}" target="_blank">${label}</a>
		</span>`).appendTo($parent);
		return $p;
	}

	add_bin_dialog(store, company, city, zone) {
		// Open a dialog to create / restore one of the canonical stock-state
		// bins for the given store. Bin types are constrained to the values
		// allowed by the Warehouse.ch_bin_type Select field — adding a free-
		// form type would fail server-side validation.
		const ctxHTML = `<div class="text-muted small" style="margin:-4px 0 8px;">
			<b>${__('Store')}:</b> ${frappe.utils.escape_html((store.store_code ? store.store_code + ' · ' : '') + (store.store_name || store.name))}
			&nbsp;·&nbsp; <b>${__('Zone')}:</b> ${frappe.utils.escape_html(zone.zone_name || zone.zone)}
		</div>`;

		// Path B Phase 1: only the 3 active bin types are offered for new
		// creation. Reserved / Disposed / In-Transit were removed (see
		// ch_store.py STORE_BIN_TYPES rationale). Existing warehouses of
		// those types remain readable in the tree.
		const STANDARD = ['Damaged', 'Demo', 'Buyback'];
		const d = new frappe.ui.Dialog({
			title: __('Add Bin to {0}', [store.store_code || store.name]),
			fields: [
				{ fieldtype: 'HTML', options: ctxHTML },
				{
					fieldtype: 'Select', fieldname: 'bin_type', label: __('Bin Type'),
					options: ['', ...STANDARD].join('\n'),
					reqd: 1,
					description: __('Idempotent — if this bin already exists for the store, it will be reported and no new warehouse will be created.'),
				},
			],
			primary_action_label: __('Create Bin'),
			primary_action: (v) => {
				const bin_type = (v.bin_type || '').trim();
				if (!bin_type) {
					frappe.msgprint({ message: __('Bin Type is required.'), indicator: 'red' });
					return;
				}
				frappe.call({
					method: 'ch_item_master.ch_core.location_hierarchy.create_store_bin',
					args: { store: store.name, bin_type },
					freeze: true,
					freeze_message: __('Creating bin…'),
					callback: (r) => {
						d.hide();
						if (r.message && r.message.created === false) {
							frappe.show_alert({
								message: __('Bin already exists: {0}', [r.message.warehouse]),
								indicator: 'orange',
							});
						} else {
							frappe.show_alert({
								message: __('Bin created: {0}', [r.message.warehouse]),
								indicator: 'green',
							});
						}
						this.render();
					},
				});
			},
		});
		d.show();
	}

	// ----------------- Dialogs -----------------

	add_city(company) {
		// "Add City to a Company" is an ASSOCIATION action, not a master-edit:
		// it picks an existing CH City row and immediately chains into the
		// Add Zone dialog with company + city pre-filled. The city only shows
		// up under the company in the tree once a Zone (and Store / Warehouse)
		// is created under it — that matches how the data model actually works
		// (cities are surfaced in the tree by virtue of stores/zones/warehouses
		// stamped with ch_city, there is no Company↔City mapping doctype).
		this._city_picker_dialog(company || this.company);
	}
	edit_city(name) {
		frappe.db.get_doc('CH City', name).then(doc => this._city_master_dialog(doc, false, null));
	}
	/**
	 * Pick an existing CH City master row and chain into Add Zone.
	 *
	 * @param {string|null} company - locked context (from a Company card) or
	 *   null to ask. When null we also surface a Company Link field.
	 */
	_city_picker_dialog(company) {
		const lockedCompany = !!company;
		const ctxHTML = lockedCompany
			? `<div class="text-muted small" style="margin:-4px 0 8px;">
				<b>${__('Adding to')}:</b> ${frappe.utils.escape_html(company)}
				&nbsp;·&nbsp; <i>${__('City masters are shared across all companies. Pick the city this company operates in; the next step adds a Zone under it.')}</i>
			   </div>`
			: `<div class="text-muted small" style="margin:-4px 0 8px;">
				${__('Pick the company and the city it operates in. The next step adds a Zone under that city.')}
			   </div>`;
		const fields = [
			{ fieldtype: 'HTML', options: ctxHTML },
		];
		if (!lockedCompany) {
			fields.push({
				fieldtype: 'Link', fieldname: 'company', label: __('Company'),
				options: 'Company', reqd: 1, default: this.company,
			});
		}
		fields.push({
			fieldtype: 'Link', fieldname: 'city', label: __('City'),
			options: 'CH City', reqd: 1,
			placeholder: __('Search the CH City master…'),
			description: __('Pick from CH City master (full Indian district coverage is pre-seeded). Missing one? Use the link below.'),
			get_query: () => ({ filters: { disabled: 0 } }),
		});
		fields.push({
			fieldtype: 'HTML',
			options: `<div class="text-muted small" style="margin-top:-4px;">
				<a href="/app/ch-city/new?city_name=" target="_blank" data-act="create-city-master">
					+ ${__('Create new city in master…')}
				</a>
				&nbsp;·&nbsp;
				<a href="/app/ch-city" target="_blank">${__('Browse CH City master')}</a>
			</div>`,
		});
		const d = new frappe.ui.Dialog({
			title: __('Add City to Company'),
			fields,
			primary_action_label: __('Next: Add Zone'),
			primary_action: (v) => {
				const pickedCompany = lockedCompany ? company : v.company;
				const pickedCity = v.city;
				if (!pickedCompany || !pickedCity) return;
				// Resolve the friendly label once so _zone_dialog doesn't have
				// to round-trip again.
				frappe.db.get_value('CH City', pickedCity, 'city_name').then((r) => {
					const cityLabel = (r && r.message && r.message.city_name) || pickedCity;
					d.hide();
					this._zone_dialog(
						{ company: pickedCompany, city: pickedCity, city_label: cityLabel },
						true,
					);
				});
			},
		});
		d.show();
	}
	/**
	 * Master-edit form for CH City. Used by:
	 *  - the pencil "Edit City" action on an existing city
	 *  - the "Add City to Master" menu item (rarely used; pre-seed covers most cases)
	 *
	 * NOT used by the primary "+ City" / "Add City" buttons — those go through
	 * `_city_picker_dialog` and never create master rows.
	 */
	_city_master_dialog(doc, fromCompanyCard, contextCompany) {
		const ctxHTML = fromCompanyCard
			? `<div class="text-muted small" style="margin:-4px 0 8px;">${__('Opened from')} <b>${frappe.utils.escape_html(contextCompany || '')}</b>. ${__('City masters are shared across all companies — no company is stored on the city itself.')}</div>`
			: '';
		const state_field = {
			fieldtype: 'Link', fieldname: 'state', label: __('State'),
			options: 'CH State', default: doc.state,
			placeholder: __('Select a state…'),
			description: __('Pick from CH State master. Use <b>Menu → Add State</b> if it is missing.'),
			get_query: () => ({ filters: { disabled: 0 } }),
		};
		const fields = [
			...(ctxHTML ? [{ fieldtype: 'HTML', options: ctxHTML }] : []),
			{ fieldtype: 'Data', fieldname: 'city_name', label: __('City Name'), reqd: 1, default: doc.city_name },
			state_field,
			{ fieldtype: 'Check', fieldname: 'disabled', label: __('Disabled'), default: doc.disabled || 0 },
			{ fieldtype: 'Small Text', fieldname: 'description', label: __('Description'), default: doc.description },
		];
		const d = new frappe.ui.Dialog({
			title: doc && doc.name ? __('Edit City') : __('Add City to Master'),
			fields,
			primary_action_label: __('Save'),
			primary_action: (v) => {
				frappe.call({
					method: 'ch_item_master.ch_core.location_hierarchy.save_city',
					args: {
						city_name: v.city_name,
						state: v.state || null,
						disabled: v.disabled || 0,
						description: v.description || null,
						name: doc.name || null,
					},
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

	add_state() {
		this._state_dialog({});
	}
	_state_dialog(doc) {
		const fields = [
			{ fieldtype: 'Data', fieldname: 'state_name', label: __('State Name'), reqd: 1, default: doc.state_name },
			{ fieldtype: 'Data', fieldname: 'state_code', label: __('State Code'), reqd: 1, default: doc.state_code,
				description: __('ISO 3166-2 / GST state code, e.g. KA, MH, 29') },
			{ fieldtype: 'Link', fieldname: 'country', label: __('Country'), options: 'Country', default: doc.country || 'India' },
			{ fieldtype: 'Check', fieldname: 'disabled', label: __('Disabled'), default: doc.disabled || 0 },
			{ fieldtype: 'Small Text', fieldname: 'description', label: __('Description'), default: doc.description },
		];
		const d = new frappe.ui.Dialog({
			title: doc.name ? __('Edit State') : __('Add State'),
			fields,
			primary_action_label: __('Save'),
			primary_action: (v) => {
				frappe.call({
					method: 'ch_item_master.ch_core.location_hierarchy.save_state',
					args: {
						state_name: v.state_name,
						state_code: v.state_code,
						country: v.country || 'India',
						disabled: v.disabled || 0,
						description: v.description || null,
						name: doc.name || null,
					},
					callback: () => { d.hide(); frappe.show_alert({message: __('State saved'), indicator:'green'}); this.render(); }
				});
			}
		});
		d.show();
	}

	add_zone(company, city, city_label) {
		this._zone_dialog({ company: company || this.company, city, city_label }, !!(company && city));
	}
	edit_zone(name) {
		frappe.db.get_doc('CH Store Zone', name).then(doc => this._zone_dialog(doc, false));
	}
	_zone_dialog(doc, lockContext) {
		// Show the human label (city_name) instead of the raw link key, which
		// for legacy records can be a long compounded autoname string like
		// "Tamil Nadu-Tamil Nadu-…-Chennai". Fall back to the link key only
		// if no label was passed.
		const cityLabel = doc.city_label || doc.city || '';
		const cityHref = doc.city
			? `/app/ch-city/${encodeURIComponent(doc.city)}`
			: null;
		const cityHTML = cityHref
			? `<a href="${cityHref}" target="_blank">${frappe.utils.escape_html(cityLabel)}</a>`
			: frappe.utils.escape_html(cityLabel);
		const ctxHTML = lockContext ? `<div class="text-muted small" style="margin:-4px 0 8px;">
			<b>Company:</b> ${frappe.utils.escape_html(doc.company)} &nbsp;·&nbsp;
			<b>City:</b> ${cityHTML}
		</div>` : '';
		const fields = lockContext ? [
			{ fieldtype: 'HTML', options: ctxHTML },
			{ fieldtype: 'Data', fieldname: 'zone_name', label: 'Zone Name', reqd: 1 },
			{ fieldtype: 'Link', fieldname: 'source_warehouse', label: 'Source Warehouse', options: 'Warehouse',
				get_query: () => ({ filters: { company: doc.company, is_group: 0 } }) },
		] : [
			{ fieldtype: 'Link', fieldname: 'company', label: 'Company', options: 'Company', reqd: 1, default: doc.company || this.company },
			{ fieldtype: 'Link', fieldname: 'city', label: 'City', options: 'CH City', reqd: 1, default: doc.city,
				get_query: () => ({ filters: { disabled: 0 } }) },
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
				get_query: () => ({ filters: { disabled: 0 } }) });
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
				get_query: () => ({ filters: { disabled: 0 } }) },
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
					get_query: () => ({ filters: { disabled: 0 } }) },
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
				get_query: () => ({ filters: { disabled: 0 } }) },
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
					get_query: () => ({ filters: { disabled: 0 } }) },
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