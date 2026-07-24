import frappe
from frappe import _
from frappe.model.document import Document

from ch_item_master.config import require_role_setting


_LOCATION_MANAGER_ROLES = ("CH Master Manager",)


class CHState(Document):
    def validate(self):
        if self.state_name:
            self.state_name = self.state_name.strip().title()
        if self.state_code:
            self.state_code = self.state_code.strip().upper()


def _bounded_value(value, fieldname, label):
    value = (value or "").strip()
    if not value:
        frappe.throw(_("{0} is required.").format(label), frappe.ValidationError)
    field = frappe.get_meta("CH State").get_field(fieldname)
    maximum = field.length if field and field.length else 140
    if len(value) > maximum:
        frappe.throw(
            _("{0} cannot exceed {1} characters.").format(label, maximum),
            frappe.ValidationError,
        )
    return value


def _authoritative_state_code(state_name, country, supplied_code=None):
    supplied_code = (supplied_code or "").strip().upper()
    if country == "India":
        from ch_item_master.ch_core.setup.india_geography import STATES

        state_codes = {
            name.strip().title(): gst_code
            for name, gst_code, _iso_code in STATES
        }
        expected = state_codes.get(state_name)
        if expected:
            if supplied_code and supplied_code != expected:
                frappe.throw(
                    _("State code does not match the authoritative India geography master."),
                    frappe.ValidationError,
                )
            return expected
    if not supplied_code:
        frappe.throw(
            _("State Code is required when creating a state outside the reference master."),
            frappe.ValidationError,
        )
    return supplied_code


def _ensure_state(state_name, country="India", state_code=None):
    canonical = _bounded_value(state_name, "state_name", _("State Name")).title()
    country = _bounded_value(country or "India", "country", _("Country"))

    if not frappe.db.exists("Country", country):
        frappe.throw(_("Country {0} was not found.").format(country), frappe.DoesNotExistError)
    country_doc = frappe.get_doc("Country", country)
    country_doc.check_permission("read")

    existing = frappe.db.get_value("CH State", canonical, "name", for_update=True)
    if existing:
        state = frappe.get_doc("CH State", existing)
        state.check_permission("read")
        if state.country and state.country != country:
            frappe.throw(_("State already belongs to another country."), frappe.ValidationError)
        if state_code and state.state_code != state_code.strip().upper():
            frappe.throw(_("State already has a different state code."), frappe.ValidationError)
        return state.name

    frappe.has_permission("CH State", ptype="create", throw=True)
    state_code = _bounded_value(
        _authoritative_state_code(canonical, country, state_code),
        "state_code",
        _("State Code"),
    ).upper()
    if frappe.db.exists("CH State", {"state_code": state_code}):
        frappe.throw(_("State Code {0} is already in use.").format(state_code), frappe.DuplicateEntryError)

    state = frappe.new_doc("CH State")
    state.state_name = canonical
    state.state_code = state_code
    state.country = country
    try:
        state.insert()
    except frappe.DuplicateEntryError:
        existing = frappe.get_doc("CH State", canonical)
        existing.check_permission("read")
        return existing.name
    return state.name


def ensure_state(state_name: str, country: str = "India", state_code: str | None = None) -> str | None:
    """Internal compatibility entry point; it is intentionally not whitelisted."""
    if not (state_name or "").strip():
        return None
    require_role_setting(
        "location_manager_roles",
        _LOCATION_MANAGER_ROLES,
        action=_("manage state reference data"),
    )
    return _ensure_state(state_name, country, state_code)


@frappe.whitelist(methods=["POST"])
def ensure_state_api(
    state_name: str,
    country: str = "India",
    state_code: str | None = None,
) -> str | None:
    """Create or read a state through the configured location-management policy."""
    return ensure_state(state_name, country, state_code)
