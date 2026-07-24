import inspect
from datetime import date
from unittest import TestCase
from unittest.mock import patch

import frappe

from ch_item_master.ch_item_master import coupon_campaign_api, exception_api
from ch_item_master.ch_item_master.page.ch_item_master_dashboard import ch_item_master_dashboard


class TestCouponCampaignHubSecurity(TestCase):
	def test_hub_denies_before_any_query_when_role_gate_fails(self):
		with (
			patch.object(
				coupon_campaign_api,
				"require_role_setting",
				side_effect=frappe.PermissionError,
			),
			patch.object(coupon_campaign_api.frappe.db, "sql") as sql,
			self.assertRaises(frappe.PermissionError),
		):
			coupon_campaign_api.get_campaign_hub_data(company="Test Company")
		sql.assert_not_called()

	def test_hub_requires_named_read_permissions(self):
		with (
			patch.object(coupon_campaign_api, "require_role_setting"),
			patch.object(coupon_campaign_api, "is_privileged_user", return_value=False),
			patch.object(coupon_campaign_api.frappe, "has_permission", return_value=True) as allowed,
		):
			coupon_campaign_api._require_campaign_access(coupon_campaign_api._HUB_READ_DOCTYPES)

		self.assertEqual(
			{entry.args[0] for entry in allowed.call_args_list},
			set(coupon_campaign_api._HUB_READ_DOCTYPES),
		)
		for entry in allowed.call_args_list:
			self.assertEqual(entry.kwargs["ptype"], "read")

	def test_system_manager_bypass_does_not_depend_on_mutable_docperms(self):
		with (
			patch.object(coupon_campaign_api, "require_role_setting") as role_gate,
			patch.object(coupon_campaign_api, "is_privileged_user", return_value=True),
			patch.object(coupon_campaign_api.frappe, "has_permission") as has_permission,
		):
			coupon_campaign_api._require_campaign_access(coupon_campaign_api._HUB_READ_DOCTYPES)

		role_gate.assert_called_once_with(
			"coupon_campaign_management_roles",
			coupon_campaign_api._CAMPAIGN_ROLES,
			action="view campaign and redemption data",
		)
		has_permission.assert_not_called()

	def test_blank_company_never_becomes_global_for_unprivileged_user(self):
		with (
			patch.object(coupon_campaign_api, "is_privileged_user", return_value=False),
			patch.object(coupon_campaign_api, "get_company_scope", return_value=[]),
			self.assertRaises(frappe.PermissionError),
		):
			coupon_campaign_api._resolve_company()

		with (
			patch.object(coupon_campaign_api, "is_privileged_user", return_value=False),
			patch.object(coupon_campaign_api, "get_company_scope", return_value=["A", "B"]),
			self.assertRaises(frappe.ValidationError),
		):
			coupon_campaign_api._resolve_company()

	def test_single_company_is_checked_against_location_scope(self):
		with (
			patch.object(coupon_campaign_api, "is_privileged_user", return_value=False),
			patch.object(coupon_campaign_api, "get_company_scope", return_value=["Company A"]),
			patch("ch_erp15.ch_erp15.scope.assert_user_has_store_scope") as scope_guard,
		):
			self.assertEqual(coupon_campaign_api._resolve_company(), "Company A")

		scope_guard.assert_called_once_with(
			company="Company A",
			user=frappe.session.user,
			msg="You are not permitted to view campaigns for this company.",
		)

	def test_date_range_is_validated_and_capped(self):
		self.assertEqual(
			coupon_campaign_api._build_date_filters("2026-07-01", "2026-07-22"),
			{"from_date": date(2026, 7, 1), "to_date": date(2026, 7, 22)},
		)
		with self.assertRaises(frappe.ValidationError):
			coupon_campaign_api._build_date_filters("2026-07-23", "2026-07-22")
		with self.assertRaises(frappe.ValidationError):
			coupon_campaign_api._build_date_filters("2025-01-01", "2026-07-22")

	def test_invoice_scope_fails_closed_without_store_or_warehouse(self):
		with (
			patch.object(coupon_campaign_api, "is_privileged_user", return_value=False),
			patch(
				"ch_erp15.ch_erp15.scope.get_user_scope",
				return_value={"bypass": False, "stores": set(), "warehouses": set()},
			),
		):
			scope = coupon_campaign_api._build_invoice_scope()
		self.assertEqual(scope, {"clause": " AND 1 = 0", "params": {}})

	def test_invoice_scope_uses_bound_parameters(self):
		with (
			patch.object(coupon_campaign_api, "is_privileged_user", return_value=False),
			patch(
				"ch_erp15.ch_erp15.scope.get_user_scope",
				return_value={
					"bypass": False,
					"stores": {"Store A"},
					"warehouses": {"Warehouse A"},
				},
			),
			patch.object(coupon_campaign_api.frappe, "get_all", return_value=["Profile A"]),
		):
			scope = coupon_campaign_api._build_invoice_scope()

		self.assertIn("%(scope_profiles)s", scope["clause"])
		self.assertIn("%(scope_warehouses)s", scope["clause"])
		self.assertNotIn("Profile A", scope["clause"])
		self.assertNotIn("Warehouse A", scope["clause"])
		self.assertEqual(scope["params"]["scope_profiles"], ("Profile A",))

	def test_voucher_redemptions_are_company_and_sales_invoice_scoped(self):
		with patch.object(
			coupon_campaign_api.frappe.db,
			"sql",
			side_effect=[[], []],
		) as sql:
			coupon_campaign_api._get_recent_redemptions(
				{"company": "Company A"},
				{"from_date": date(2026, 7, 1), "to_date": date(2026, 7, 22)},
				{
					"clause": " AND {alias}.set_warehouse IN %(scope_warehouses)s",
					"params": {"scope_warehouses": ("Warehouse A",)},
				},
			)

		voucher_query = sql.call_args_list[1].args[0]
		voucher_params = sql.call_args_list[1].args[1]
		self.assertIn("`tabSales Invoice` si", voucher_query)
		self.assertIn("v.company = %(company)s", voucher_query)
		self.assertIn("si.set_warehouse IN %(scope_warehouses)s", voucher_query)
		self.assertNotIn("Company A", voucher_query)
		self.assertEqual(voucher_params["company"], "Company A")

	def test_insight_campaign_names_are_html_escaped(self):
		malicious = '<script>alert("x")</script>'
		def query_rows(query, *_args, **_kwargs):
			if "redemption_rate < 5" in query:
				return [frappe._dict(
					campaign_name=malicious,
					redemption_rate=1,
					total_codes_generated=10,
				)]
			return []

		with (
			patch.object(
				coupon_campaign_api.frappe.db,
				"sql",
				side_effect=query_rows,
			),
			patch.object(coupon_campaign_api.frappe.db, "count", return_value=1),
		):
			insights = coupon_campaign_api._get_insights({"company": "Company A"})

		self.assertNotIn("<script>", insights[0]["text"])
		self.assertIn("&lt;script&gt;", insights[0]["text"])

	def test_lookup_denies_before_reading_codes(self):
		with (
			patch.object(
				coupon_campaign_api,
				"_require_campaign_access",
				side_effect=frappe.PermissionError,
			),
			patch.object(coupon_campaign_api.frappe.db, "get_value") as get_value,
			self.assertRaises(frappe.PermissionError),
		):
			coupon_campaign_api.lookup_code("SECRET")
		get_value.assert_not_called()

	def test_voucher_lookup_removes_customer_identity_and_checks_scope(self):
		voucher = frappe._dict(
			name="VOUCHER-1",
			voucher_code="SECRET",
			voucher_type="Promo Voucher",
			status="Active",
			original_amount=500,
			balance=250,
			valid_from="2026-07-01",
			valid_upto="2026-07-31",
			company="Company A",
		)
		with (
			patch.object(coupon_campaign_api, "_require_campaign_access"),
			patch.object(coupon_campaign_api, "_require_document_read"),
			patch.object(coupon_campaign_api, "_get_linked_campaign", return_value=None),
			patch.object(
				coupon_campaign_api.frappe.db,
				"get_value",
				side_effect=[None, voucher],
			),
			patch.object(coupon_campaign_api, "_require_lookup_scope") as scope_guard,
		):
			result = coupon_campaign_api.lookup_code("SECRET")

		scope_guard.assert_called_once_with("Company A")
		self.assertNotIn("issued_to", result)
		self.assertEqual(result["balance"], 250)

	def test_lookup_rejects_cross_company_reference_chain(self):
		coupon = frappe._dict(
			name="COUPON-1",
			coupon_code="SECRET",
			pricing_rule="RULE-1",
			used=0,
			maximum_use=1,
			valid_from="2026-07-01",
			valid_upto="2026-07-31",
		)
		with (
			patch.object(coupon_campaign_api, "_require_campaign_access"),
			patch.object(coupon_campaign_api, "_require_document_read"),
			patch.object(
				coupon_campaign_api,
				"_get_linked_campaign",
				return_value=frappe._dict(name="CAMPAIGN-1", campaign_name="Campaign", company="A"),
			),
			patch.object(
				coupon_campaign_api.frappe.db,
				"get_value",
				side_effect=[coupon, frappe._dict(company="B", warehouse=None)],
			),
			patch.object(coupon_campaign_api, "_require_lookup_scope") as scope_guard,
			self.assertRaises(frappe.PermissionError),
		):
			coupon_campaign_api.lookup_code("SECRET")
		scope_guard.assert_not_called()

	def test_static_contract_has_bounded_queries_and_no_customer_identity(self):
		source = inspect.getsource(coupon_campaign_api)
		self.assertGreaterEqual(source.count("LIMIT "), 8)
		self.assertNotIn('"issued_to":', source)
		self.assertNotIn("issued_to_name", source)
		self.assertIn("coupon_campaign_management_roles", source)
		self.assertIn("%(company)s", source)


