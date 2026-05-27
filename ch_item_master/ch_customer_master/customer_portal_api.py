import json

import frappe
from frappe import _
from frappe.utils import cint, flt, validate_email_address

from buyback.api import get_assessments_by_phone, get_orders_by_phone
from buyback.buyback.whatsapp_notifications import _get_email_for_mobile, send_otp_email
from buyback.utils import validate_indian_phone
from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog
from ch_item_master.ch_core.whatsapp import send_template_message

PORTAL_SESSION_PREFIX = "customer_portal_session:"
PORTAL_SESSION_TTL = 1800


def _create_portal_session(mobile_no: str, customer: str | None = None) -> str:
    session_token = frappe.generate_hash(length=32)
    payload = {"mobile_no": mobile_no, "customer": customer or ""}
    frappe.cache().set_value(
        f"{PORTAL_SESSION_PREFIX}{session_token}",
        json.dumps(payload),
        expires_in_sec=PORTAL_SESSION_TTL,
    )
    return session_token


def _get_portal_session(session_token: str) -> dict:
    session_token = (session_token or "").strip()
    if not session_token:
        frappe.throw(_("Sign in again to continue."), title=_("Session Expired"))

    cached = frappe.cache().get_value(f"{PORTAL_SESSION_PREFIX}{session_token}")
    if not cached:
        frappe.throw(_("Your portal session has expired. Request a new OTP."), title=_("Session Expired"))

    if isinstance(cached, bytes):
        cached = cached.decode("utf-8")
    payload = json.loads(cached)
    payload["mobile_no"] = validate_indian_phone(payload.get("mobile_no"), "Mobile No")
    return payload


def _get_customer_profile(customer: str | None, mobile_no: str) -> dict:
    profile = {
        "customer": customer or "",
        "customer_name": "Guest Customer",
        "mobile_no": mobile_no,
        "email_id": "",
        "membership_id": "",
        "city": "",
        "state": "",
        "pincode": "",
    }
    if not customer:
        return profile

    cust = frappe.get_doc("Customer", customer)
    profile.update(
        {
            "customer_name": cust.customer_name,
            "email_id": cust.email_id or "",
            "membership_id": cust.get("ch_membership_id") or "",
        }
    )
    if cust.primary_address and frappe.db.exists("Address", cust.primary_address):
        addr = frappe.get_doc("Address", cust.primary_address)
        profile.update(
            {
                "city": addr.city or "",
                "state": addr.state or "",
                "pincode": addr.pincode or "",
            }
        )
    return profile


def _get_store_credit_wallet(customer: str | None) -> dict:
    if not customer or not frappe.db.exists("DocType", "Store Credit Wallet"):
        return {"current_balance": 0, "last_voucher": ""}

    wallet = frappe.db.get_value(
        "Store Credit Wallet",
        {"customer": customer},
        ["name", "current_balance", "last_voucher"],
        as_dict=True,
    )
    return wallet or {"current_balance": 0, "last_voucher": ""}


def _get_loyalty_snapshot(customer: str | None, company: str | None = None) -> dict:
    if not customer:
        return {
            "loyalty_program": None,
            "points": 0,
            "conversion_factor": 0,
            "currency_value": 0,
            "tier_name": "",
        }

    if not company:
        company = frappe.defaults.get_user_default("Company")

    loyalty_program = frappe.db.get_value("Customer", customer, "loyalty_program")
    if not loyalty_program:
        return {
            "loyalty_program": None,
            "points": 0,
            "conversion_factor": 0,
            "currency_value": 0,
            "tier_name": "",
        }

    from erpnext.accounts.doctype.loyalty_program.loyalty_program import get_loyalty_program_details_with_points

    details = get_loyalty_program_details_with_points(customer, loyalty_program, company=company)
    conversion_factor = flt(details.get("conversion_factor"))
    points = cint(details.get("loyalty_points"))

    return {
        "loyalty_program": loyalty_program,
        "points": points,
        "conversion_factor": conversion_factor,
        "currency_value": flt(points * conversion_factor),
        "tier_name": details.get("tier_name", ""),
    }


