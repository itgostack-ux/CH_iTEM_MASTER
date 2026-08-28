import re

import frappe
from frappe.model.document import Document
from frappe.utils import cint

from ch_item_master.id_sequences import next_free_numeric_id, next_scoped_number
from ch_item_master.security import require_scoped_document_action
from ch_item_master.ch_item_master.utils import validate_indian_phone


# Brand-prefix conventions use the prefix configured on Company.
#
# Prefix source (priority order):
#   1. ``Company.store_code_prefix`` — the generic 2-char brand tag any
#      company can set (owned by the ``gofix`` app but usable by any brand).
#   2. ``Company.gofix_enabled = 1`` — legacy fallback to the Company's own
#      abbreviation when the prefix field is not installed or populated.
#
# Anything else falls back to the deterministic
# ``STO-<ABBR>-<CITY>-####`` form.
_STORE_CODE_ALLOCATION_ATTEMPTS = 1000


def _slugify_store_name(store_name: str) -> str:
    """Turn a human store name into the uppercase alphanumeric slug used by
    the ``GG-`` / ``GF-`` prefix conventions.

    "Anna Nagar" → "ANNANAGAR", "Perambur - RS" → "PERAMBURRS",
    "Tambaram 2" → "TAMBARAM2".
    """
    return re.sub(r"[^A-Za-z0-9]", "", store_name or "").upper()