class TestItemDashboardSecurity(TestCase):
	def test_dashboard_denies_before_query_when_role_gate_fails(self):
		with (
			patch.object(
				ch_item_master_dashboard,
				"require_role_setting",
				side_effect=frappe.PermissionError,
			),
			patch.object(ch_item_master_dashboard.frappe.db, "sql") as sql,
			patch.object(ch_item_master_dashboard.frappe.db, "count") as count,
			self.assertRaises(frappe.PermissionError),
		):
			ch_item_master_dashboard.get_dashboard_data(company="Company A")
		sql.assert_not_called()
		count.assert_not_called()

	def test_dashboard_requires_named_reads_for_every_exposed_doctype(self):
		with (
			patch.object(ch_item_master_dashboard, "require_role_setting"),
			patch.object(ch_item_master_dashboard, "is_privileged_user", return_value=False),
			patch.object(ch_item_master_dashboard.frappe, "has_permission", return_value=True) as allowed,
		):
			ch_item_master_dashboard._require_dashboard_access()

		self.assertEqual(
			{entry.args[0] for entry in allowed.call_args_list},
			set(ch_item_master_dashboard._DASHBOARD_READ_DOCTYPES),
		)

	def test_dashboard_blank_company_is_fail_closed(self):
		with (
			patch.object(ch_item_master_dashboard, "is_privileged_user", return_value=False),
			patch.object(ch_item_master_dashboard, "get_company_scope", return_value=[]),
			self.assertRaises(frappe.PermissionError),
		):
			ch_item_master_dashboard._resolve_dashboard_company()

	def test_dashboard_company_owned_kpis_are_filtered(self):
		with patch.object(ch_item_master_dashboard.frappe.db, "count", return_value=0) as count:
			ch_item_master_dashboard._get_kpis("2026-07-22", "Company A")

		price_call = next(entry for entry in count.call_args_list if entry.args[0] == "CH Item Price")
		offer_call = next(entry for entry in count.call_args_list if entry.args[0] == "CH Item Offer")
		self.assertEqual(price_call.args[1]["company"], "Company A")
		self.assertEqual(offer_call.args[1]["company"], "Company A")

	def test_dashboard_sql_uses_bound_company_parameters(self):
		source = inspect.getsource(ch_item_master_dashboard)
		self.assertIn("%(company)s", source)
		self.assertNotIn('f" AND p.company', source)
		self.assertIn("app_access_roles", source)


