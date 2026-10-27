"""
Monkeypatches applied at worker startup (imported from hooks.py).

These are narrow fixes for upstream ERPNext code paths that we cannot edit
directly in the vendored app checkout.
"""

from functools import wraps


def _patch_customer_loyalty_resolution():
	from erpnext.selling.doctype.customer import customer as _customer
	from ch_item_master.ch_customer_master.loyalty import get_applicable_loyalty_programs

	if getattr(_customer.get_loyalty_programs, "_ch_patched", False):
		return

	_original = _customer.get_loyalty_programs

	@wraps(_original)
	def patched(doc):
		return get_applicable_loyalty_programs(doc)

	patched._ch_patched = True
	patched.__wrapped__ = _original
	_customer.get_loyalty_programs = patched


def _patch_template_serial_no_check():
	"""Let a (non-stock) template keep Has Serial No.

	ERPNext refuses Has Serial No on a non-stock item. A CH template is
	non-stock, but its Has Serial No / Serial Number Kind is the serial profile
	copied onto its (stocked) variants, so the check is skipped for templates.
	"""
	from erpnext.stock.doctype.item.item import Item

	if getattr(Item.validate_item_type, "_ch_patched", False):
		return

	_original = Item.validate_item_type

	@wraps(_original)
	def patched(self):
		if self.has_serial_no and self.has_variants and not self.variant_of and not self.is_stock_item:
			return
		return _original(self)

	patched._ch_patched = True
	Item.validate_item_type = patched


def apply_all():
	for fn in (_patch_customer_loyalty_resolution, _patch_template_serial_no_check):
		try:
			fn()
		except Exception:
			import traceback
			print(f"ch_item_master monkeypatch failed: {fn.__name__}")
			traceback.print_exc()


apply_all()