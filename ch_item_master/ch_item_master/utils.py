import frappe

# Roles that are allowed to access the CH Item Master app
CH_APP_ROLES = frozenset([
	"CH Master Manager",
	"CH Price Manager",
	"CH Offer Manager",
	"CH Warranty Manager",
	"CH Viewer",
])


def check_app_permission():
	"""Check if user has a CH-specific role to access the CH Item Master app.

	Returns True for Administrator unconditionally; for all others the user must
	have at least one of the CH_APP_ROLES — generic Item read access is NOT
	sufficient (prevents all ERPNext stock users from landing here).
	"""
	if frappe.session.user == "Administrator":
		return True

	user_roles = set(frappe.get_roles(frappe.session.user))
	if user_roles & CH_APP_ROLES:
		return True

	return False
