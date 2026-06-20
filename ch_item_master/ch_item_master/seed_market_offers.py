"""One-shot seeder for market-reality CH Item Offers.

Modelled on offers actually live at Poorvika / Sangeetha / Reliance Digital
catalogues as of June 2026. Creates Bank, Brand, Attachment (VAS bundle),
and Bill-level offers idempotently — re-running skips records already
present (by `offer_name`). Each insert is wrapped in its own try/commit so
a single failure does not roll back the whole batch.

Invoke:
    bench --site erpnext.local execute ch_item_master.ch_item_master.seed_market_offers.run
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime, add_months


COMPANY = "BestBuy Mobiles Pvt Ltd"
START = str(now_datetime())
END = str(add_months(now_datetime(), 6))


# ─── Bank offers (Reliance Digital "ResQ Bonanza" / Poorvika / Sangeetha) ───
BANK_OFFERS = [
	# (offer_name, bank_name, card_type, value_type, value, min_bill, payment_mode)
	("HDFC Credit Card ₹3000 Off (Mobiles >₹30k)", "HDFC Bank", "Credit", "Amount",   3000, 30000, "Credit Card"),
	("HDFC Credit Card EMI No-Cost 6M",            "HDFC Bank", "Credit", "Amount",   1500, 15000, "EMI / Finance"),
	("ICICI Credit Card 10% Instant (max ₹4000)",  "ICICI Bank","Credit", "Percentage", 10, 15000, "Credit Card"),
	("SBI Debit Card EMI ₹2500 Cashback",          "SBI",       "Debit",  "Amount",   2500, 25000, "EMI / Finance"),
	("Axis Bank Credit Card ₹1500 Instant",        "Axis Bank", "Credit", "Amount",   1500, 15000, "Credit Card"),
	("Federal Bank Credit Card 5% Cashback",       "Federal Bank","Credit","Percentage", 5,  8000, "Credit Card"),
	("Kotak 811 Debit Card ₹500 Off",              "Kotak Mahindra Bank","Debit","Amount", 500, 5000, "Debit Card"),
]

# ─── Brand offers (OEM cashback / brand sponsored) ──────────────────────────
BRAND_OFFERS = [
	# (offer_name, target_brand, value_type, value, min_bill_amount)
	("Samsung Galaxy ₹2000 OEM Cashback",   "Samsung", "Amount",     2000, 40000),
	("Vivo Exchange Bonus ₹2000",           "Vivo",    "Amount",     2000, 25000),
	("Apple Education Discount ₹3000",      "Apple",   "Amount",     3000, 50000),
	("Realme Festival 5% Flat",             "Realme",  "Percentage",    5, 10000),
	("Xiaomi Redmi Launch 4% Off",          "Redmi",   "Percentage",    4, 10000),
	("Oneplus Festive ₹1500 Cashback",      "Oneplus", "Amount",     1500, 25000),
	("Oppo Diwali Saving ₹1000",            "Oppo",    "Amount",     1000, 15000),
	("Motorola ₹500 Bank Independent",      "Motorola","Amount",      500, 10000),
]

# ─── VAS bundle / attachment offers (Poorvika "Total Protect" style) ────────
ATTACHMENT_OFFERS = [
	# (offer_name, trigger_item, target_brand, reward_item, reward_price, reward_qty, min_bill_amount)
	("ADLD Protect Plus @ ₹1 with Vivo V60E",          "MB000004-12GB-256GB-150W-WC-AFP", "Vivo",  "VAS-PROTECT-PLUS",        1.0, 1, 20000),
	("Anti-Theft VAS @ ₹99 with Apple iPhone 11 Pro",  "Apple iPhone 11 Pro",             "Apple", "VAS-PROTECT-PLUS",       99.0, 1, 40000),
	("GoFix Service Care @ ₹499 with Vivo (I00808)",   "I00808",                          "Vivo",  "GF-VAS-SVC-20260620-01",499.0, 1, 15000),
]

# ─── Bill-level offers (cart-wide flat / percentage) ────────────────────────
BILL_OFFERS = [
	# (offer_name, apply_discount_on, value_type, value, min_bill_amount)
	("Festive Bill ₹500 Off (>₹10k)", "Grand Total", "Amount",     500, 10000),
	("Weekend Bill 3% Off (>₹5k)",    "Net Total",   "Percentage",   3,  5000),
]


def _exists(offer_name: str) -> bool:
	return bool(frappe.db.exists("CH Item Offer", {"offer_name": offer_name}))


def _insert_resilient(doc_dict: dict, label: str, created: list, skipped: list) -> None:
	try:
		doc = frappe.get_doc(doc_dict)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		doc.db_set("status", "Active", update_modified=False)
		doc.db_set("approval_status", "Approved", update_modified=False)
		doc.db_set("approved_by", "Administrator", update_modified=False)
		doc.db_set("approved_at", now_datetime(), update_modified=False)
		frappe.db.commit()
		created.append(f"{doc.name} — {label}")
	except Exception as exc:
		frappe.db.rollback()
		skipped.append(f"{label} [{type(exc).__name__}: {str(exc)[:160]}]")


def run():
	created: list[str] = []
	skipped: list[str] = []

	for name, bank, card, vt, val, minbill, pmode in BANK_OFFERS:
		if _exists(name):
			skipped.append(f"{name} [exists]")
			continue
		_insert_resilient(
			{
				"doctype": "CH Item Offer",
				"company": COMPANY,
				"offer_name": name,
				"offer_level": "Bill",
				"apply_on": "Item Code",
				"offer_type": "Bank Offer",
				"value_type": vt,
				"value": val,
				"apply_discount_on": "Grand Total",
				"bank_name": bank,
				"card_type": card,
				"payment_mode": pmode,
				"min_bill_amount": minbill,
				"start_date": START,
				"end_date": END,
				"priority": 5,
				"stackable": 0,
				"notes": "Seeded — market-reality (Poorvika / Sangeetha / Reliance Digital).",
			},
			name, created, skipped,
		)

	for name, brand, vt, val, minbill in BRAND_OFFERS:
		if _exists(name):
			skipped.append(f"{name} [exists]")
			continue
		if not frappe.db.exists("Brand", brand):
			skipped.append(f"{name} [brand {brand} missing]")
			continue
		_insert_resilient(
			{
				"doctype": "CH Item Offer",
				"company": COMPANY,
				"offer_name": name,
				"offer_level": "Item",
				"apply_on": "Brand",
				"target_brand": brand,
				"offer_type": "Brand Offer",
				"value_type": vt,
				"value": val,
				"min_bill_amount": minbill,
				"start_date": START,
				"end_date": END,
				"priority": 6,
				"stackable": 1,
				"notes": "OEM brand cashback — modelled on Reliance Digital / Sangeetha.",
			},
			name, created, skipped,
		)

	for name, trigger, brand, reward, reward_price, reward_qty, minbill in ATTACHMENT_OFFERS:
		if _exists(name):
			skipped.append(f"{name} [exists]")
			continue
		if not frappe.db.exists("Item", trigger):
			skipped.append(f"{name} [trigger {trigger} missing]")
			continue
		if not frappe.db.exists("Item", reward):
			skipped.append(f"{name} [reward {reward} missing]")
			continue
		offer_dict = {
			"doctype": "CH Item Offer",
			"company": COMPANY,
			"offer_name": name,
			"offer_level": "Item",
			"apply_on": "Brand",
			"target_brand": brand,
			"offer_type": "Attachment",
			"value_type": "Amount",
			"value": reward_price,
			"min_bill_amount": minbill,
			"trigger_item": trigger,
			"reward_item": reward,
			"reward_price": reward_price,
			"reward_qty": reward_qty,
			"start_date": START,
			"end_date": END,
			"priority": 7,
			"stackable": 1,
			"notes": "VAS attach — modelled on Poorvika Total Protect / Reliance ResQ.",
		}
		_insert_resilient(offer_dict, name, created, skipped)

	for name, apply_on, vt, val, minbill in BILL_OFFERS:
		if _exists(name):
			skipped.append(f"{name} [exists]")
			continue
		_insert_resilient(
			{
				"doctype": "CH Item Offer",
				"company": COMPANY,
				"offer_name": name,
				"offer_level": "Bill",
				"apply_on": "Item Code",
				"offer_type": "Flat Discount",
				"value_type": vt,
				"value": val,
				"apply_discount_on": apply_on,
				"min_bill_amount": minbill,
				"start_date": START,
				"end_date": END,
				"priority": 3,
				"stackable": 0,
				"notes": "Bill-level festive offer.",
			},
			name, created, skipped,
		)

	print(f"SEED_OK created={len(created)} skipped={len(skipped)}")
	for n in created:
		print("  +", n)
	for n in skipped:
		print("  -", n)
	return {"created": created, "skipped": skipped}
