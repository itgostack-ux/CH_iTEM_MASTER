# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

from ch_item_master.setup import (
	create_ch_custom_fields,
	setup_item_variant_settings,
	setup_roles,
)


def after_install():
	"""Called after ch_item_master is installed."""
	setup_roles()
	create_ch_custom_fields()
	setup_item_variant_settings()


def before_uninstall():
	"""Called before ch_item_master is uninstalled."""
	from ch_item_master.setup import delete_ch_custom_fields

	delete_ch_custom_fields()
