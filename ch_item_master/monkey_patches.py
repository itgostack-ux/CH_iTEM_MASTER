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


def apply_all():
	for fn in (_patch_customer_loyalty_resolution,):
		try:
			fn()
		except Exception:
			import traceback
			print(f"ch_item_master monkeypatch failed: {fn.__name__}")
			traceback.print_exc()


apply_all()