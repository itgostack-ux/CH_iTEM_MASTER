from frappe.tests import IntegrationTestCase

from ch_item_master.ch_customer_master.customer_360_api import _get_devices


class TestCustomer360LifecycleSQL(IntegrationTestCase):
	def test_device_lifecycle_window_query_is_mariadb_compatible(self):
		# The production failure used ROW_NUMBER() with the reserved alias
		# ``row_number``. Running the real query guards MariaDB compatibility.
		devices = _get_devices("Maha")
		self.assertIsInstance(devices, list)
		for device in devices:
			self.assertIn("lifecycle_logs", device)
