// Copyright (c) 2026, GoStack and contributors
// CH Warranty Plan — client script

frappe.ui.form.on('CH Warranty Plan', {
	setup(frm) {
		// service_item must be a Subscription-nature item (CH Sub Category.item_nature
		// = 'Subscription' OR is_warranty_plan = 1). Falls back to non-stock items
		// for sub-categories that haven't been classified yet (legacy data).
		frm.set_query('service_item', () => ({
			query: 'ch_item_master.ch_item_master.api.items_by_subcategory_nature',
			filters: { natures: ['Subscription'], is_stock_item: 0 }
		})
		);

		frm.set_query('applicable_categories', () => {
			const item_groups = (frm.doc.applicable_item_groups || [])
				.map(row => row.item_group)
				.filter(Boolean);
			if (!item_groups.length) return {};
			return { filters: { item_group: ['in', item_groups] } };
		});

		frm.set_query('applicable_sub_categories', () => {
			const categories = (frm.doc.applicable_categories || [])
				.map(row => row.category)
				.filter(Boolean);
			if (!categories.length) return {};
			return { filters: { category: ['in', categories] } };
		});
	},

	refresh(frm) {
		// Status indicator
		let colors = { 'Active': 'green', 'Inactive': 'grey', 'Draft': 'orange' };
		if (frm.doc.status && !frm.is_new()) {
			frm.dashboard.set_headline(
				__('Plan is <strong>{0}</strong>', [frm.doc.status]),
				colors[frm.doc.status] || 'grey'
			);
		}

		// Validity period indicator
		if (frm.doc.valid_from || frm.doc.valid_to) {
			let today = frappe.datetime.get_today();
			let is_valid = true;
			if (frm.doc.valid_from && today < frm.doc.valid_from) is_valid = false;
			if (frm.doc.valid_to && today > frm.doc.valid_to) is_valid = false;

			if (!is_valid) {
				frm.dashboard.add_comment(
					__('This plan is outside its validity period ({0} to {1})',
						[frm.doc.valid_from || 'N/A', frm.doc.valid_to || 'N/A']),
					'orange', true
				);
			}
		}

		// If pricing_mode is Percentage, show calculated price example
		if (frm.doc.pricing_mode === 'Percentage of Device Price' && frm.doc.percentage_value) {
			let example_price = 20000; // ₹20,000 device
			let plan_price = (example_price * frm.doc.percentage_value / 100).toFixed(0);
			frm.dashboard.add_comment(
				__('Example: For a ₹{0} device, plan price = ₹{1}',
					[example_price.toLocaleString(), parseInt(plan_price).toLocaleString()]),
				'blue', true
			);
		}

		// ── Sell the plan from the Desk ──────────────────────────────────
		// warranty_api.issue_warranty_plan has always been able to sell one —
		// GoFix and the POS both call it — but nothing in the Desk did, so a
		// plan bought at the back office had no way in. Reuses that whitelisted
		// call rather than adding a second path to the same record.
		if (!frm.is_new() && frm.doc.status === 'Active' && frm.doc.is_sellable) {
			frm.add_custom_button(__('Issue to Customer'), () => issue_plan(frm))
				.addClass('btn-primary');
		}

		// Show margin info
		if (frm.doc.price && frm.doc.cost_to_company && frm.doc.pricing_mode === 'Fixed') {
			let margin = frm.doc.price - frm.doc.cost_to_company;
			let margin_pct = ((margin / frm.doc.price) * 100).toFixed(1);
			let color = margin >= 0 ? 'green' : 'red';
			frm.dashboard.add_comment(
				__('Margin: ₹{0} ({1}%)', [margin.toLocaleString(), margin_pct]),
				color, true
			);
		}
	},

	pricing_mode(frm) {
		if (frm.doc.pricing_mode === 'Fixed') {
			frm.set_value('percentage_value', 0);
		} else {
			frm.set_value('price', 0);
		}
	},

	service_item(frm) {
		// Show alert if service item is stock
		if (frm.doc.service_item) {
			frappe.db.get_value('Item', frm.doc.service_item, 'is_stock_item').then(r => {
				if (r && r.message && r.message.is_stock_item) {
					frappe.show_alert({
						message: __('This is a stock item. Warranty plans should use non-stock service items.'),
						indicator: 'red'
					});
				}
			});
		}
		frappe.db.get_value('Item', frm.doc.service_item,
			['brand', 'item_group', 'ch_category', 'ch_sub_category']
		).then(r => {
			if (!r || !r.message) return;
			const item = r.message;

			if (item.brand) {
				frm.set_value('brand', item.brand);
			}
		});

	},

	coverage_availability(frm) {
		const external = ['External Only', 'Both'].includes(frm.doc.coverage_availability);
		frm.set_value('allow_external_device', external ? 1 : 0);
		if (external) {
			frappe.show_alert({
				message: __('Configure the fixed External Device Price. Leave Applicable Sub Categories blank to accept every external phone, or configure them to require model verification at POS.'),
				indicator: 'blue'
			});
		}
	},
});


