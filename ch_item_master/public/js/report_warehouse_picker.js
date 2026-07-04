/**
 * ch_item_master — Store-centric Warehouse pickers in query reports
 * =================================================================
 * Business users think in STORES, but report "Warehouse" filters list the
 * whole warehouse tree (group nodes, Damaged/Demo/Buyback bins, transit,
 * hub bins…). This global patch rewires every Warehouse filter in every
 * query report to `report_warehouse_query`, which lists only:
 *
 *   * each store's Sellable warehouse — searchable/labelled by store name
 *   * Distribution Hubs
 *
 * The stored filter value is still a Warehouse PK, so report queries are
 * untouched — picking a store maps to its selling bin server-side.
 *
 * Covers both filter shapes:
 *   * Link             (custom CH reports, Stock Ledger, …)
 *   * MultiSelectList  (ERPNext Stock Balance-style `get_data`)
 */
(function () {
	const QUERY = "ch_item_master.ch_core.location_hierarchy.report_warehouse_query";

	function company_filters(report) {
		const filters = {};
		try {
			const company = report.get_filter_value("company");
			if (company) filters.company = company;
		} catch (e) {
			// report has no company filter — ignore
		}
		return filters;
	}

	function apply(report) {
		if (!report || !Array.isArray(report.filters)) return;
		report.filters.forEach((filter) => {
			const df = filter.df || {};
			if (df.options !== "Warehouse" || df.__ch_store_picker) return;

			if (df.fieldtype === "Link") {
				df.__ch_store_picker = true;
				df.get_query = () => ({ query: QUERY, filters: company_filters(report) });
			} else if (df.fieldtype === "MultiSelectList") {
				df.__ch_store_picker = true;
				// Call the query directly so we control the label: the store
				// name renders BOLD in the dropdown and on the selected chip,
				// while the stored value stays the sellable Warehouse PK.
				df.get_data = (txt) =>
					frappe
						.xcall(QUERY, {
							doctype: "Warehouse",
							txt: txt || "",
							searchfield: "name",
							start: 0,
							page_len: 20,
							filters: company_filters(report),
						})
						.then((rows) =>
							(rows || []).map((row) => ({
								value: row[0],
								label: row[1] || row[0],
								description: `${row[2] || ""} · ${row[0]}`,
							}))
						);
			}
		});
	}

	function apply_with_retry(attempt) {
		const report = frappe.query_report;
		if (report && Array.isArray(report.filters) && report.filters.length) {
			apply(report);
			return;
		}
		// Filters render asynchronously after route change; retry briefly.
		if ((attempt || 0) < 20) {
			setTimeout(() => apply_with_retry((attempt || 0) + 1), 300);
		}
	}

	$(document).on("page-change", () => {
		const route = frappe.get_route ? frappe.get_route() : [];
		if (route && route[0] === "query-report") {
			apply_with_retry(0);
		}
	});
})();
