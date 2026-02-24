import frappe


def check_app_permission():
	"""Check if user has permission to access the CH Item Master app"""
	if frappe.session.user == "Administrator":
		return True

	if frappe.has_permission("Item", ptype="read"):
		return True

	return False
