from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from ch_item_master.ch_core import location_hierarchy as hierarchy
from ch_item_master.ch_core import warehouse_geo
from ch_item_master.ch_core.doctype.ch_store import ch_store


class TestLocationHierarchySecurity(IntegrationTestCase):
	def test_every_public_mutator_is_post_only(self):
		mutators = (
			hierarchy.save_city,
			hierarchy.delete_city,
			hierarchy.save_state,
			hierarchy.delete_state,
			hierarchy.save_zone,
			hierarchy.delete_zone,
			hierarchy.assign_warehouse,
			hierarchy.unassign_warehouse,
			hierarchy.unassign_city_hub,
			hierarchy.create_hub,
			hierarchy.add_hub_bin_at_city,
			hierarchy.assign_office,
			hierarchy.unassign_office,
			hierarchy.create_office,
			hierarchy.save_store,
			hierarchy.delete_store,
			hierarchy.create_store_bin,
			hierarchy.create_hub_bin,
		)
		for method in mutators:
			self.assertEqual(
				set(frappe.allowed_http_methods_for_whitelisted_func[method]),
				{"POST"},
				method.__name__,
			)

	def test_location_scope_uses_direct_grants_for_mutations(self):
		scope = {
			"bypass": False,
			"companies": {"Company A"},
			"direct_companies": set(),
			"cities": {"City A"},
			"direct_cities": set(),
			"zones": {"Zone A"},
			"direct_zones": set(),
			"stores": {"Store A"},
			"warehouses": {"Store A - A"},
			"requested_company": "Company A",
		}
		self.assertTrue(hierarchy._city_in_scope(scope, "Company A", "City A"))
		self.assertFalse(
			hierarchy._city_in_scope(scope, "Company A", "City A", for_write=True)
		)
		self.assertTrue(hierarchy._zone_in_scope(scope, "Company A", "City A", "Zone A"))
		self.assertFalse(
			hierarchy._zone_in_scope(
				scope, "Company A", "City A", "Zone A", for_write=True
			)
		)
		scope["direct_zones"] = {"Zone A"}
		self.assertTrue(
			hierarchy._zone_in_scope(
				scope, "Company A", "City A", "Zone A", for_write=True
			)
		)

	def test_warehouse_sql_scope_is_parameterized_and_fail_closed(self):
		values = {}
		condition = hierarchy._warehouse_scope_condition(
			{
				"bypass": False,
				"requested_company": "Company A",
				"direct_companies": set(),
				"direct_cities": set(),
				"direct_zones": {"Zone A"},
				"stores": {"Store A"},
				"warehouses": {"Warehouse A"},
			},
			values,
			prefix="test_scope",
		)
		self.assertIn("wh.company = %(test_scope_company)s", condition)
		self.assertIn("wh.ch_zone IN %(test_scope_zones)s", condition)
		self.assertIn("wh.ch_store IN %(test_scope_stores)s", condition)
		self.assertNotIn("Company A", condition)
		self.assertEqual(values["test_scope_company"], "Company A")
		self.assertEqual(values["test_scope_zones"], ("Zone A",))
		self.assertEqual(
			hierarchy._warehouse_scope_condition(
				{"bypass": False, "requested_company": None}, {}, prefix="empty"
			),
			"(1=0)",
		)

	def test_link_query_page_length_is_capped_by_setting(self):
		with patch.object(hierarchy, "get_int_setting", return_value=25):
			self.assertEqual(hierarchy._bounded_page(-10, 5000), (0, 25))

	def test_public_module_has_no_permission_or_transaction_bypass(self):
		for module in (hierarchy, warehouse_geo, ch_store):
			source = inspect.getsource(module)
			self.assertNotIn("ignore_permissions", source)
			self.assertNotIn("frappe.db.commit", source)
		self.assertNotIn("limit_page_length=0", inspect.getsource(hierarchy))

	def test_settings_and_default_docperms_align(self):
		app_path = Path(frappe.get_app_path("ch_item_master"))
		settings = json.loads(
			(app_path / "ch_item_master/doctype/ch_item_master_settings/ch_item_master_settings.json").read_text()
		)
		fields = {row["fieldname"]: row for row in settings["fields"]}
		for fieldname in (
			"location_view_roles",
			"location_manager_roles",
			"warehouse_picker_roles",
			"location_tree_row_limit",
			"location_query_limit",
		):
			self.assertIn(fieldname, fields)

		for relative in (
			"ch_core/doctype/ch_city/ch_city.json",
			"ch_core/doctype/ch_state/ch_state.json",
			"ch_core/doctype/ch_store/ch_store.json",
			"ch_core/doctype/ch_store_zone/ch_store_zone.json",
		):
			doctype = json.loads((app_path / relative).read_text())
			manager = next(
				row for row in doctype["permissions"] if row["role"] == "CH Master Manager"
			)
			for permission_type in ("read", "write", "create", "delete"):
				self.assertEqual(manager.get(permission_type), 1, relative)
