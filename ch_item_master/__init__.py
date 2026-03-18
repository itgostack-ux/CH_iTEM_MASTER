__version__ = "0.0.1"


def __getattr__(name):
	"""Lazy-load submodules so that import_string_path resolution works."""
	if name == "supplier_scheme":
		import importlib
		mod = importlib.import_module("ch_item_master.supplier_scheme")
		return mod
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def has_permission(user=None):
	"""Check if user has permission to access the CH Item Master app"""
	import frappe

	if not user:
		user = frappe.session.user

	# Allow all users for now, can be customized based on roles
	return True
