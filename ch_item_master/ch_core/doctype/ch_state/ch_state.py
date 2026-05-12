import frappe
from frappe.model.document import Document


class CHState(Document):
    def validate(self):
        if self.state_name:
            self.state_name = self.state_name.strip().title()
        if self.state_code:
            self.state_code = self.state_code.strip().upper()


def ensure_state(state_name: str, country: str = "India") -> str | None:
    """Idempotently create/return a CH State by name (used by city auto-fill).

    Returns the canonical CH State name, or None when input is empty.
    """
    if not state_name:
        return None
    canonical = state_name.strip().title()
    if not canonical:
        return None
    if frappe.db.exists("CH State", canonical):
        return canonical
    try:
        doc = frappe.get_doc({
            "doctype": "CH State",
            "state_name": canonical,
            "country": country or "India",
        })
        doc.insert(ignore_permissions=True)
        return doc.name
    except frappe.DuplicateEntryError:
        return canonical


@frappe.whitelist()
def ensure_state_api(state_name: str, country: str = "India") -> str | None:
    """Whitelisted wrapper of `ensure_state` for POS / portal callers."""
    return ensure_state(state_name, country)


# Frappe whitelist accepts the function path directly; alias for legacy callers.
frappe.whitelist()(ensure_state)