@frappe.whitelist(allow_guest=True)
def request_login_otp(mobile_no: str, customer_name: str = "Customer", email_id: str | None = None) -> dict:
    mobile_no = validate_indian_phone(mobile_no, "Mobile No")
    to_email = (email_id or "").strip()
    if to_email:
        to_email = validate_email_address(to_email, throw=True)

    otp_code = CHOTPLog.generate_otp(
        mobile_no=mobile_no,
        purpose="POS Customer Verification",
        reference_doctype="Customer",
        reference_name="",
    )

    sent_whatsapp = False
    sent_email = False
    try:
        wa_settings = frappe.get_cached_doc("CH WhatsApp Settings")
        if wa_settings and cint(wa_settings.enabled):
            template_name = wa_settings.get("general_otp") or "ch_otp_verification"
            send_template_message(
                phone=mobile_no,
                template_name=template_name,
                body_values={"1": otp_code},
                customer_name=(customer_name or "Customer")[:140],
                ref_doctype="Customer",
                ref_name="",
                enqueue=False,
            )
            sent_whatsapp = True
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Customer portal WhatsApp OTP delivery failed")

    try:
        if not to_email:
            to_email = _get_email_for_mobile(mobile_no)
        if to_email:
            sent_email = send_otp_email(to_email, otp_code, "Customer Portal Login", "")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Customer portal OTP email delivery failed")

    if not sent_whatsapp and not sent_email:
        frappe.throw(
            _("OTP was generated, but no delivery channel is enabled. Enable WhatsApp OTP or provide a valid customer email."),
            title=_("OTP Delivery Not Configured"),
        )

    return {
        "sent": True,
        "mobile": mobile_no[:3] + "****" + mobile_no[-3:],
        "otp_generated": bool(otp_code),
        "sent_whatsapp": sent_whatsapp,
        "sent_email": sent_email,
    }


@frappe.whitelist(allow_guest=True)
def verify_login_otp(mobile_no: str, otp_code: str) -> dict:
    mobile_no = validate_indian_phone(mobile_no, "Mobile No")
    result = CHOTPLog.verify_otp(
        mobile_no=mobile_no,
        purpose="POS Customer Verification",
        otp_code=str(otp_code or "").strip(),
        reference_doctype="Customer",
        reference_name="",
    )
    if not result.get("valid"):
        return result

    customer = frappe.db.get_value("Customer", {"mobile_no": mobile_no}, "name")
    session_token = _create_portal_session(mobile_no, customer)
    result.update(
        {
            "session_token": session_token,
            "customer": customer,
            "expires_in_seconds": PORTAL_SESSION_TTL,
        }
    )
    return result


@frappe.whitelist(allow_guest=True)
def get_store_locator(city: str | None = None, capability: str | None = None, limit: int = 8) -> list[dict]:
    limit = max(1, min(cint(limit or 8), 20))
    filters = {"disabled": 0}
    city = (city or "").strip()
    if city:
        filters["city"] = city

    capability_field = {
        "buyback": "is_buyback_enabled",
        "service": "is_service_enabled",
        "retail": "is_retail_enabled",
    }.get((capability or "").strip().lower())
    if capability_field:
        filters[capability_field] = 1

    return frappe.get_all(
        "CH Store",
        filters=filters,
        fields=[
            "name",
            "store_name",
            "store_code",
            "city",
            "state",
            "pincode",
            "contact_phone",
            "is_buyback_enabled",
            "is_service_enabled",
            "is_retail_enabled",
        ],
        order_by="store_name asc",
        limit_page_length=limit,
    )


@frappe.whitelist(allow_guest=True)
def get_dashboard(session_token: str) -> dict:
    session = _get_portal_session(session_token)
    mobile_no = session["mobile_no"]
    customer = session.get("customer") or frappe.db.get_value("Customer", {"mobile_no": mobile_no}, "name")
    profile = _get_customer_profile(customer, mobile_no)

    loyalty = _get_loyalty_snapshot(customer)

    purchases = []
    service_requests = []
    vouchers = []
    if customer:
        purchases = frappe.get_all(
            "Sales Invoice",
            filters={"customer": customer, "docstatus": 1},
            fields=["name", "posting_date", "grand_total", "status"],
            order_by="posting_date desc, modified desc",
            limit_page_length=8,
        )
        if frappe.db.exists("DocType", "Service Request"):
            service_requests = frappe.get_all(
                "Service Request",
                filters={"customer": customer},
                fields=["name", "creation", "status", "device_item_name"],
                order_by="creation desc",
                limit_page_length=8,
            )
        if frappe.db.exists("DocType", "CH Voucher"):
            vouchers = frappe.get_all(
                "CH Voucher",
                filters={"issued_to": customer},
                fields=["name", "voucher_code", "voucher_type", "balance", "valid_upto", "status"],
                order_by="modified desc",
                limit_page_length=8,
            )

    stores = get_store_locator(city=profile.get("city"), capability="buyback", limit=6)

    return {
        "customer": customer,
        "mobile_no": mobile_no,
        "profile": profile,
        "loyalty": loyalty,
        "wallet": _get_store_credit_wallet(customer),
        "recent_purchases": purchases,
        "service_requests": service_requests,
        "buyback_assessments": get_assessments_by_phone(mobile_no),
        "buyback_orders": get_orders_by_phone(mobile_no),
        "vouchers": vouchers,
        "stores": stores,
    }
