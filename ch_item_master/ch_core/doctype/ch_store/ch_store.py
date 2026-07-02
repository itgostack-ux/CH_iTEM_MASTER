import re

import frappe
from frappe.model.document import Document

from ch_item_master.ch_item_master.utils import validate_indian_phone


class CHStore(Document):
    def autoname(self):
        """Auto-generate store_code: STO-{COMPANY_ABBR}-{CITY_SHORT}-####.

        Manual override is allowed (if user sets store_code explicitly we
        respect it). Otherwise we compose a deterministic, sortable code.
        """
        if not self.store_code:
            self.store_code = self._generate_store_code()
        else:
            self.store_code = self.store_code.strip().upper()
        self.name = self.store_code

    def _generate_store_code(self):
        company_abbr = (
            frappe.db.get_value("Company", self.company, "abbr") if self.company else None
        ) or "STO"
        city_short = ""
        if self.city:
            city_name = frappe.db.get_value("CH City", self.city, "city_name") or self.city
            city_short = re.sub(r"[^A-Za-z0-9]", "", city_name)[:6].upper()
        prefix_parts = ["STO", company_abbr.upper()]
        if city_short:
            prefix_parts.append(city_short)
        prefix = "-".join(prefix_parts) + "-"

        last = frappe.db.sql(
            "SELECT store_code FROM `tabCH Store` WHERE store_code LIKE %s ORDER BY creation DESC LIMIT 1",
            (prefix + "%",),
        )
        next_seq = 1
        if last and last[0][0]:
            try:
                next_seq = int(last[0][0].rsplit("-", 1)[-1]) + 1
            except ValueError:
                next_seq = 1
        return f"{prefix}{next_seq:04d}"

    def before_insert(self):
        """Auto-assign sequential integer ID using advisory lock."""
        frappe.db.sql("SELECT GET_LOCK('ch_store_id', 10)")
        try:
            last = frappe.db.sql(
                "SELECT MAX(store_id) FROM `tabCH Store`"
            )[0][0] or 0
            self.store_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('ch_store_id')")

    def validate(self):
        if self.store_code:
            self.store_code = self.store_code.strip().upper()

        if self.store_name:
            self.store_name = self.store_name.strip()

        self._validate_unique_store_name()

        if self.contact_phone:
            self.contact_phone = validate_indian_phone(self.contact_phone, "Contact Phone")

        if self.pincode and len(self.pincode.strip()) != 6:
            frappe.throw(
                frappe._("PIN Code must be exactly 6 digits."),
                title=frappe._("Invalid PIN Code"),
            )

        if self.zone:
            zone = frappe.db.get_value("CH Store Zone", self.zone, ["company", "city"], as_dict=True)
            if zone:
                if not self.city and zone.city:
                    self.city = zone.city
                if self.company and zone.company != self.company:
                    frappe.throw(
                        frappe._("Zone {0} belongs to company {1}, not {2}.").format(
                            frappe.bold(self.zone), frappe.bold(zone.company), frappe.bold(self.company)
                        ),
                        title=frappe._("Invalid Zone"),
                    )
                if self.city and zone.city and zone.city != self.city:
                    frappe.throw(
                        frappe._("Zone {0} belongs to city {1}, not {2}.").format(
                            frappe.bold(self.zone), frappe.bold(zone.city), frappe.bold(self.city)
                        ),
                        title=frappe._("Invalid Zone"),
                    )

        from ch_item_master.ch_core.location_hierarchy import validate_store_location_contract

        validate_store_location_contract(self)

    def _validate_unique_store_name(self):
        """Reject duplicate store_name within the same company.

        ``store_code`` remains the primary key (auto-generated), but two
        active stores in the same company sharing the exact same
        ``store_name`` is almost always a data-entry mistake — reports and
        dashboards key off the display name and would silently collapse
        the two. We scope the check by company because a franchise group
        legitimately runs identically-named stores under separate legal
        entities.
        """
        if not (self.store_name and self.company):
            return
        dup = frappe.db.get_value(
            "CH Store",
            {
                "store_name": self.store_name,
                "company": self.company,
                "name": ["!=", self.name or ""],
                "disabled": 0,
            },
            "name",
        )
        if dup:
            frappe.throw(
                frappe._("A store named {0} already exists for {1}: {2}.").format(
                    frappe.bold(self.store_name),
                    frappe.bold(self.company),
                    frappe.bold(dup),
                ),
                title=frappe._("Duplicate Store Name"),
            )

    def after_insert(self):
        """Auto-create the operational stock-state bins as siblings of the store warehouse."""
        ensure_store_bins(self)
        ensure_store_pos_profile(self)

    def on_update(self):
        """If warehouse is assigned later, ensure bins are created."""
        if self.has_value_changed("warehouse") and self.warehouse:
            ensure_store_bins(self)
            ensure_store_pos_profile(self)


