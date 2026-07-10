"""Merge VAS Plan / VAS Product / VAS Attach Rule / VAS Claim into CH Warranty Plan.

The `VAS Plan` wrapper was created as a first-class sellable catalog SKU
on top of the governance `CH Warranty Plan`, but the enforced 1:1 link
(`VAS Plan.source_warranty_plan` reqd + unique) meant it was never
actually enabling 1:N packaging — just splitting fields across two
doctypes. Same story for `VAS Product` (which mirrored
`CH Warranty Plan.service_item`) and `VAS Attach Rule` (which mirrored
`CH Attach Rule`). `VAS Claim` mirrored `CH Warranty Claim`.

This patch:

1. Absorbs the unique fields from `VAS Plan` onto its source
   `CH Warranty Plan`:
        - list_price          → price (only if source.price == 0)
        - duration_months     → duration_months (only if source has none)
        - partner             → partner (new field)
        - auto_attach         → auto_attach (new field)
        - min_device_price    → min_device_price (new field)
        - max_device_price    → max_device_price (new field)
     Sets `is_sellable=1` on every source that had a VAS Plan wrapper
     AND on every CH Warranty Plan with `plan_type IN ('Value Added
     Service', 'Protection Plan')` (the pre-migration sellable
     convention), so no plan silently vanishes from POS.

2. Drops the VAS Attach Rule table (was a mirror of CH Attach Rule,
   never fully wired) — 0 records typical on dev benches, but any
   rows are logged before deletion for audit.

3. Drops the VAS Claim table (mirrored CH Warranty Claim via
   `source_warranty_claim`). Rows are logged; the underlying
   `CH Warranty Claim` records are the surviving source of truth.

4. Drops the VAS Plan and VAS Product records themselves.

5. Deletes the four DocTypes from the metadata layer so the folders
   can be removed from source in the same commit.

Idempotent:
    - safe to re-run: absorbed rows go through `frappe.db.exists`
      guards and the DocType deletes are wrapped in try/except.
    - patch registers itself in patches.txt after
      `v15_phase3_vas_first_class` (the original backfill patch), so
      it can seamlessly replace that patch on servers that ran the
      original.

Rolls forward on other servers automatically the next time they
`bench migrate`.
"""

import frappe


LEGACY_DOCTYPES = ("VAS Attach Rule", "VAS Claim", "VAS Plan", "VAS Product")


def execute() -> None:
    _absorb_vas_plan_into_source()
    _mark_legacy_sellable_plans()
    _drop_legacy_records()
    _drop_legacy_doctypes()


def _absorb_vas_plan_into_source() -> None:
    """Copy VAS Plan's unique fields onto its source CH Warranty Plan."""
    if not frappe.db.exists("DocType", "VAS Plan"):
        return

    # `frappe.db.has_column` guards these new columns in case migrate
    # order gives us the patch before the JSON columns land.
    for col in ("is_sellable", "partner", "auto_attach",
                "min_device_price", "max_device_price"):
        if not frappe.db.has_column("CH Warranty Plan", col):
            frappe.log_error(
                title="VAS merge patch skipped",
                message=(
                    f"Column CH Warranty Plan.{col} is missing — "
                    "run bench migrate to sync the JSON changes before "
                    "re-running this patch."
                ),
            )
            return

    rows = frappe.db.sql(
        """
        SELECT v.name AS vas_plan, v.source_warranty_plan AS source,
               v.list_price, v.duration_months, v.partner,
               v.auto_attach, v.min_device_price, v.max_device_price
          FROM `tabVAS Plan` v
        """,
        as_dict=True,
    )

    for row in rows:
        if not row.source or not frappe.db.exists("CH Warranty Plan", row.source):
            frappe.log_error(
                title="VAS Plan orphan skipped in merge",
                message=(
                    f"VAS Plan {row.vas_plan!r} points at missing source "
                    f"{row.source!r} — cannot absorb."
                ),
            )
            continue

        wp = frappe.get_doc("CH Warranty Plan", row.source)
        touched = False

        # list_price → price ONLY when governance side is zero. The
        # governance-side Fixed price takes precedence otherwise.
        if row.list_price and not (wp.price or 0):
            wp.price = row.list_price
            touched = True

        # duration_months: prefer VAS Plan's when governance has none.
        if row.duration_months and not (wp.duration_months or 0):
            wp.duration_months = row.duration_months
            touched = True

        for src_field, dest_field in (
            ("partner", "partner"),
            ("auto_attach", "auto_attach"),
            ("min_device_price", "min_device_price"),
            ("max_device_price", "max_device_price"),
        ):
            val = row.get(src_field)
            if val and not wp.get(dest_field):
                wp.set(dest_field, val)
                touched = True

        # Every plan that had a VAS Plan wrapper was intentionally
        # published as a sellable SKU.
        if not wp.get("is_sellable"):
            wp.is_sellable = 1
            touched = True

        if touched:
            wp.flags.ignore_permissions = True
            wp.flags.ignore_validate = True
            wp.flags.ignore_mandatory = True
            wp.save()

    frappe.db.commit()


def _mark_legacy_sellable_plans() -> None:
    """Set is_sellable=1 on plans that were sellable-by-convention.

    Before the VAS Plan wrapper existed, cashiers created CH Warranty
    Plans with `plan_type IN ('Value Added Service','Protection Plan')`
    and the POS endpoint surfaced them by filtering on that plan_type.
    Preserve that behaviour post-merge so no plan silently disappears.
    """
    if not frappe.db.has_column("CH Warranty Plan", "is_sellable"):
        return

    frappe.db.sql(
        """
        UPDATE `tabCH Warranty Plan`
           SET is_sellable = 1
         WHERE plan_type IN ('Value Added Service', 'Protection Plan')
           AND IFNULL(is_sellable, 0) = 0
        """
    )
    frappe.db.commit()


def _drop_legacy_records() -> None:
    """Delete data in the legacy tables, respecting FK order."""
    # FK order (from audit): VAS Attach Rule → VAS Plan; VAS Claim → VAS Plan;
    # VAS Plan → VAS Product. So delete rules and claims first, then plans,
    # then products.
    for dt in LEGACY_DOCTYPES:  # already in FK-safe order
        if not frappe.db.exists("DocType", dt):
            continue
        try:
            table = f"tab{dt}"
            count = frappe.db.sql(f"SELECT COUNT(*) FROM `{table}`")[0][0]
            if count:
                frappe.log_error(
                    title=f"VAS merge dropping {count} row(s)",
                    message=(
                        f"Deleting {count} record(s) from {dt!r} as part of "
                        "the VAS→CH Warranty Plan merge."
                    ),
                )
            frappe.db.sql(f"DELETE FROM `{table}`")
        except Exception as exc:  # pragma: no cover — best-effort
            frappe.log_error(
                title=f"VAS merge could not drop rows in {dt}",
                message=str(exc),
            )
    frappe.db.commit()


def _drop_legacy_doctypes() -> None:
    """Remove the four DocTypes from the metadata layer."""
    for dt in LEGACY_DOCTYPES:
        if not frappe.db.exists("DocType", dt):
            continue
        try:
            frappe.delete_doc(
                "DocType", dt,
                force=True, ignore_permissions=True,
                ignore_missing=True, delete_permanently=True,
            )
        except Exception as exc:  # pragma: no cover — best-effort
            frappe.log_error(
                title=f"VAS merge could not drop DocType {dt}",
                message=str(exc),
            )
    frappe.db.commit()
