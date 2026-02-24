__version__ = "0.0.1"


def has_permission(user=None):
	"""Check if user has permission to access the CH Item Master app"""
	import frappe

	if not user:
		user = frappe.session.user

	# Allow all users for now, can be customized based on roles
	return True