class CHStore(Document):
    def autoname(self):
        """Auto-generate ``store_code``.

        Naming rules (in priority order):
          1. Manual override — respected as-is (upper-cased + trimmed).
          2. Company with a ``store_code_prefix`` set (or legacy
             ``gofix_enabled=1``) → ``<PREFIX>-<STORE_NAME_SLUG>``
             (e.g. ``GG-ANNANAGAR``, ``GF-DOVETON``). Collisions get a
             numeric suffix (``GF-DOVETON-2``, ``-3``…). Falls back to the
             STO-... form when ``store_name`` is missing.
          3. All other companies → ``STO-{COMPANY_ABBR}-{CITY_SHORT}-####``
             (deterministic, sortable, backward-compatible).
        """
        if self.store_code:
            self.store_code = self.store_code.strip().upper()
            self.name = self.store_code
            return

        prefix = self._resolve_brand_prefix()
        if prefix and self.store_name:
            self.store_code = self._generate_prefixed_store_code(prefix)
        else:
            self.store_code = self._generate_store_code()
        self.name = self.store_code

    def _resolve_brand_prefix(self) -> str | None:
        """Return the 2-char brand prefix for this store's company, or None.

        Reads ``Company.store_code_prefix`` first; falls back to the
        historical ``gofix_enabled`` flag when the prefix field is empty so
        an incomplete migration doesn't quietly revert to STO-... codes.
        Both columns are custom fields owned by the ``gofix`` app — treat
        their absence as "no brand prefix configured".
        """
        if not self.company:
            return None
        try:
            row = frappe.db.get_value(
                "Company",
                self.company,
                ["store_code_prefix", "gofix_enabled", "abbr"],
                as_dict=True,
            )
        except Exception:
            return None
        if not row:
            return None
        prefix = (row.get("store_code_prefix") or "").strip().upper()
        if prefix:
            return prefix
        if row.get("gofix_enabled"):
            return re.sub(r"[^A-Za-z0-9]", "", row.get("abbr") or "").upper() or None
        return None

    def _generate_prefixed_store_code(self, prefix: str) -> str:
        slug = _slugify_store_name(self.store_name)
        if not slug:
            # Shouldn't normally happen — autoname already gated on
            # ``store_name``. Guard defensively so a blank slug can't
            # produce a bare "<PREFIX>-" name.
            return self._generate_store_code()
        base = f"{prefix}-{slug}"
        for _ in range(_STORE_CODE_ALLOCATION_ATTEMPTS):
            sequence = next_scoped_number("CH-STORE-CODE-BRAND", base)
            candidate = base if sequence == 1 else f"{base}-{sequence}"
            if not frappe.db.exists("CH Store", candidate):
                return candidate
        frappe.throw(frappe._("Could not allocate a unique store code. Please retry."))

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

        for _ in range(_STORE_CODE_ALLOCATION_ATTEMPTS):
            sequence = next_scoped_number("CH-STORE-CODE-STANDARD", prefix)
            candidate = f"{prefix}{sequence:04d}"
            if not frappe.db.exists("CH Store", candidate):
                return candidate
        frappe.throw(frappe._("Could not allocate a unique store code. Please retry."))

    def before_insert(self):
        """Auto-assign the atomic sequential integration ID."""
        if not self.store_id:
            self.store_id = next_free_numeric_id("store")

    def validate(self):
        if self.store_code:
            self.store_code = self.store_code.strip().upper()

        if self.store_name:
            self.store_name = self.store_name.strip()

        self._validate_unique_store_name()

        if self.contact_phone:
            self.contact_phone = validate_indian_phone(self.contact_phone, "Contact Phone")

        if self.pincode:
            self.pincode = self.pincode.strip()
            if not re.fullmatch(r"\d{6}", self.pincode):
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

        self._validate_operational_location()
        self._validate_geography()
        self._validate_branch_location()

        from ch_item_master.ch_core.location_hierarchy import validate_store_location_contract

        validate_store_location_contract(self)

    def _requires_operational_location(self):
        return not cint(self.disabled) and (self.store_status or "Active") == "Active"

    def _validate_operational_location(self):
        """An active store is an operating unit, not a loose address label."""
        if not self._requires_operational_location():
            return
        missing = [
            label
            for fieldname, label in (("company", "Company"), ("city", "City"), ("zone", "Zone"))
            if not self.get(fieldname)
        ]
        if missing:
            frappe.throw(
                frappe._("Active stores require: {0}.").format(", ".join(missing)),
                title=frappe._("Incomplete Store Location"),
            )
        if cint(self.is_hub) and not self.warehouse:
            frappe.throw(
                frappe._("Active hub stores require an existing hub Warehouse."),
                title=frappe._("Missing Hub Warehouse"),
            )

    def _validate_geography(self):
        if self.city:
            city = frappe.db.get_value("CH City", self.city, ["state", "disabled"], as_dict=True)
            if not city or cint(city.disabled):
                frappe.throw(
                    frappe._("City {0} was not found or is disabled.").format(frappe.bold(self.city)),
                    title=frappe._("Invalid City"),
                )
            if city.state and not self.state:
                self.state = city.state
            elif city.state and self.state and city.state != self.state:
                frappe.throw(
                    frappe._("City {0} belongs to state {1}, not {2}.").format(
                        frappe.bold(self.city), frappe.bold(city.state), frappe.bold(self.state)
                    ),
                    title=frappe._("Invalid State"),
                )

        if self.pincode and frappe.db.exists("CH Pincode", self.pincode):
            pin = frappe.db.get_value(
                "CH Pincode", self.pincode, ["city", "state", "disabled"], as_dict=True
            )
            if cint(pin.disabled):
                frappe.throw(
                    frappe._("PIN Code {0} is disabled.").format(frappe.bold(self.pincode)),
                    title=frappe._("Invalid PIN Code"),
                )
            if self.city and pin.city and pin.city != self.city:
                frappe.throw(
                    frappe._("PIN Code {0} belongs to city {1}, not {2}.").format(
                        frappe.bold(self.pincode), frappe.bold(pin.city), frappe.bold(self.city)
                    ),
                    title=frappe._("Invalid PIN Code"),
                )
            if self.state and pin.state and pin.state != self.state:
                frappe.throw(
                    frappe._("PIN Code {0} belongs to state {1}, not {2}.").format(
                        frappe.bold(self.pincode), frappe.bold(pin.state), frappe.bold(self.state)
                    ),
                    title=frappe._("Invalid PIN Code"),
                )

    def _validate_branch_location(self):
        if not self.branch:
            return
        branch = frappe.db.get_value(
            "Branch", self.branch, ["ch_company", "ch_city", "ch_zone"], as_dict=True
        )
        if not branch:
            frappe.throw(frappe._("Branch {0} was not found.").format(frappe.bold(self.branch)))
        checks = (
            ("company", "ch_company", "company"),
            ("city", "ch_city", "city"),
            ("zone", "ch_zone", "zone"),
        )
        for store_field, branch_field, label in checks:
            branch_value = branch.get(branch_field)
            store_value = self.get(store_field)
            if branch_value and store_value and branch_value != store_value:
                frappe.throw(
                    frappe._("Branch {0} belongs to {1} {2}, not {3}.").format(
                        frappe.bold(self.branch), label, frappe.bold(branch_value), frappe.bold(store_value)
                    ),
                    title=frappe._("Invalid Branch"),
                )

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
        """Provision the standard Warehouse tree even for direct Desk/API inserts."""
        if self._requires_operational_location() and not cint(self.is_hub) and not self.warehouse:
            from ch_item_master.ch_core.warehouse_geo import provision_store_warehouse

            provision_store_warehouse(self.name)
            self.reload()
        _ensure_store_cost_center_best_effort(self)
        ensure_store_bins(self)
        ensure_store_pos_profile(self)

    def on_update(self):
        """Keep accounting/warehouse provisioning aligned with store master data."""
        if any(
            self.has_value_changed(fieldname)
            for fieldname in ("company", "zone", "city", "state", "is_hub")
        ):
            _ensure_store_cost_center_best_effort(self)
        location_changed = any(
            self.has_value_changed(fieldname)
            for fieldname in ("company", "zone", "city", "warehouse", "is_hub")
        )
        if location_changed and self.warehouse:
            if not cint(self.is_hub):
                from ch_item_master.ch_core.warehouse_geo import restructure_store_tree

                ensure_store_bins(self)
                restructure_store_tree(self.name)
            ensure_store_pos_profile(self)


