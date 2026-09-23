import unittest

import frappe


class TestCHStore(unittest.TestCase):
    """Plain unittest.TestCase, wrapped in a savepoint.

    IntegrationTestCase pulls in erpnext's test-record bootstrap, which tries
    to create its own Fiscal Years and dies on this site with "Year start date
    or end date is overlapping with Fiscal Year 2021-2022" -- before a single
    assertion runs. The bench convention is unittest.TestCase plus a savepoint
    for exactly this reason; the savepoint also guarantees the cleanup these
    tests used to do by hand, which a failing assertion would have skipped.
    """

    def setUp(self):
        frappe.set_user("Administrator")
        self._savepoint = "ch_store_test"
        frappe.db.savepoint(self._savepoint)

    def tearDown(self):
        frappe.db.rollback(save_point=self._savepoint)

    def _location(self):
        """A company/city/zone that actually belong together.

        An active store is an operating unit, not an address label, so
        CH Store refuses one without a City and a Zone. Both tests used to
        pass a company alone and have failed on that rule ever since it
        landed. Taking all three off one existing store keeps them
        consistent -- a city from one company and a zone from another would
        trip a different validation.
        """
        row = frappe.db.sql(
            """SELECT company, city, zone FROM `tabCH Store`
               WHERE COALESCE(company, '') <> '' AND COALESCE(city, '') <> ''
                 AND COALESCE(zone, '') <> ''
               ORDER BY name LIMIT 1""",
            as_dict=True,
        )
        if not row:
            raise unittest.SkipTest("no CH Store with a company, city and zone to copy")
        return row[0]

    def _new_store(self, code, name):
        loc = self._location()
        return frappe.get_doc({
            "doctype": "CH Store",
            "store_code": code,
            "store_name": name,
            "company": loc.company,
            "city": loc.city,
            "zone": loc.zone,
        })

    def test_auto_id(self):
        doc = self._new_store("_TEST-STORE-001", "Test Store")
        doc.insert()
        self.assertGreater(doc.store_id, 0)

    def test_store_code_uppercase(self):
        doc = self._new_store("test-lower", "Test Lower Store")
        doc.insert()
        self.assertEqual(doc.store_code, "TEST-LOWER")
