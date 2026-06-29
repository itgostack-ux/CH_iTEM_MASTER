"""Seed the CH Message Template registry with the legacy GoFix template library.

These are global fallbacks (no company): each company can override any code by
creating its own enabled CH Message Template with the same code+channel.
Idempotent — re-running only inserts missing rows and leaves edits in place.
"""
import frappe


# (code, channel, subject, description, body)
TEMPLATES = [
    (
        "OTP",
        "SMS",
        "",
        "OTP for login / customer verification.",
        "Your OTP to log in is #OTP#. This will expire within #MIN# minutes. "
        "Do not share it with anyone. - #CompanyName#",
    ),
    (
        "OTPW",
        "SMS",
        "",
        "Wallet redemption OTP.",
        "Use OTP #OTP# to redeem your wallet balance. "
        "This will expire in #MIN# minutes. - #CompanyName#",
    ),
    (
        "EOTP",
        "Email",
        "OTP Confirmation Code for #CompanyName# Account",
        "OTP email template.",
        """<!DOCTYPE html>
<html><body style=\"font-family: Arial, sans-serif; font-size: 14px;\">
  <h2>OTP for Your #CompanyName# Account</h2>
  <p>Dear #CustomerName#,</p>
  <p>Thank you for choosing #CompanyName#. To complete your verification, please enter the following OTP:</p>
  <h3 style=\"font-weight: bold; letter-spacing: 4px;\">#OTP#</h3>
  <p>This code will expire in #MIN# minutes. Do not share it with anyone.</p>
  <p>If you did not request this OTP, please ignore this email.</p>
  <p>Best regards,<br>#CompanyName#</p>
</body></html>""",
    ),
    (
        "CANM",
        "SMS",
        "",
        "Appointment / order cancellation notice.",
        "Order ##ServiceNo# is canceled. Need assistance? Contact us. - #CompanyName#",
    ),
    (
        "CBL",
        "SMS",
        "",
        "Apology message to call back later.",
        "Dear #CustomerName#, we received your enquiry but were unable to connect today. "
        "Our team will call you back shortly. - #CompanyName#",
    ),
    (
        "PICM",
        "SMS",
        "",
        "Pickup confirmation message.",
        "Hi #CustomerName#, your pickup is confirmed. AWB #AWBNumber#. "
        "Pack your device securely. The courier will contact you soon. - #CompanyName#",
    ),
    (
        "PICS",
        "SMS",
        "",
        "Pickup confirmation - ShipRocket.",
        "Hi #CustomerName#, pickup confirmed. Our team will share OTP via Shiprocket. "
        "Await our call. - #CompanyName#",
    ),
    (
        "ACF",
        "Email",
        "Your appointment with #CompanyName# is confirmed - #ServiceRequest#",
        "Appointment confirmation email.",
        """<!DOCTYPE html><html><body style=\"font-family: Arial, sans-serif; font-size: 14px;\">
  <h2>Thank You for Booking an Appointment</h2>
  <p>Dear #CustomerName#,</p>
  <p>Your appointment with us with Service Request <b>#ServiceRequest#</b> is confirmed.</p>
  <p>If you need to reschedule or cancel, please let us know at least 24 hours in advance.</p>
  <p>Best regards,<br>#CompanyName#</p>
</body></html>""",
    ),
]


def execute():
    for code, channel, subject, description, body in TEMPLATES:
        existing = frappe.db.get_value(
            "CH Message Template",
            {"code": code, "channel": channel, "company": ["in", [None, ""]]},
            "name",
        )
        if existing:
            continue
        doc = frappe.new_doc("CH Message Template")
        doc.code = code
        doc.channel = channel
        doc.company = None
        doc.subject = subject
        doc.body = body
        doc.description = description
        doc.language = "en"
        doc.enabled = 1
        doc.insert(ignore_permissions=True)
        # Frappe auto-fills Link defaults from the running user's defaults; force
        # the seeded rows back to a true global fallback (company IS NULL).
        if doc.company:
            frappe.db.set_value("CH Message Template", doc.name, "company", None)
    frappe.db.commit()
