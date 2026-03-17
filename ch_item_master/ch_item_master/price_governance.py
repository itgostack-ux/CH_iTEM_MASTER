# Copyright (c) 2026, GoStack and contributors
# Price Governance — hooks for CH Item Price & Buyback Price Master
#
# Responsibilities:
#   1. Block direct edits (enforce maker/checker batch process)
#   2. Auto-log every change to CH Price Change Log for audit trail

import frappe
from frappe import _
from frappe.utils import now_datetime, flt


# ─────────────────────────────────────────────────────────────────────────────
# Flags used by batch-apply and Ready Reckoner dialogs to bypass the block
# ─────────────────────────────────────────────────────────────────────────────
# When applying changes from a batch or from the Ready Reckoner quick-price
# dialog, set doc.flags.from_price_batch = True or doc.flags.from_ready_reckoner = True
# before calling save/insert. This skips the direct-edit block.

_PRICE_FIELDS = {"mrp", "mop", "selling_price"}
_BUYBACK_PRICE_FIELDS = {
    "current_market_price", "vendor_price",
    "a_grade_iw_0_3", "b_grade_iw_0_3", "c_grade_iw_0_3",
    "a_grade_iw_0_6", "b_grade_iw_0_6", "c_grade_iw_0_6", "d_grade_iw_0_6",
    "a_grade_iw_6_11", "b_grade_iw_6_11", "c_grade_iw_6_11", "d_grade_iw_6_11",
    "a_grade_oow_11", "b_grade_oow_11", "c_grade_oow_11", "d_grade_oow_11",
}

# Roles allowed to make direct edits (bypass the batch requirement)
# NOTE: No admin bypass — all users must go through the batch workflow.
# Only programmatic bypasses (from_price_batch, from_ready_reckoner, ignore_price_governance) are allowed.


def _is_bypassed(doc):
    """Check if this save was triggered from an approved process."""
    return (
        getattr(doc.flags, "from_price_batch", False)
        or getattr(doc.flags, "from_ready_reckoner", False)
        or getattr(doc.flags, "ignore_price_governance", False)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Validate hooks — block direct edits
# ─────────────────────────────────────────────────────────────────────────────

def validate_ch_item_price(doc, method=None):
    """Block direct edits to CH Item Price if not from an approved source."""
    if _is_bypassed(doc):
        return

    # Allow new records (creation) — only block price field changes on existing docs
    if doc.is_new():
        return

    # Check if any price field actually changed
    old = doc.get_doc_before_save()
    if not old:
        return

    changed_fields = []
    for f in _PRICE_FIELDS:
        if flt(doc.get(f) or 0, 2) != flt(old.get(f) or 0, 2):
            changed_fields.append(f)

    if changed_fields:
        frappe.throw(
            _("Direct price edits are not allowed. Please use the "
              "<b>Ready Reckoner → Upload Prices</b> workflow (maker/checker) "
              "to update prices.<br><br>"
              "Changed fields: {0}").format(", ".join(changed_fields)),
            title=_("Price Governance"),
        )


def validate_buyback_price(doc, method=None):
    """Block direct edits to Buyback Price Master if not from an approved source."""
    if _is_bypassed(doc):
        return

    if doc.is_new():
        return

    old = doc.get_doc_before_save()
    if not old:
        return

    changed_fields = []
    for f in _BUYBACK_PRICE_FIELDS:
        if flt(doc.get(f) or 0, 2) != flt(old.get(f) or 0, 2):
            changed_fields.append(f)

    if changed_fields:
        frappe.throw(
            _("Direct buyback price edits are not allowed. Please use the "
              "<b>Ready Reckoner → Upload Prices</b> workflow (maker/checker) "
              "to update prices.<br><br>"
              "Changed fields: {0}").format(", ".join(changed_fields)),
            title=_("Price Governance"),
        )


# ─────────────────────────────────────────────────────────────────────────────
# On-update hooks — auto-log price changes
# ─────────────────────────────────────────────────────────────────────────────

def log_ch_item_price_change(doc, method=None):
    """Log any price field change on CH Item Price to the audit trail."""
    # Skip if the change was from a batch (batch controller logs its own entries)
    if getattr(doc.flags, "from_price_batch", False):
        return

    old = doc.get_doc_before_save()
    if not old:
        # New record — log creation
        for f in _PRICE_FIELDS:
            val = float(doc.get(f) or 0)
            if val:
                _create_log(
                    item_code=doc.item_code,
                    channel=doc.channel,
                    change_type="Selling Price",
                    field_name=f,
                    field_label=f.replace("_", " ").title(),
                    old_value="0",
                    new_value=str(val),
                    source="Ready Reckoner Dialog" if getattr(doc.flags, "from_ready_reckoner", False) else "Direct Edit",
                )
        return

    for f in _PRICE_FIELDS:
        old_val = float(old.get(f) or 0)
        new_val = float(doc.get(f) or 0)
        if old_val != new_val:
            _create_log(
                item_code=doc.item_code,
                channel=doc.channel,
                change_type="Selling Price",
                field_name=f,
                field_label=f.replace("_", " ").title(),
                old_value=str(old_val),
                new_value=str(new_val),
                source="Ready Reckoner Dialog" if getattr(doc.flags, "from_ready_reckoner", False) else "Direct Edit",
            )


def log_buyback_price_change(doc, method=None):
    """Log any buyback price field change to the audit trail."""
    if getattr(doc.flags, "from_price_batch", False):
        return

    old = doc.get_doc_before_save()
    if not old:
        for f in _BUYBACK_PRICE_FIELDS:
            val = float(doc.get(f) or 0)
            if val:
                _create_log(
                    item_code=doc.item_code,
                    channel="Buyback",
                    change_type="Buyback Price",
                    field_name=f,
                    field_label=f.replace("_", " ").title(),
                    old_value="0",
                    new_value=str(val),
                    source="Ready Reckoner Dialog" if getattr(doc.flags, "from_ready_reckoner", False) else "Direct Edit",
                )
        return

    for f in _BUYBACK_PRICE_FIELDS:
        old_val = float(old.get(f) or 0)
        new_val = float(doc.get(f) or 0)
        if old_val != new_val:
            _create_log(
                item_code=doc.item_code,
                channel="Buyback",
                change_type="Buyback Price",
                field_name=f,
                field_label=f.replace("_", " ").title(),
                old_value=str(old_val),
                new_value=str(new_val),
                source="Ready Reckoner Dialog" if getattr(doc.flags, "from_ready_reckoner", False) else "Direct Edit",
            )


def _create_log(item_code, channel, change_type, field_name, field_label,
                old_value, new_value, source, batch_ref=None, reason=None):
    """Insert a CH Price Change Log entry."""
    log = frappe.new_doc("CH Price Change Log")
    log.item_code = item_code
    log.channel = channel or ""
    log.change_type = change_type
    log.field_name = field_name
    log.field_label = field_label
    log.old_value = old_value
    log.new_value = new_value
    log.source = source
    log.batch_ref = batch_ref
    log.reason = reason or ""
    log.changed_by = frappe.session.user
    log.changed_at = now_datetime()
    log.insert(ignore_permissions=True)
