"""POS bin transfers must carry the Store Transfer classification."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import frappe

from ch_item_master.ch_core import bin_transfer


class TestPOSBinTransferType(unittest.TestCase):
    def test_pos_wrapper_marks_transfer_as_pos_origin(self):
        reason = frappe._dict(source_bin_type="Sellable", target_bin_type="Damaged")
        with (
            patch.object(bin_transfer, "get_store_for_user", return_value="STORE-1"),
            patch.object(bin_transfer.frappe.db, "get_value", return_value=reason),
            patch.object(bin_transfer, "transfer_between_bins", return_value="MAT-STE-TEST") as create,
        ):
            result = bin_transfer.pos_bin_transfer(
                item_code="ITEM-1",
                qty=1,
                reason="Damage",
            )

        self.assertEqual(result["stock_entry"], "MAT-STE-TEST")
        self.assertTrue(create.call_args.kwargs["from_pos"])