class TestExceptionSummarySecurity(TestCase):
	def test_summary_denies_before_query_when_role_gate_fails(self):
		with (
			patch.object(
				exception_api,
				"require_role_setting",
				side_effect=frappe.PermissionError,
			),
			patch.object(exception_api.frappe.db, "sql") as sql,
			self.assertRaises(frappe.PermissionError),
		):
			exception_api.get_exception_summary("Company A")
		sql.assert_not_called()

	def test_summary_requires_named_read_and_company_scope(self):
		with (
			patch.object(exception_api, "require_role_setting"),
			patch.object(exception_api, "is_privileged_user", return_value=False),
			patch.object(exception_api.frappe, "has_permission", return_value=True) as read_permission,
			patch.object(exception_api, "get_company_scope", return_value=["Company A"]) as company_scope,
			patch("ch_erp15.ch_erp15.scope.assert_user_has_store_scope") as location_scope,
			patch.object(exception_api.frappe.db, "sql", return_value=[]) as sql,
		):
			result = exception_api.get_exception_summary(
				"Company A",
				"2026-07-01",
				"2026-07-22",
			)

		self.assertEqual(result, [])
		read_permission.assert_called_once_with(
			"CH Exception Request",
			ptype="read",
			user=frappe.session.user,
		)
		company_scope.assert_called_once_with(requested_company="Company A")
		location_scope.assert_called_once_with(
			company="Company A",
			user=frappe.session.user,
			msg="You are not permitted to view exception data for this company.",
		)
		query = sql.call_args.args[0]
		params = sql.call_args.args[1]
		self.assertIn("company = %(company)s", query)
		self.assertIn("LIMIT 500", query)
		self.assertNotIn("Company A", query)
		self.assertEqual(params["company"], "Company A")

	def test_summary_rejects_blank_or_excessive_date_ranges(self):
		with (
			patch.object(exception_api, "require_role_setting"),
			patch.object(exception_api, "is_privileged_user", return_value=True),
			self.assertRaises(frappe.ValidationError),
		):
			exception_api.get_exception_summary("")

		with (
			patch.object(exception_api, "require_role_setting"),
			patch.object(exception_api, "is_privileged_user", return_value=True),
			patch.object(exception_api, "get_company_scope", return_value=None),
			self.assertRaises(frappe.ValidationError),
		):
			exception_api.get_exception_summary("Company A", "2025-01-01", "2026-07-22")
