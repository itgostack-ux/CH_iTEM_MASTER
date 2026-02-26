// Copyright (c) 2026, GoStack and contributors
// CH Warranty Plan — client script

frappe.ui.form.on('CH Warranty Plan', {
	setup(frm) {
		// Service item must be a non-stock item
		frm.set_query('service_item', () => ({
			filters: { is_stock_item: 0, disabled: 0 }
		}));
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
	},
});