/**
 * Sell this plan to a customer against one device.
 *
 * Everything the server needs is already validated inside
 * issue_warranty_plan — company access, customer and item read permission, the
 * plan's own company, base-warranty stacking and the coverage window. This
 * collects the four things it cannot infer and gets out of the way.
 */
function issue_plan(frm) {
	const zero_priced = frm.doc.pricing_mode === 'Fixed' && !frm.doc.price;
	const percentage_priced = frm.doc.pricing_mode === 'Percentage of Device Price';
	const d = new frappe.ui.Dialog({
		title: __('Issue {0}', [frm.doc.plan_name || frm.doc.name]),
		fields: [
			{
				fieldname: 'customer', fieldtype: 'Link', options: 'Customer',
				label: __('Customer'), reqd: 1,
			},
			{
				fieldname: 'item_code', fieldtype: 'Link', options: 'Item',
				label: __('Device'), reqd: 1,
				description: __('The device being covered, not the plan itself.'),
				onchange: () => price_from_device(frm, d),
			},
			{ fieldtype: 'Column Break' },
			{
				fieldname: 'serial_no', fieldtype: 'Data', label: __('Serial / IMEI'),
				description: __('Required for a serialised device.'),
			},
			{
				fieldname: 'start_date', fieldtype: 'Date', label: __('Coverage Starts'),
				default: frappe.datetime.get_today(),
				description: frm.doc.starts_after_base_warranty
					? __('Ignored while this plan stacks on the base warranty — the server starts it the day that expires.')
					: '',
			},
			{ fieldtype: 'Section Break' },
			{
				fieldname: 'plan_price', fieldtype: 'Currency', label: __('Price Charged'),
				default: frm.doc.price || 0,
				// A Fixed plan with no price would be given away silently; a
				// percentage plan carries price 0 by design and is priced off
				// the device once one is chosen.
				description: zero_priced
					? __('This plan has no price set, so it will be issued free unless you enter one.')
					: (percentage_priced
						? __('Set from {0}% of the device price once you pick a device.',
							[frm.doc.percentage_value])
						: ''),
			},
			{
				fieldname: 'sales_invoice', fieldtype: 'Link', options: 'Sales Invoice',
				label: __('Against Invoice'),
				description: __('Optional — links the cover to the sale that paid for it.'),
			},
		],
		primary_action_label: __('Issue Plan'),
		primary_action: (values) => {
			d.hide();
			frappe.call({
				method: 'ch_item_master.ch_item_master.warranty_api.issue_warranty_plan',
				args: Object.assign({ warranty_plan: frm.doc.name }, values),
				freeze: true,
				freeze_message: __('Issuing plan…'),
				callback: (r) => {
					if (!r.message) return;
					frappe.show_alert({
						message: __('Issued {0}', [r.message.active_plan]),
						indicator: 'green',
					});
					frappe.set_route('Form', 'Active VAS Plans', r.message.active_plan);
				},
			});
		},
	});
	d.show();
}


/**
 * Price a percentage plan off the chosen device.
 *
 * A "Percentage of Device Price" plan stores price 0 — every VAS and
 * Protection plan on this estate is one — so the dialog would otherwise
 * default the charge to zero and issue the cover free. The POS attach panel
 * resolves the same number from CH Item Price (POS channel), so this reads the
 * same source rather than inventing a second answer.
 */
function price_from_device(frm, dialog) {
	if (frm.doc.pricing_mode !== 'Percentage of Device Price') return;
	const item_code = dialog.get_value('item_code');
	if (!item_code) return;
	frappe.db.get_value(
		'CH Item Price',
		{ item_code: item_code, channel: 'POS', status: 'Active' },
		'selling_price'
	).then((r) => {
		const device_price = (r && r.message && r.message.selling_price) || 0;
		if (!device_price) {
			dialog.set_df_property('plan_price', 'description',
				__('{0} has no active POS price, so the percentage cannot be applied — enter the charge.',
					[item_code]));
			return;
		}
		const computed = flt(device_price * flt(frm.doc.percentage_value) / 100.0, 2);
		dialog.set_value('plan_price', computed);
		dialog.set_df_property('plan_price', 'description',
			__('{0}% of {1} (POS price for {2}).',
				[frm.doc.percentage_value, format_currency(device_price), item_code]));
	});
}