@frappe.whitelist(methods=["POST"])
def create_pos_profile_for_store(store):
    """Provision a scoped store's disabled POS Profile with standard permissions."""
    doc = frappe.get_doc("CH Store", store)
    require_scoped_document_action(
        doc,
        "location_manager_roles",
        action=frappe._("create a POS Profile for a store"),
        permission_types=("write",),
        company_field="company",
        store_field="name",
        lock=True,
    )
    return _create_store_pos_profile_with_permissions(doc)


def _create_store_pos_profile_with_permissions(store):
    if not store.company or not store.warehouse:
        frappe.throw(
            frappe._("Company and a sellable Warehouse are required before creating a POS Profile."),
            frappe.ValidationError,
        )

    company = frappe.get_doc("Company", store.company)
    company.check_permission("read")
    warehouse = frappe.get_doc("Warehouse", store.warehouse)
    warehouse.check_permission("read")
    if warehouse.company != store.company:
        frappe.throw(frappe._("Store warehouse belongs to another company."), frappe.ValidationError)
    if warehouse.is_group:
        frappe.throw(frappe._("A group Warehouse cannot be used by a POS Profile."), frappe.ValidationError)
    if warehouse.get("disabled"):
        frappe.throw(frappe._("The store warehouse is disabled."), frappe.ValidationError)

    store_cost_center = ensure_store_cost_center(store)

    candidate_names = [name for name in (store.pos_profile, f"POS - {store.store_code}") if name]
    for profile_name in dict.fromkeys(candidate_names):
        if not frappe.db.exists("POS Profile", profile_name):
            continue
        profile = frappe.get_doc("POS Profile", profile_name)
        profile.check_permission("read")
        if profile.company != store.company or profile.warehouse != store.warehouse:
            frappe.throw(
                frappe._("Existing POS Profile company and warehouse must match the store."),
                frappe.ValidationError,
            )
        assign_pos_profile_cost_center(profile.name, store_cost_center, store.company)
        if store.pos_profile != profile.name:
            store.pos_profile = profile.name
            store.save()
        return {
            "pos_profile": profile.name,
            "created": False,
            "disabled": bool(profile.disabled),
        }

    if not frappe.has_permission("POS Profile", ptype="create", print_logs=False):
        frappe.throw(
            frappe._("You do not have create permission for POS Profile."),
            frappe.PermissionError,
        )
    profile = frappe.new_doc("POS Profile")
    profile.name = f"POS - {store.store_code}"
    profile.company = store.company
    profile.warehouse = store.warehouse
    profile.currency = company.default_currency
    profile.disabled = 1
    for fieldname, value in (
        ("cost_center", store_cost_center or company.cost_center),
        ("income_account", company.default_income_account),
        ("expense_account", company.default_expense_account),
        ("write_off_account", company.write_off_account),
    ):
        if value:
            profile.set(fieldname, value)

    profile.flags.ignore_validate = True
    profile.flags.ignore_mandatory = True
    profile.insert()
    profile.check_permission("read")

    store.pos_profile = profile.name
    store.save()
    return {"pos_profile": profile.name, "created": True, "disabled": True}


def _get_root_cost_center(company):
    """Return the company's root Cost Center without assuming its name."""
    if not company or not frappe.db.table_exists("Cost Center"):
        return None
    return (
        frappe.db.get_value(
            "Cost Center",
            {"company": company, "is_group": 1, "parent_cost_center": ("in", [None, ""])},
            "name",
        )
        or frappe.db.get_value(
            "Cost Center", {"company": company, "is_group": 1}, "name"
        )
    )


_RETAIL_COST_CENTER_LABEL = "Retail Stores"
_REGION_COST_CENTER_PREFIX = "Region - "


