import frappe
from frappe import _
from frappe.contacts.doctype.address.address import get_address_display


def validate(doc, method=None):
	"""Keep Branch address preview and Address links in sync."""
	if not doc.meta.get_field("ch_branch_address"):
		return

	address_name = doc.get("ch_branch_address")
	if not address_name:
		if doc.meta.get_field("ch_branch_address_display"):
			doc.ch_branch_address_display = None
		return

	if not frappe.db.exists("Address", {"name": address_name, "disabled": 0}):
		frappe.throw(_("Branch Address {0} was not found or is disabled.").format(address_name))

	if doc.meta.get_field("ch_branch_address_display"):
		doc.ch_branch_address_display = get_address_display(address_name)

	_ensure_address_links(address_name, doc)


@frappe.whitelist()
def get_branch_address_display(address_name=None):
	if not address_name:
		return ""
	if not frappe.db.exists("Address", {"name": address_name, "disabled": 0}):
		return ""
	return get_address_display(address_name)


def _ensure_address_links(address_name, branch):
	address = frappe.get_doc("Address", address_name)
	changed = False

	if address.meta.get_field("is_your_company_address") and not address.get("is_your_company_address"):
		address.is_your_company_address = 1
		changed = True

	changed = _append_link(address, "Branch", branch.name) or changed

	company = branch.get("ch_company")
	if company:
		changed = _append_link(address, "Company", company) or changed

	if changed:
		address.save(ignore_permissions=True)


def _append_link(address, link_doctype, link_name):
	if not link_name:
		return False

	for row in address.get("links", []):
		if row.link_doctype == link_doctype and row.link_name == link_name:
			return False

	address.append("links", {"link_doctype": link_doctype, "link_name": link_name})
	return True