@frappe.whitelist()
def create_pos_profile_for_store(store):
    """Idempotent whitelisted wrapper — invoked from the CH Store form button."""
    doc = frappe.get_doc("CH Store", store)
    return ensure_store_pos_profile(doc, force=True)


def ensure_store_pos_profile(store, force=False):
    """Provision a minimal, DISABLED POS Profile for a CH Store.

    Design (matches HRMS / India Compliance ``ensure_*`` helpers):
      * Skip when ``store.warehouse`` is not yet set — the sellable
        warehouse is a hard dependency of POS Profile.
      * Skip when ``store.pos_profile`` is already set, unless ``force``
        (used by the manual "Create / Refresh" button so retail-ops can
        rebuild the profile after fixing payment modes / cost centre).
      * Create the profile DISABLED. Cashiers cannot use it until an
        operator opens it, adds valid payment methods, and unchecks
        ``disabled``. This mirrors the SAP "config in draft, activate
        via change order" pattern and is safer than shipping a live
        cashier profile with default payment modes.
      * Everything is best-effort — POS Profile creation must never
        block store creation. Failures are logged and swallowed.

    Returns
    -------
    dict | None
        ``{"pos_profile": <name>, "created": bool, "disabled": bool}``
        or ``None`` when nothing was provisioned (missing prerequisites,
        best-effort skip on error).
    """
    if not store.warehouse or not store.company:
        return None

    # Only auto-fill when there is no existing profile, unless the caller
    # forced a rebuild via the desk button.
    if store.pos_profile and not force:
        return {"pos_profile": store.pos_profile, "created": False, "disabled": None}

    if not store.store_code:
        return None

    profile_name = f"POS - {store.store_code}"

    if frappe.db.exists("POS Profile", profile_name):
        # Reuse — link it back to the store if the link was dropped.
        if store.pos_profile != profile_name:
            frappe.db.set_value(
                "CH Store", store.name, "pos_profile", profile_name, update_modified=False,
            )
        disabled = frappe.db.get_value("POS Profile", profile_name, "disabled")
        return {"pos_profile": profile_name, "created": False, "disabled": bool(disabled)}

    try:
        currency = frappe.db.get_value("Company", store.company, "default_currency")
        cost_center = frappe.db.get_value("Company", store.company, "cost_center")
        income_account = frappe.db.get_value("Company", store.company, "default_income_account")
        expense_account = frappe.db.get_value(
            "Company", store.company, "default_expense_account"
        )
        write_off_account = frappe.db.get_value(
            "Company", store.company, "write_off_account"
        )

        pp = frappe.new_doc("POS Profile")
        pp.name = profile_name
        pp.company = store.company
        pp.warehouse = store.warehouse
        pp.currency = currency
        pp.disabled = 1  # cashiers cannot use until payment methods are added
        if cost_center:
            pp.cost_center = cost_center
        if income_account:
            pp.income_account = income_account
        if expense_account:
            pp.expense_account = expense_account
        if write_off_account:
            pp.write_off_account = write_off_account

        # Skip payment-methods validation on the seed insert — we want a
        # blank skeleton that a retail-ops user completes on the form.
        # ``validate_payment_methods`` throws if ``self.payments`` is empty,
        # so we bypass validate() entirely on the initial insert; the
        # user's Save will exercise full validation once modes are added.
        pp.flags.ignore_validate = True
        pp.flags.ignore_mandatory = True
        pp.insert(ignore_permissions=True)

        frappe.db.set_value(
            "CH Store", store.name, "pos_profile", pp.name, update_modified=False,
        )
        return {"pos_profile": pp.name, "created": True, "disabled": True}
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"ensure_store_pos_profile failed for {store.name}",
        )
        return None