def _ensure_cost_center_node(company, label, parent, *, is_group):
    """Return an idempotent Cost Center node, creating it only when absent."""
    existing = frappe.db.get_value(
        "Cost Center",
        {"company": company, "cost_center_name": label},
        ["name", "parent_cost_center", "is_group"],
        as_dict=True,
    )
    if not existing:
        doc = frappe.get_doc(
            {
                "doctype": "Cost Center",
                "cost_center_name": label,
                "parent_cost_center": parent,
                "company": company,
                "is_group": cint(is_group),
            }
        )
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        return doc.name

    if cint(existing.is_group) != cint(is_group):
        frappe.throw(
            frappe._(
                "Cost Center {0} already exists but has the wrong group setting."
            ).format(frappe.bold(existing.name)),
            frappe.ValidationError,
        )

    if parent and existing.parent_cost_center != parent:
        current_label = frappe.db.get_value(
            "Cost Center", existing.parent_cost_center, "cost_center_name"
        )
        root = _get_root_cost_center(company)
        managed_parent = (
            existing.parent_cost_center == root
            or current_label == _RETAIL_COST_CENTER_LABEL
            or (current_label or "").startswith(_REGION_COST_CENTER_PREFIX)
        )
        # Re-parent nodes created by this provisioning scheme, including the
        # legacy store leaves that were placed directly below Company root.
        # A deliberately chosen custom parent is preserved.
        if managed_parent:
            doc = frappe.get_doc("Cost Center", existing.name)
            doc.parent_cost_center = parent
            doc.flags.ignore_permissions = True
            doc.save(ignore_permissions=True)
    return existing.name


def get_store_region_label(store):
    """Return the stable management region available on the Store master."""
    if isinstance(store, str):
        store = frappe.get_doc("CH Store", store)
    region = (store.get("zone") or "").strip()
    if region:
        return region
    city = (store.get("city") or "").strip()
    if city:
        return (
            frappe.db.get_value("CH City", city, "city_name") or city
        ).strip()
    return (store.get("state") or "").strip() or "Unassigned"


def ensure_store_cost_center_hierarchy(store):
    """Ensure Company → Retail Stores → Region → Store Cost Center."""
    if isinstance(store, str):
        store = frappe.get_doc("CH Store", store)
    if not store or cint(store.get("is_hub")) or not store.company:
        return None

    root = _get_root_cost_center(store.company)
    if not root:
        return None
    retail = _ensure_cost_center_node(
        store.company, _RETAIL_COST_CENTER_LABEL, root, is_group=True
    )
    region_label = f"{_REGION_COST_CENTER_PREFIX}{get_store_region_label(store)}"
    region = _ensure_cost_center_node(
        store.company, region_label, retail, is_group=True
    )
    return {"root": root, "retail": retail, "region": region}


def _ensure_store_cost_center_best_effort(store):
    try:
        return ensure_store_cost_center(store)
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"ensure_store_cost_center failed for {store.name}",
        )
        return None


def assign_pos_profile_cost_center(pos_profile, cost_center, company=None):
    """Safely default a POS Profile to its store Cost Center.

    Blank values and the Company default are configuration defaults, so they
    may be replaced. An operator-selected non-default Cost Center is preserved
    to avoid silently changing an intentional accounting design.
    """
    if not pos_profile or not cost_center or not frappe.db.exists("POS Profile", pos_profile):
        return False

    row = frappe.db.get_value(
        "POS Profile", pos_profile, ["company", "cost_center"], as_dict=True
    )
    if not row or (company and row.company != company):
        return False

    company = company or row.company
    company_default = frappe.db.get_value("Company", company, "cost_center")
    current = row.cost_center or None
    if current not in (None, "", company_default, cost_center):
        return False
    if current != cost_center:
        frappe.db.set_value(
            "POS Profile", pos_profile, "cost_center", cost_center, update_modified=False
        )
    frappe.clear_document_cache("POS Profile", pos_profile)
    return True


def ensure_store_cost_center(store, pos_profile=None):
    """Create and assign the stable per-store Cost Center.

    The label is based on ``store_code`` rather than the editable display name,
    so renaming a store does not split its accounting history. The operation is
    idempotent and deliberately non-destructive for explicitly configured POS
    Profiles.
    """
    if isinstance(store, str):
        store = frappe.get_doc("CH Store", store)
    if not store or cint(store.get("is_hub")) or not store.company or not store.store_code:
        return None

    hierarchy = ensure_store_cost_center_hierarchy(store)
    parent = hierarchy.get("region") if hierarchy else None
    if not parent:
        return None

    label = f"POS - {store.store_code}"
    cost_center = _ensure_cost_center_node(
        store.company, label, parent, is_group=False
    )

    profile_name = pos_profile or store.get("pos_profile") or f"POS - {store.store_code}"
    assign_pos_profile_cost_center(profile_name, cost_center, store.company)
    return cost_center


