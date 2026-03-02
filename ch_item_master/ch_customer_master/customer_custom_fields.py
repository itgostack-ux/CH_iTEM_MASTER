# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

# Custom Fields added to ERPNext Customer doctype by CH Customer Master module.
# Applied via frappe.custom.doctype.custom_field.custom_field.create_custom_fields()

from frappe import _

CUSTOMER_CUSTOM_FIELDS = {
	"Customer": [
		# ══════════════════════════════════════════════
		# Customer ID (top of form, after naming_series)
		# ══════════════════════════════════════════════
		{
			"fieldname": "ch_customer_id",
			"label": _("Customer ID"),
			"fieldtype": "Int",
			"insert_after": "naming_series",
			"unique": 1,
			"read_only": 1,
			"in_list_view": 1,
			"in_standard_filter": 1,
			"bold": 1,
			"no_copy": 1,
			"description": _("Auto-generated numeric ID for API / mobile / POS integration"),
		},
		# ══════════════════════════════════════════════
		# Tab: CH Profile
		# ══════════════════════════════════════════════
		{
			"fieldname": "ch_profile_tab",
			"label": _("CH Profile"),
			"fieldtype": "Tab Break",
			"insert_after": "portal_users_tab",
		},
		{
			"fieldname": "ch_personal_section",
			"label": _("Personal Information"),
			"fieldtype": "Section Break",
			"insert_after": "ch_profile_tab",
		},
		{
			"fieldname": "ch_date_of_birth",
			"label": _("Date of Birth"),
			"fieldtype": "Date",
			"insert_after": "ch_personal_section",
		},
		{
			"fieldname": "ch_anniversary_date",
			"label": _("Anniversary Date"),
			"fieldtype": "Date",
			"insert_after": "ch_date_of_birth",
		},
		{
			"fieldname": "ch_customer_since",
			"label": _("Customer Since"),
			"fieldtype": "Date",
			"insert_after": "ch_anniversary_date",
			"read_only": 1,
			"description": _("Auto-set to the date of first transaction"),
		},
		{
			"fieldname": "ch_profile_col_1",
			"fieldtype": "Column Break",
			"insert_after": "ch_customer_since",
		},
		{
			"fieldname": "ch_alternate_phone",
			"label": _("Alternate Phone"),
			"fieldtype": "Data",
			"options": "Phone",
			"insert_after": "ch_profile_col_1",
		},
		{
			"fieldname": "ch_whatsapp_number",
			"label": _("WhatsApp Number"),
			"fieldtype": "Data",
			"options": "Phone",
			"insert_after": "ch_alternate_phone",
			"description": _("For Gallabox WhatsApp integration"),
		},
		{
			"fieldname": "ch_customer_image",
			"label": _("Customer Photo"),
			"fieldtype": "Attach Image",
			"insert_after": "ch_whatsapp_number",
		},
		# ── Communication Preferences ──
		{
			"fieldname": "ch_comm_section",
			"label": _("Communication Preferences"),
			"fieldtype": "Section Break",
			"insert_after": "ch_customer_image",
		},
		{
			"fieldname": "ch_preferred_language",
			"label": _("Preferred Language"),
			"fieldtype": "Link",
			"options": "Language",
			"insert_after": "ch_comm_section",
		},
		{
			"fieldname": "ch_communication_preference",
			"label": _("Communication Preference"),
			"fieldtype": "Select",
			"options": "\nSMS\nWhatsApp\nEmail\nAll",
			"insert_after": "ch_preferred_language",
			"default": "All",
		},
		{
			"fieldname": "ch_comm_col_1",
			"fieldtype": "Column Break",
			"insert_after": "ch_communication_preference",
		},
		{
			"fieldname": "ch_is_subscribed",
			"label": _("Subscribed to Marketing"),
			"fieldtype": "Check",
			"insert_after": "ch_comm_col_1",
			"default": "1",
			"description": _("Opt-in for promotional messages"),
		},
		# ── Referral ──
		{
			"fieldname": "ch_referral_section",
			"label": _("Referral"),
			"fieldtype": "Section Break",
			"insert_after": "ch_is_subscribed",
			"collapsible": 1,
		},
		{
			"fieldname": "ch_referral_code",
			"label": _("Referral Code"),
			"fieldtype": "Data",
			"insert_after": "ch_referral_section",
			"unique": 1,
			"read_only": 1,
			"description": _("Auto-generated unique code for this customer"),
		},
		{
			"fieldname": "ch_referred_by",
			"label": _("Referred By"),
			"fieldtype": "Link",
			"options": "Customer",
			"insert_after": "ch_referral_code",
		},
		{
			"fieldname": "ch_referral_col_1",
			"fieldtype": "Column Break",
			"insert_after": "ch_referred_by",
		},
		{
			"fieldname": "ch_referral_source",
			"label": _("Acquisition Source"),
			"fieldtype": "Select",
			"options": "\nWalk-in\nOnline\nReferral\nSocial Media\nCampaign\nGoogle\nJust Dial\nOther",
			"insert_after": "ch_referral_col_1",
		},
		{
			"fieldname": "ch_referrals_made",
			"label": _("Referrals Made"),
			"fieldtype": "Int",
			"insert_after": "ch_referral_source",
			"read_only": 1,
			"default": "0",
		},
		# ══════════════════════════════════════════════
		# Tab: KYC & Documents
		# ══════════════════════════════════════════════
		{
			"fieldname": "ch_kyc_tab",
			"label": _("KYC & Documents"),
			"fieldtype": "Tab Break",
			"insert_after": "ch_referrals_made",
		},
		{
			"fieldname": "ch_kyc_section",
			"label": _("Identity Documents"),
			"fieldtype": "Section Break",
			"insert_after": "ch_kyc_tab",
		},
		{
			"fieldname": "ch_aadhaar_number",
			"label": _("Aadhaar Number"),
			"fieldtype": "Data",
			"insert_after": "ch_kyc_section",
			"description": _("12-digit Aadhaar number (masked in display)"),
		},
		{
			"fieldname": "ch_aadhaar_document",
			"label": _("Aadhaar Document"),
			"fieldtype": "Attach",
			"insert_after": "ch_aadhaar_number",
		},
		{
			"fieldname": "ch_kyc_col_1",
			"fieldtype": "Column Break",
			"insert_after": "ch_aadhaar_document",
		},
		{
			"fieldname": "ch_kyc_verified",
			"label": _("KYC Verified"),
			"fieldtype": "Check",
			"insert_after": "ch_kyc_col_1",
			"default": "0",
		},
		{
			"fieldname": "ch_kyc_verified_by",
			"label": _("Verified By"),
			"fieldtype": "Link",
			"options": "User",
			"insert_after": "ch_kyc_verified",
			"read_only": 1,
			"depends_on": "ch_kyc_verified",
		},
		{
			"fieldname": "ch_kyc_verified_on",
			"label": _("Verified On"),
			"fieldtype": "Date",
			"insert_after": "ch_kyc_verified_by",
			"read_only": 1,
			"depends_on": "ch_kyc_verified",
		},
		# ══════════════════════════════════════════════
		# Tab: Payment Accounts
		# ══════════════════════════════════════════════
		{
			"fieldname": "ch_payment_tab",
			"label": _("Payment Accounts"),
			"fieldtype": "Tab Break",
			"insert_after": "ch_kyc_verified_on",
		},
		{
			"fieldname": "ch_payment_section",
			"label": _("Saved Payment Methods"),
			"fieldtype": "Section Break",
			"insert_after": "ch_payment_tab",
			"description": _("Bank accounts and UPI IDs for buyback payouts, refunds, etc."),
		},
		{
			"fieldname": "ch_payment_accounts",
			"label": _("Payment Accounts"),
			"fieldtype": "Table",
			"options": "CH Customer Payment Account",
			"insert_after": "ch_payment_section",
		},
		# ══════════════════════════════════════════════
		# Tab: Activity & History
		# ══════════════════════════════════════════════
		{
			"fieldname": "ch_activity_tab",
			"label": _("Activity & History"),
			"fieldtype": "Tab Break",
			"insert_after": "ch_payment_accounts",
		},
		{
			"fieldname": "ch_summary_section",
			"label": _("Summary"),
			"fieldtype": "Section Break",
			"insert_after": "ch_activity_tab",
		},
		{
			"fieldname": "ch_total_purchases",
			"label": _("Total Purchases (₹)"),
			"fieldtype": "Currency",
			"insert_after": "ch_summary_section",
			"read_only": 1,
			"default": "0",
			"description": _("Sum of all Sales Invoices across companies"),
		},
		{
			"fieldname": "ch_total_services",
			"label": _("Total Service Requests"),
			"fieldtype": "Int",
			"insert_after": "ch_total_purchases",
			"read_only": 1,
			"default": "0",
		},
		{
			"fieldname": "ch_total_buybacks",
			"label": _("Total Buybacks"),
			"fieldtype": "Int",
			"insert_after": "ch_total_services",
			"read_only": 1,
			"default": "0",
		},
		{
			"fieldname": "ch_summary_col_1",
			"fieldtype": "Column Break",
			"insert_after": "ch_total_buybacks",
		},
		{
			"fieldname": "ch_active_devices",
			"label": _("Active Devices"),
			"fieldtype": "Int",
			"insert_after": "ch_summary_col_1",
			"read_only": 1,
			"default": "0",
			"description": _("Devices currently owned (Sold status in lifecycle)"),
		},
		{
			"fieldname": "ch_loyalty_points_balance",
			"label": _("Loyalty Points Balance"),
			"fieldtype": "Int",
			"insert_after": "ch_active_devices",
			"read_only": 1,
			"default": "0",
		},
		{
			"fieldname": "ch_last_visit_date",
			"label": _("Last Visit"),
			"fieldtype": "Date",
			"insert_after": "ch_loyalty_points_balance",
			"read_only": 1,
		},
		{
			"fieldname": "ch_last_visit_store",
			"label": _("Last Visit Store"),
			"fieldtype": "Data",
			"insert_after": "ch_last_visit_date",
			"read_only": 1,
		},
		# ── Customer Segment ──
		{
			"fieldname": "ch_segment_section",
			"label": _("Customer Segment"),
			"fieldtype": "Section Break",
			"insert_after": "ch_last_visit_store",
		},
		{
			"fieldname": "ch_customer_segment",
			"label": _("Segment"),
			"fieldtype": "Select",
			"options": "\nNew\nRegular\nVIP\nDormant\nChurned",
			"insert_after": "ch_segment_section",
			"read_only": 1,
			"description": _("Auto-classified based on transaction history"),
		},
		{
			"fieldname": "ch_segment_col_1",
			"fieldtype": "Column Break",
			"insert_after": "ch_customer_segment",
		},
		{
			"fieldname": "ch_customer_rating",
			"label": _("Customer Rating"),
			"fieldtype": "Rating",
			"insert_after": "ch_segment_col_1",
			"description": _("Staff-assigned rating"),
		},
		# ── Store Visits ──
		{
			"fieldname": "ch_visits_section",
			"label": _("Store Visits"),
			"fieldtype": "Section Break",
			"insert_after": "ch_customer_rating",
			"collapsible": 1,
		},
		{
			"fieldname": "ch_stores_visited",
			"label": _("Store Visits"),
			"fieldtype": "Table",
			"options": "CH Customer Store Visit",
			"insert_after": "ch_visits_section",
		},
	],
}
