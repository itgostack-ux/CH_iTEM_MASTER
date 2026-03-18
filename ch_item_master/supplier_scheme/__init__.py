def __getattr__(name):
	"""Lazy-load submodules so that import_string_path resolution works."""
	if name == "scorecard_variables":
		import importlib
		mod = importlib.import_module("ch_item_master.supplier_scheme.scorecard_variables")
		return mod
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