def backfill_store_pos_profiles():
    """Ensure every enabled CH Store has its default (disabled) POS Profile.

    ``ensure_store_pos_profile`` only fires on store insert / warehouse
    change / the form button, so stores that predate it (or were seeded
    while a prerequisite was missing) stay without a profile forever.
    This after_migrate backfill heals them: every enabled store with a
    sellable warehouse gets its ``POS - <store_code>`` skeleton so
    retail-ops only has to add payment modes and untick ``disabled``.

    Idempotent. Safe to run repeatedly from after_migrate.
    """
    if not frappe.db.table_exists("CH Store") or not frappe.db.table_exists("POS Profile"):
        return

    stores = frappe.get_all(
        "CH Store",
        filters={"disabled": 0, "warehouse": ("is", "set"), "pos_profile": ("is", "not set")},
        pluck="name",
    )
    created = linked = 0
    for name in stores:
        try:
            store = frappe.get_doc("CH Store", name)
            result = ensure_store_pos_profile(store)
            if result and result.get("created"):
                created += 1
            elif result and result.get("pos_profile"):
                linked += 1
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"backfill_store_pos_profiles: {name}")
    if created or linked:
        print(f"backfill_store_pos_profiles: created={created} relinked={linked}")


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
    if cint(store.get("is_hub")):
        # Hubs / distribution centres are not cashier locations — never
        # provision a POS Profile for them.
        return None
    if not store.warehouse or not store.company:
        return None

    try:
        store_cost_center = ensure_store_cost_center(store, pos_profile=store.pos_profile)
    except Exception:
        # Finance provisioning is important but must not make the Store master
        # impossible to create. The POS skeleton falls back to Company default
        # and after_migrate will retry the idempotent backfill.
        frappe.log_error(
            frappe.get_traceback(),
            f"ensure_store_cost_center failed for {store.name}",
        )
        store_cost_center = None

    # Only auto-fill when there is no existing profile, unless the caller
    # forced a rebuild via the desk button.
    if store.pos_profile and not force:
        if store_cost_center:
            assign_pos_profile_cost_center(store.pos_profile, store_cost_center, store.company)
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
        assign_pos_profile_cost_center(profile_name, store_cost_center, store.company)
        disabled = frappe.db.get_value("POS Profile", profile_name, "disabled")
        return {"pos_profile": profile_name, "created": False, "disabled": bool(disabled)}

    try:
        currency = frappe.db.get_value("Company", store.company, "default_currency")
        cost_center = store_cost_center or frappe.db.get_value(
            "Company", store.company, "cost_center"
        )
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
        pp.insert()

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
    # Customer Device: handsets we are holding but do not own — a repair booked
    # in over the counter. Received at zero valuation, so the balance sheet is
    # untouched, but the device becomes a real object the system can move,
    # scan and account for. This is customer special stock in SAP's sense, and
    # it is what lets a repair travel on a manifest with a driver rather than
    # vanishing from the record the moment it leaves the shop.
    ("Customer Device", "CustomerDevice"),
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
    if cint(store.get("is_hub")):
        # Hubs / distribution centres keep their own warehouse hierarchy
        # (hub bins under the city group — see ch_core.warehouse_geo).
        # Never re-parent the hub warehouse under a retail Store Group or
        # create Damaged / Demo / Buyback retail bins for it.
        return
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
        if not existing:
            # Legacy provisioning created the correctly named leaf but did not
            # always stamp its ownership metadata. Adopt it instead of trying
            # to insert the same warehouse_name and swallowing DuplicateEntry.
            base_name = f"{store.store_code}-{suffix}"
            company_abbr = frappe.get_cached_value("Company", store.company, "abbr")
            canonical_name = f"{base_name} - {company_abbr}" if company_abbr else base_name
            existing = frappe.db.exists("Warehouse", canonical_name) or frappe.db.exists(
                "Warehouse",
                {"company": store.company, "warehouse_name": ("in", (base_name, canonical_name)), "is_group": 0},
            )
        if existing:
            updates = {
                "ch_city": store.city,
                "ch_zone": store.zone,
                "ch_location_type": "Store Bin",
                "ch_store": store.name,
                "ch_bin_type": bin_type,
            }
            if bin_parent:
                updates["parent_warehouse"] = bin_parent
            frappe.db.set_value("Warehouse", existing, updates, update_modified=False)
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
            wh.insert()
        except frappe.DuplicateEntryError:
            # Another save raced us; safe to skip.
            continue
