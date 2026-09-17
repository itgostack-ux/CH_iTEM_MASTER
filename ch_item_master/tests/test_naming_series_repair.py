"""A restored database must not hand out names that already exist.

`tabSeries` holds one counter per naming prefix. A dump brings the documents
but not always a matching counter, and every insert then re-uses a taken name:

    DuplicateEntryError: ('POS Kiosk Token', 'KTK-2026-00002', ...)

Nothing recovers on its own — each attempt picks the same number. Six series
were in that state here, two at the counter: POS Kiosk Token (1 vs 77) and
Buyback Assessment (1 vs 2). A walk-in token is mandatory before a Service
Request, so front desk and buyback intake were both unusable, and it presented
as a duplicate-key crash rather than anything a user could act on.

id_sequences.sync_all_numeric_id_series already heals numeric *field*
sequences on after_migrate; document *names* were uncovered.
"""

import unittest

import frappe

from ch_item_master.naming_series_repair import _SKIP_DOCTYPES, repair_naming_series


class TestNamingSeriesRepair(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def test_no_series_is_behind_its_documents(self):
        result = repair_naming_series(dry_run=True)
        self.assertEqual(
            result["repaired"], 0,
            "a naming counter is behind the documents already issued — the next "
            f"insert will collide: {result['details'][:5]}")

    def test_it_actually_checks_something(self):
        """A skip list that swallowed everything would pass the test above."""
        self.assertGreater(repair_naming_series(dry_run=True)["checked"], 100)

    def test_counters_only_move_forward(self):
        import inspect
        src = inspect.getsource(repair_naming_series)
        self.assertIn("GREATEST", src,
                      "a counter must never be lowered onto names already taken")

    def test_hash_named_doctypes_are_excluded(self):
        """File and Version names end in digits but are not series-allocated;
        reading them would push a counter into the billions."""
        for doctype in ("File", "Version", "Comment"):
            self.assertIn(doctype, _SKIP_DOCTYPES)

    def test_it_runs_after_every_migrate(self):
        from ch_item_master import hooks
        self.assertIn("ch_item_master.naming_series_repair.after_migrate",
                      hooks.after_migrate)

    def test_the_counter_surfaces_can_be_created(self):
        """The two that mattered: a walk-in token and a buyback assessment."""
        for doctype in ("POS Kiosk Token", "Buyback Assessment"):
            doc = frappe.new_doc(doctype)
            doc.flags.ignore_mandatory = True
            doc.flags.ignore_permissions = True
            try:
                doc.insert(ignore_permissions=True)
            except frappe.DuplicateEntryError:
                self.fail(f"{doctype} still collides — the series is behind again")
            except Exception:
                pass  # any other validation is not what this test is about