# Operational stock-state bins created as siblings of the store warehouse.
# The store warehouse itself is the implicit "Sellable" bin (it carries
# ch_bin_type='Sellable' so all existing resolvers keep working).
# (Bin type label, suffix used in warehouse name)
#
# Path B Phase 1 cleanup (2026-06-29):
#   * Reserved   — removed; soft reservations are tracked in the
#                  reservation tables (e.g. Spare Parts Usage), no
#                  physical bin needed. Mirrors SAP/Oracle reservation
#                  semantics.
#   * Disposed   — removed; disposal posts a write-off Stock Entry to
#                  a Disposal expense account (SAP/Oracle parity).
#                  Stock leaves on-hand; no permanent "Disposed" bucket.
#   * In-Transit — removed at store level; transit is handled by the
#                  company-level `Goods In Transit - <abbr>` warehouse
#                  that ERPNext already provisions and the Material
#                  Transfer workflow uses.
#
# Path B Phase 3 (2026-06-29): the three legacy bin types above were
# hard-purged from the dev dataset and removed from the
# ``ch_bin_type`` Select options. The corresponding
# ``LEGACY_STORE_BIN_TYPES`` constant is gone — there is now exactly
# one canonical set of bin types.
#
# Phase 4 (Inventory Dimension) will eventually fold the remaining 3
# bins into a CH Stock Status dimension on the base warehouse so the
# tree stops multiplying physical warehouses by status.
STORE_BIN_TYPES = (
    ("Damaged", "Damaged"),
    # Demo: valued stock used for in-store demonstration units. Counted in
    # warehouse stock value but tagged so reports/aging can isolate it.
    ("Demo", "Demo"),
    ("Buyback", "Buyback"),
)


def ensure_store_bins(store):
    """Create the operational stock-state bins for a store.

    Architecture (Path B Phase 2, SAP/Oracle parity):
      - The store's ``warehouse`` is the Sellable LEAF (kept as a leaf so
        it can post Stock Ledger Entries directly from POS / Sales).
      - A group warehouse (the "Store Group", recorded on
        ``CH Store.warehouse_group``) sits one level above and holds the
        Sellable leaf + the operational bin leaves as siblings.
      - The Store Group is itself parented under the Zone Group, which is
        parented under the City Group, which lives under the company root.
        See ``ch_core.warehouse_geo`` for the full hierarchy contract.
      - Bin leaves (Damaged / Demo / Buyback) are CHILDREN of the Store
        Group, not siblings of the Sellable leaf.

    Idempotent.
    """
    if not store.warehouse or not store.company:
        return

    base = frappe.db.get_value(
        "Warehouse",
        store.warehouse,
        ["name", "company", "is_group", "parent_warehouse"],
        as_dict=True,
    )
    if not base:
        return

    # Resolve / create the City -> Zone -> Store Group chain so the new
    # bins land in the right place from day one. Failing the chain is non
    # fatal: bins still get created as siblings of the base warehouse.
    from ch_item_master.ch_core.warehouse_geo import ensure_store_group

    store_group = None
    try:
        store_group = ensure_store_group(store)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"ensure_store_group failed: {store.name}")

    # In the SAP-aligned tree every per-store Sellable leaf is a Store Bin;
    # the parent Store Group carries the store identity.
    base_updates = {
        "ch_city": store.city,
        "ch_zone": store.zone,
        "ch_location_type": "Store Bin",
        "ch_store": store.name,
        "ch_bin_type": "Sellable",
    }
    if store_group:
        base_updates["parent_warehouse"] = store_group
    frappe.db.set_value(
        "Warehouse",
        base.name,
        base_updates,
        update_modified=False,
    )

    # Persist the group pointer for downstream code (Location Hierarchy page,
    # reports, etc.) without disturbing CH Store.warehouse semantics.
    if store_group and store.get("warehouse_group") != store_group:
        frappe.db.set_value(
            "CH Store", store.name, "warehouse_group", store_group,
            update_modified=False,
        )

    # New bin leaves are children of the Store Group so the tree reads
    # cleanly:  Store Group -> [Sellable, Damaged, Demo, Buyback].
    bin_parent = store_group or base.parent_warehouse or None

    for bin_type, suffix in STORE_BIN_TYPES:
        existing = frappe.db.exists(
            "Warehouse",
            {
                "company": store.company,
                "ch_store": store.name,
                "ch_bin_type": bin_type,
            },
        )
        if existing:
            if bin_parent:
                current_parent = frappe.db.get_value(
                    "Warehouse", existing, "parent_warehouse"
                )
                if current_parent != bin_parent:
                    frappe.db.set_value(
                        "Warehouse", existing, "parent_warehouse", bin_parent,
                        update_modified=False,
                    )
            continue

        wh = frappe.new_doc("Warehouse")
        wh.warehouse_name = f"{store.store_code}-{suffix}"
        if bin_parent:
            wh.parent_warehouse = bin_parent
        wh.company = store.company
        wh.is_group = 0
        wh.ch_city = store.city
        wh.ch_zone = store.zone
        wh.ch_store = store.name
        wh.ch_location_type = "Store Bin"
        wh.ch_bin_type = bin_type
        try:
            wh.insert(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            # Another save raced us; safe to skip.
            continue
