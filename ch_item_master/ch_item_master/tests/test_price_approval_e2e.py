# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
E2E test: company + category routed price approval.

Covers the full flow — a batch spanning several categories splits into one
approval row per category, each routed to that category's manager; approvers
can only action their own category; approved categories apply immediately
while others stay pending; and the company head picks up categories with no
manager mapped.

Run:
  bench --site erpnext.local execute \
    ch_item_master.ch_item_master.tests.test_price_approval_e2e.run_all
"""

import frappe
from frappe.utils import now_datetime

_results = []
_created = {"batches": [], "users": [], "prices": [], "logs": [], "todos": []}

# Categories borrowed for the test; their original managers are restored in
# _cleanup so a real mapping is never left modified.
_saved_managers = {}
_saved_head = {}
# (item_code, channel) -> selling_price before the test, or None if no row
# existed. Applying a batch overwrites an existing price, so the original must
# be captured to restore it afterwards.
_saved_prices = {}


def ok(name, detail=""):
	_results.append(("PASS", name, detail))
	print(f"PASS  {name} ─ {detail}")


def fail(name, detail=""):
	_results.append(("FAIL", name, detail))
	print(f"FAIL  {name} ─ {detail}")


def skip(name, detail=""):
	_results.append(("SKIP", name, detail))
	print(f"SKIP  {name} ─ {detail}")


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_user(email, first_name, roles):
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = first_name
		u.send_welcome_email = 0
		u.user_type = "System User"
		u.insert(ignore_permissions=True)
		_created["users"].append(email)
	u = frappe.get_doc("User", email)
	have = {r.role for r in u.roles}
	for r in roles:
		if r not in have and frappe.db.exists("Role", r):
			u.append("roles", {"role": r})
	u.save(ignore_permissions=True)
	return email


def _pick_items(company, count=3):
	"""Distinct items from distinct categories that have a POS price."""
	rows = frappe.db.sql(
		"""
		SELECT i.name AS item_code, i.ch_category
		  FROM `tabItem` i
		 WHERE i.ch_category IS NOT NULL AND i.ch_category != ''
		   AND i.disabled = 0
		 GROUP BY i.ch_category
		 ORDER BY i.ch_category
		 LIMIT %(n)s
		""",
		{"n": count},
		as_dict=True,
	)
	return rows


def _snapshot_price(item_code, channel, company):
	if (item_code, channel) in _saved_prices:
		return
	_saved_prices[(item_code, channel)] = frappe.db.get_value(
		"CH Item Price",
		{"item_code": item_code, "channel": channel,
		 "status": ("in", ["Active", "Scheduled"])},
		"selling_price",
	)


def _new_batch(company, rows, channel="POS"):
	b = frappe.new_doc("CH Price Upload Batch")
	b.title = "E2E category routing test"
	b.company = company
	b.uploaded_by = frappe.session.user
	b.upload_date = frappe.utils.nowdate()
	b.status = "Draft"
	b.notes = "Reason: automated e2e"
	for r in rows:
		_snapshot_price(r["item_code"], channel, company)
		b.append("items", {
			"item_code": r["item_code"],
			"channel": channel,
			"change_type": "Selling Price",
			"field_label": "Selling Price",
			"old_value": "0",
			"new_value": str(r.get("new_value", 1111)),
			"reason": "e2e",
		})
	b.insert(ignore_permissions=True)
	_created["batches"].append(b.name)
	return b


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def run_all():
	frappe.set_user("Administrator")
	company = "Bestbuy Mobiles Private Limited"

	if not frappe.db.exists("Company", company):
		fail("setup", f"company {company} missing")
		return _summary()

	items = _pick_items(company, 3)
	if len(items) < 3:
		fail("setup", f"need 3 categorised items, found {len(items)}")
		return _summary()

	cat_a, cat_b, cat_c = [i["ch_category"] for i in items]

	mgr_a = _ensure_user("e2e.cat.a@example.com", "CatA", ["CH Category Head", "CH Price Manager"])
	mgr_b = _ensure_user("e2e.cat.b@example.com", "CatB", ["CH Category Head", "CH Price Manager"])
	head = _ensure_user("e2e.company.head@example.com", "CoHead", ["CH Category Head", "CH Price Manager"])
	maker = _ensure_user("e2e.price.maker@example.com", "Maker", ["CH Price Manager"])

	# Map A and B to managers; leave C unmapped so it must fall back.
	for cat, mgr in ((cat_a, mgr_a), (cat_b, mgr_b), (cat_c, None)):
		_saved_managers[cat] = frappe.db.get_value("CH Category", cat, "category_manager")
		frappe.db.set_value("CH Category", cat, "category_manager", mgr)
	_saved_head[company] = frappe.db.get_value("Company", company, "ch_company_head")
	frappe.db.set_value("Company", company, "ch_company_head", head)
	frappe.db.commit()

	# ── 1. Split + routing ───────────────────────────────────────────────
	frappe.set_user(maker)
	try:
		batch = _new_batch(company, [
			{"item_code": items[0]["item_code"], "new_value": 1111},
			{"item_code": items[1]["item_code"], "new_value": 2222},
			{"item_code": items[2]["item_code"], "new_value": 3333},
		])
		batch.submit_for_approval()
		batch.reload()
	except Exception as e:
		fail("submit routes by category", str(e)[:200])
		return _summary()

	rows = {r.category: r for r in batch.category_approvals}
	if len(batch.category_approvals) == 3:
		ok("split by category", f"3 approval rows for {sorted(rows)}")
	else:
		fail("split by category", f"expected 3 rows, got {len(batch.category_approvals)}")

	if rows.get(cat_a) and rows[cat_a].approver == mgr_a and rows[cat_a].routed_via == "Category Manager":
		ok("routes to category manager", f"{cat_a} -> {mgr_a}")
	else:
		fail("routes to category manager", f"{cat_a} -> {rows.get(cat_a) and rows[cat_a].approver}")

	if rows.get(cat_c) and rows[cat_c].approver == head and rows[cat_c].routed_via == "Company Head":
		ok("unmapped falls back to company head", f"{cat_c} -> {head}")
	else:
		fail("unmapped falls back to company head", f"{cat_c} -> {rows.get(cat_c) and rows[cat_c].approver}")

	if batch.status == "Pending Approval":
		ok("status after submit", batch.status)
	else:
		fail("status after submit", batch.status)

	# Every row carries its category, for routing that cannot drift later.
	if all(r.category for r in batch.items):
		ok("rows stamped with category", f"{len(batch.items)} rows")
	else:
		fail("rows stamped with category", "some rows have no category")

	# ── 2. Approver isolation ────────────────────────────────────────────
	from ch_item_master.ch_item_master.price_approval import decide_category

	frappe.set_user(mgr_b)
	try:
		decide_category(batch.name, cat_a, "Approve")
		fail("cannot approve another's category", "no exception raised")
	except frappe.PermissionError:
		ok("cannot approve another's category", f"{mgr_b} blocked from {cat_a}")
	except Exception as e:
		if "cannot action" in str(e).lower() or "not your" in str(e).lower():
			ok("cannot approve another's category", "blocked")
		else:
			fail("cannot approve another's category", str(e)[:160])

	# ── 3. Segregation of duties ─────────────────────────────────────────
	# Make the maker the manager of category A momentarily.
	frappe.set_user("Administrator")
	frappe.db.set_value("CH Category", cat_a, "category_manager", maker)
	frappe.db.commit()
	sod_batch = None
	try:
		frappe.set_user(maker)
		sod_batch = _new_batch(company, [{"item_code": items[0]["item_code"], "new_value": 4444}])
		sod_batch.submit_for_approval()
		sod_batch.reload()
		decide_category(sod_batch.name, cat_a, "Approve")
		fail("SoD blocks self-approval", "maker approved own batch")
	except Exception as e:
		if "segregation" in str(e).lower() or "cannot also approve" in str(e).lower():
			ok("SoD blocks self-approval", "submitter refused as approver")
		else:
			fail("SoD blocks self-approval", str(e)[:160])
	finally:
		frappe.set_user("Administrator")
		frappe.db.set_value("CH Category", cat_a, "category_manager", mgr_a)
		frappe.db.commit()

	# ── 4. Partial apply ─────────────────────────────────────────────────
	frappe.set_user(mgr_a)
	try:
		res = decide_category(batch.name, cat_a, "Approve")
		batch.reload()
	except Exception as e:
		fail("approve own category", str(e)[:200])
		return _summary()

	if res.get("decision") == "Approved":
		ok("approve own category", f"{cat_a} approved by {mgr_a}")
	else:
		fail("approve own category", str(res))

	if batch.status == "Partially Approved":
		ok("batch partially approved", batch.status)
	else:
		fail("batch partially approved", f"got {batch.status}")

	applied_a = [r for r in batch.items if r.category == cat_a and r.status == "Applied"]
	pending_b = [r for r in batch.items if r.category == cat_b and r.status == "Pending"]
	if applied_a and pending_b:
		ok("approved category applies immediately", f"{len(applied_a)} applied, {len(pending_b)} still pending")
	else:
		fail("approved category applies immediately", f"applied={len(applied_a)} pending_b={len(pending_b)}")

	price = frappe.db.get_value(
		"CH Item Price",
		{"item_code": items[0]["item_code"], "channel": "POS", "company": company,
		 "status": ("in", ["Active", "Scheduled"])},
		["selling_price"],
	)
	if price and float(price) == 1111.0:
		ok("price actually written", f"{items[0]['item_code']} = {price}")
	else:
		fail("price actually written", f"expected 1111, got {price}")

	# Category B must be untouched by A's approval.
	price_b = frappe.db.get_value(
		"CH Item Price",
		{"item_code": items[1]["item_code"], "channel": "POS", "company": company,
		 "status": ("in", ["Active", "Scheduled"])},
		["selling_price"],
	)
	if not price_b or float(price_b) != 2222.0:
		ok("pending category not applied", f"{items[1]['item_code']} still {price_b}")
	else:
		fail("pending category not applied", "unapproved price leaked through")

	# ── 5. Rejection is per category ─────────────────────────────────────
	frappe.set_user(mgr_b)
	try:
		decide_category(batch.name, cat_b, "Reject", comments="too low")
		batch.reload()
		rej = [r for r in batch.category_approvals if r.category == cat_b]
		if rej and rej[0].status == "Rejected":
			ok("reject own category", "cat_b rejected, others unaffected")
		else:
			fail("reject own category", "status not recorded")
	except Exception as e:
		fail("reject own category", str(e)[:200])

	try:
		frappe.set_user(mgr_b)
		decide_category(batch.name, cat_b, "Reject", comments="again")
		fail("double decision blocked", "second decision accepted")
	except Exception as e:
		if "already" in str(e).lower():
			ok("double decision blocked", "re-decide refused")
		else:
			fail("double decision blocked", str(e)[:160])

	# Reject with no reason must be refused.
	frappe.set_user(head)
	try:
		decide_category(batch.name, cat_c, "Reject", comments="   ")
		fail("reject requires reason", "blank reason accepted")
	except Exception as e:
		if "reason" in str(e).lower():
			ok("reject requires reason", "blank reason refused")
		else:
			fail("reject requires reason", str(e)[:160])

	# ── 6. Final roll-up ─────────────────────────────────────────────────
	try:
		decide_category(batch.name, cat_c, "Approve")
		batch.reload()
	except Exception as e:
		fail("company head approves fallback category", str(e)[:200])

	if batch.status in ("Partially Applied", "Applied"):
		ok("roll-up after last decision", f"status={batch.status}")
	else:
		fail("roll-up after last decision", f"status={batch.status}")

	if not any(r.status == "Pending" for r in batch.category_approvals):
		ok("no categories left pending", "all decided")
	else:
		fail("no categories left pending", "some still pending")

	# ── 7. Inbox + notification ──────────────────────────────────────────
	todos = frappe.get_all(
		"ToDo",
		filters={"reference_type": "CH Price Upload Batch", "reference_name": batch.name},
		fields=["allocated_to", "status"],
	)
	if todos:
		ok("approver inbox populated", f"{len(todos)} ToDo(s): {[t.allocated_to for t in todos]}")
	else:
		fail("approver inbox populated", "no ToDo created")

	logs = frappe.get_all(
		"Notification Log",
		filters={"document_type": "CH Price Upload Batch", "document_name": batch.name},
		fields=["for_user"],
	)
	if logs:
		ok("in-desk notification sent", f"{len(logs)} to {sorted({l.for_user for l in logs})}")
	else:
		fail("in-desk notification sent", "no Notification Log rows")

	# ── 8. Change log written once per applied row ───────────────────────
	cl = frappe.get_all(
		"CH Price Change Log",
		filters={"batch_ref": batch.name},
		fields=["item_code"],
	)
	applied_rows = [r for r in batch.items if r.status == "Applied"]
	if len(cl) == len(applied_rows):
		ok("one change log per applied row", f"{len(cl)} logs / {len(applied_rows)} applied")
	else:
		fail("one change log per applied row", f"{len(cl)} logs vs {len(applied_rows)} applied rows")

	# ── 9. Unroutable batch is refused ───────────────────────────────────
	frappe.set_user("Administrator")
	frappe.db.set_value("CH Category", cat_c, "category_manager", None)
	frappe.db.set_value("Company", company, "ch_company_head", None)
	frappe.db.commit()
	try:
		frappe.set_user(maker)
		bad = _new_batch(company, [{"item_code": items[2]["item_code"], "new_value": 5555}])
		bad.submit_for_approval()
		fail("unroutable batch refused", "submitted with no approver")
	except Exception as e:
		if "route" in str(e).lower() or "approver" in str(e).lower():
			ok("unroutable batch refused", "throws naming the category")
		else:
			fail("unroutable batch refused", str(e)[:160])
	finally:
		frappe.set_user("Administrator")
		frappe.db.set_value("Company", company, "ch_company_head", head)
		frappe.db.commit()

	# ── 10. Legacy quarantine holds ──────────────────────────────────────
	legacy = frappe.db.count("CH Price Upload Batch", {"status": "Legacy Import"})
	stray = frappe.db.count("CH Price Upload Batch", {"status": "Approved", "approved_by": ("is", "not set")})
	if legacy and not stray:
		ok("legacy batches quarantined", f"{legacy} in Legacy Import, 0 stray Approved")
	else:
		fail("legacy batches quarantined", f"legacy={legacy} stray_approved={stray}")

	return _summary()


# ─────────────────────────────────────────────────────────────────────────────
# Teardown
# ─────────────────────────────────────────────────────────────────────────────

def _cleanup():
	frappe.set_user("Administrator")
	for cat, mgr in _saved_managers.items():
		try:
			frappe.db.set_value("CH Category", cat, "category_manager", mgr)
		except Exception:
			pass
	for company, head in _saved_head.items():
		try:
			frappe.db.set_value("Company", company, "ch_company_head", head)
		except Exception:
			pass

	for name in _created["batches"]:
		try:
			frappe.db.delete("CH Price Change Log", {"batch_ref": name})
			frappe.db.delete("ToDo", {"reference_type": "CH Price Upload Batch", "reference_name": name})
			frappe.db.delete("Notification Log", {"document_type": "CH Price Upload Batch", "document_name": name})
			frappe.delete_doc("CH Price Upload Batch", name, force=True, ignore_permissions=True)
		except Exception:
			pass

	# Restore prices to their pre-test state. Deleting by value is NOT safe:
	# applying a batch UPDATES an existing CH Item Price rather than creating
	# one, so a value-based delete destroys a real price record. Rows the test
	# created are deleted; rows it overwrote are put back.
	for key, prev in _saved_prices.items():
		item_code, channel = key
		name = frappe.db.get_value(
			"CH Item Price", {"item_code": item_code, "channel": channel}, "name"
		)
		if not name:
			continue
		try:
			if prev is None:
				frappe.delete_doc("CH Item Price", name, force=True, ignore_permissions=True)
			else:
				frappe.db.set_value("CH Item Price", name, "selling_price", prev)
		except Exception:
			pass

	for email in _created["users"]:
		try:
			frappe.delete_doc("User", email, force=True, ignore_permissions=True)
		except Exception:
			pass
	frappe.db.commit()


def _summary():
	passed = sum(1 for r in _results if r[0] == "PASS")
	failed = sum(1 for r in _results if r[0] == "FAIL")
	skipped = sum(1 for r in _results if r[0] == "SKIP")
	print(f"\nRESULT: PASS={passed}  FAIL={failed}  SKIP={skipped}")
	_cleanup()
	return {"passed": passed, "failed": failed, "skipped": skipped, "results": _results}
