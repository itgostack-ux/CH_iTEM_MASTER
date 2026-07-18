from unittest import TestCase
from unittest.mock import patch

from ch_item_master import security


class TestCompanyMappingSecurity(TestCase):
	def test_employee_mapping_does_not_inherit_global_default(self):
		def get_all(doctype, **kwargs):
			if doctype == "Employee":
				return ["GOFIX SOLUTIONS PRIVATE LIMITED"]
			return []

		with (
			patch.object(security, "_is_unrestricted_user", return_value=False),
			patch.object(security.frappe.db, "exists", return_value=True),
			patch.object(security.frappe, "get_all", side_effect=get_all),
			patch.object(
				security.frappe.permissions,
				"get_user_permissions",
				return_value={},
			),
		):
			companies = security.get_user_mapped_companies(
				"technician@example.com"
			)

		self.assertEqual(companies, ["GOFIX SOLUTIONS PRIVATE LIMITED"])

	def test_company_user_permission_precedes_employee_mapping(self):
		with (
			patch.object(security, "_is_unrestricted_user", return_value=False),
			patch.object(security.frappe.db, "exists", return_value=True),
			patch.object(security.frappe, "get_all", return_value=[]),
			patch.object(
				security.frappe.permissions,
				"get_user_permissions",
				return_value={
					"Company": [{"doc": "Bestbuy Mobiles Private Limited"}]
				},
			),
		):
			companies = security.get_user_mapped_companies(
				"buyer@example.com"
			)

		self.assertEqual(companies, ["Bestbuy Mobiles Private Limited"])

	def test_unmapped_user_has_empty_explicit_scope(self):
		with (
			patch.object(security, "_is_unrestricted_user", return_value=False),
			patch.object(security.frappe.db, "exists", return_value=True),
			patch.object(security.frappe, "get_all", return_value=[]),
			patch.object(
				security.frappe.permissions,
				"get_user_permissions",
				return_value={},
			),
		):
			companies = security.get_user_mapped_companies(
				"unmapped@example.com"
			)

		self.assertEqual(companies, [])
