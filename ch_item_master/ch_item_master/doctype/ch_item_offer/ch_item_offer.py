# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_datetime, now_datetime, flt

from ch_item_master.config import get_int_setting
from ch_item_master.security import require_scoped_document_action
from ch_item_master.ch_item_master.exceptions import (
	InvalidOfferError,
	OverlappingOfferError,
)


class CHItemOffer(Document):
	_APPROVAL_CONTEXT = object()
	_PROTECTED_FIELDS = (
		"approval_status",
		"approved_by",
		"approved_at",
		"status",
		"erp_pricing_rule",
	)
	_APPROVAL_SENSITIVE_FIELDS = (
		"company",
		"offer_name",
		"offer_level",
		"apply_on",
		"target_item_group",
		"target_brand",
		"apply_discount_on",
		"offer_type",
		"value_type",
		"value",
		"channel",
		"priority",
		"stackable",
		"start_date",
		"end_date",
		"item_code",
		"trigger_item",
		"reward_item",
		"gift_delivery",
		"is_gamified",
		"min_qty",
		"max_qty",
		"customer_group",
		"territory",
		"warehouse",
		"additional_companies",
		"combo_items",
	)

	def _authorize_approval_transition(self):
		self.flags.ch_item_offer_approval_context = self._APPROVAL_CONTEXT

	def _has_approval_context(self):
		return self.flags.get("ch_item_offer_approval_context") is self._APPROVAL_CONTEXT

	def _require_action(self, action):
		require_scoped_document_action(
			self,
			"offer_approval_roles",
			("System Manager", "CH Offer Manager", "CH Master Approver", "CH Master Manager"),
			action=action,
			permission_types=("write",),
			lock=True,
		)

	def _validate_approval_transition(self):
		if self._has_approval_context():
			return
		before = self.get_doc_before_save() if not self.is_new() else None
		if before is None:
			allowed = {
				"approval_status": (None, "", "Pending Approval"),
				"status": (None, "", "Draft"),
			}
			for fieldname in self._PROTECTED_FIELDS:
				value = self.get(fieldname)
				if value not in allowed.get(fieldname, (None, "")):
					frappe.throw(
						_("{0} is set only by the offer approval workflow.").format(
							self.meta.get_label(fieldname) or fieldname
						),
						frappe.PermissionError,
					)
			return

		changed = [
			fieldname
			for fieldname in self._PROTECTED_FIELDS
			if self.get(fieldname) != before.get(fieldname)
		]
		if changed:
			frappe.throw(
				_("Offer approval state can only be changed through Approve or Reject."),
				frappe.PermissionError,
			)

		if before.approval_status == "Approved" and any(
			self.get(fieldname) != before.get(fieldname)
			for fieldname in self._APPROVAL_SENSITIVE_FIELDS
		):
			self.approval_status = "Pending Approval"
			self.approved_by = None
			self.approved_at = None
			self.status = "Draft"

	def validate(self):
		self._validate_approval_transition()
		self._sync_gift_delivery()
		self._validate_dates()
		self._validate_value()
		self._validate_targets()
		self._validate_channel_active()
		self._validate_no_duplicate_active_offer()
		self._validate_combo()
		self._validate_attachment()
		self._auto_set_status()

	def on_update(self):
		# Keep the instant-gift Pricing Rule in step with Gift Delivery even
		# when the mode is toggled after approval — a Spin Wheel freebie must
		# never have a live Product-discount rule (double giveaway).
		if self.approval_status != "Approved":
			if self.get("erp_pricing_rule"):
				self._disable_linked_pricing_rules()
			return
		if self.offer_type != "Freebie":
			return
		if self._is_spin_wheel_freebie():
			self._disable_linked_pricing_rules()
		else:
			self._sync_to_erp_pricing_rule()
			self._sync_additional_companies()

	def _sync_gift_delivery(self):
		"""Freebies must declare how the gift reaches the customer:

		- "Direct (In Cart)": auto-added free at billing via Pricing Rule.
		- "Spin Wheel": issued post-sale as a CH Gift Redemption; NO Pricing
		  Rule, otherwise the customer would receive the gift twice.

		`is_gamified` is kept as the derived storage flag because the gift
		redemption engine and existing reports filter on it.
		"""
		if self.offer_type != "Freebie":
			self.gift_delivery = ""
			self.is_gamified = 0
			return
		if not self.gift_delivery:
			# Pre-migration rows only carry is_gamified.
			self.gift_delivery = "Spin Wheel" if cint(self.is_gamified) else "Direct (In Cart)"
		self.is_gamified = 1 if self.gift_delivery == "Spin Wheel" else 0

	def _is_spin_wheel_freebie(self) -> bool:
		return self.offer_type == "Freebie" and cint(self.is_gamified) == 1

	def _disable_linked_pricing_rules(self):
		"""Disable the offer's Pricing Rule(s), incl. cross-company copies."""
		# " |" anchors the exact offer name — a bare prefix match could catch
		# a different offer whose name merely starts the same.
		names = set(
			frappe.get_all(
				"Pricing Rule",
				filters={
					"rule_description": ["like", f"CH Offer: {self.offer_name} |%"],
					"disable": 0,
				},
				pluck="name",
			)
		)
		if self.get("erp_pricing_rule") and frappe.db.exists("Pricing Rule", self.erp_pricing_rule):
			names.add(self.erp_pricing_rule)
		for name in names:
			frappe.db.set_value("Pricing Rule", name, "disable", 1)

	def _validate_channel_active(self):
		"""Warn if the offer's channel is inactive."""
		if not self.channel:
			return
		disabled = frappe.db.get_value("CH Price Channel", self.channel, "disabled")
		if disabled:
			frappe.msgprint(
				_("Channel {0} is currently inactive. This offer will not apply "
				  "to any transactions until the channel is reactivated."
				).format(frappe.bold(self.channel)),
				indicator="orange",
				title=_("Inactive Channel"),
			)

	def _validate_targets(self):
		"""Ensure correct targeting fields are filled based on offer_level and apply_on."""
		# Attachment/Freebie use trigger_item + reward_item, not item_code
		if self.offer_type in ("Attachment", "Freebie"):
			return
		# Combo uses combo_items table, not item_code
		if self.offer_type == "Combo":
			return

		offer_level = self.get("offer_level") or "Item"
		apply_on = self.get("apply_on") or "Item Code"

		if offer_level == "Item":
			if apply_on == "Item Code" and not self.item_code:
				frappe.throw(
					_("Item Code is required for item-level offers with Apply On = Item Code"),
					title=_("Missing Item Code"),
				)
			elif apply_on == "Item Group" and not self.get("target_item_group"):
				frappe.throw(
					_("Target Item Group is required when Apply On = Item Group"),
					title=_("Missing Target"),
				)
			elif apply_on == "Brand" and not self.get("target_brand"):
				frappe.throw(
					_("Target Brand is required when Apply On = Brand"),
					title=_("Missing Target"),
				)
		# Bill-level offers don't require item_code

	def _validate_dates(self):
		"""Start Date and End Date are both required; End must be after Start."""
		if not self.start_date:
			frappe.throw(_("Start Date is required"), title=_("Missing Dates"))
		if not self.end_date:
			frappe.throw(_("End Date is required"), title=_("Missing Dates"))

		if get_datetime(self.end_date) <= get_datetime(self.start_date):
			frappe.throw(
				_("End Date {0} must be after Start Date {1}").format(
					frappe.bold(str(self.end_date)),
					frappe.bold(str(self.start_date)),
				),
				title=_("Invalid Dates"),
			)

	def _validate_value(self):
		"""Validate offer value based on value_type."""
		# Combo/Freebie may have value=0 (combo_price or free item instead)
		if self.offer_type in ("Combo", "Freebie"):
			return
		if (self.value or 0) <= 0:
			frappe.throw(
				_("Offer value must be greater than zero"),
				title=_("Invalid Value"),
				exc=InvalidOfferError,
			)
		if self.value_type == "Percentage" and self.value > 100:
			frappe.throw(
				_("Percentage discount cannot exceed 100%"),
				title=_("Invalid Value"),
				exc=InvalidOfferError,
			)
		warning_amount = get_int_setting("offer_high_amount_warning", 500000, minimum=1)
		if self.value_type == "Amount" and self.value > warning_amount:
			frappe.msgprint(
				_("Discount amount {0} is unusually high. Please verify.").format(
					frappe.format_value(self.value, dict(fieldtype="Currency"))
				),
				indicator="orange",
				title=_("High Discount Amount"),
			)

	def _validate_no_duplicate_active_offer(self):
		"""Block overlapping offers for the same item + channel + offer_type + date range.

		Two offers overlap if their date ranges intersect:
		  existing.start_date <= self.end_date AND existing.end_date >= self.start_date
		"""
		if self.approval_status == "Rejected" or self.status in ("Cancelled", "Expired"):
			return

		# Trigger-based offers use trigger_item, not item_code — and a Direct
		# freebie + a Spin Wheel freebie on the same item must be allowed to
		# run in parallel (different delivery flows).
		if self.offer_type in ("Combo", "Attachment", "Freebie"):
			return

		offer_level = self.get("offer_level") or "Item"
		if offer_level != "Item" or (self.get("apply_on") or "Item Code") != "Item Code":
			return

		if not self.item_code or not self.channel:
			return

		if not self.start_date or not self.end_date:
			return

		overlaps = frappe.db.sql(
			"""
			SELECT name, offer_name, start_date, end_date
			FROM `tabCH Item Offer`
			WHERE item_code = %(item_code)s
			  AND channel = %(channel)s
			  AND offer_type = %(offer_type)s
			  AND status IN ('Active', 'Scheduled')
			  AND name != %(self_name)s
			  AND start_date <= %(end_date)s
			  AND end_date >= %(start_date)s
			LIMIT 3
			""",
			{
				"item_code": self.item_code,
				"channel": self.channel,
				"offer_type": self.offer_type,
				"self_name": self.name or "",
				"start_date": self.start_date,
				"end_date": self.end_date,
			},
			as_dict=True,
		)
		if overlaps:
			conflicts = "<br>".join(
				_("{0} ({1} to {2})").format(
					frappe.bold(o.offer_name or o.name),
					frappe.format_value(o.start_date, dict(fieldtype="Date")),
					frappe.format_value(o.end_date, dict(fieldtype="Date")),
				)
				for o in overlaps
			)
			frappe.throw(
				_("Overlapping {0} offer(s) already exist for {1} on channel {2}:<br><br>{3}"
				  "<br><br>Expire or reject the existing offer(s) first, or change the dates."
				).format(
					frappe.bold(self.offer_type),
					frappe.bold(self.item_code),
					frappe.bold(self.channel),
					conflicts,
				),
				title=_("Overlapping Offer"),
				exc=OverlappingOfferError,
			)

	def _auto_set_status(self):
		"""Auto-compute status based on approval_status and effective dates."""
		# Rejected/Cancelled stays as-is
		if self.approval_status == "Rejected" or self.status == "Cancelled":
			return

		# Draft/Pending offers stay Draft until approved
		if self.approval_status != "Approved":
			if self.status not in ("Draft",):
				self.status = "Draft"
			return

		now = now_datetime()
		start = get_datetime(self.start_date) if self.start_date else now
		end = get_datetime(self.end_date) if self.end_date else None

		if end and now > end:
			self.status = "Expired"
		elif now < start:
			self.status = "Scheduled"
		else:
			self.status = "Active"

	@frappe.whitelist(methods=["POST"])
	def approve(self) -> None:
		"""Approve this offer — called from form action button."""
		self._require_action(_("approve an item offer"))
		self._authorize_approval_transition()
		self.approval_status = "Approved"
		self.approved_by = frappe.session.user
		self.approved_at = now_datetime()
		self._auto_set_status()
		self.save()
		self._sync_to_erp_pricing_rule()
		self._sync_additional_companies()
		self._create_tpd_scheme()
		if self._is_spin_wheel_freebie():
			frappe.msgprint(
				_("{0} approved — customers buying the trigger item will receive a "
				  "spin-wheel link after billing (no instant-gift Pricing Rule)").format(self.name),
				indicator="green",
			)
		else:
			frappe.msgprint(
				_("{0} approved — Pricing Rule created/updated in ERPNext").format(self.name),
				indicator="green",
			)

	@frappe.whitelist(methods=["POST"])
	def reject(self) -> None:
		"""Reject this offer."""
		self._require_action(_("reject an item offer"))
		self._authorize_approval_transition()
		self.approval_status = "Rejected"
		self.approved_by = None
		self.approved_at = None
		self.status = "Cancelled"
		# Disable the linked Pricing Rule if it exists
		if self.get("erp_pricing_rule") and frappe.db.exists("Pricing Rule", self.erp_pricing_rule):
			frappe.db.set_value("Pricing Rule", self.erp_pricing_rule, "disable", 1)
		self.save()
		frappe.msgprint(_("{0} rejected").format(self.name), indicator="orange")

	def _sync_to_erp_pricing_rule(self):
		"""Create or update an ERPNext Pricing Rule from this approved offer.

		ERPNext Pricing Rules are auto-applied to all selling transactions
		(Sales Invoice, Sales Order, POS, Quotation, Delivery Note) — no hook needed.

		Supports:
		- Item-level offers: apply_on = Item Code / Item Group / Brand
		- Bill-level offers: apply_on = Transaction, apply_discount_on = Grand Total / Net Total
		- Attachment/Freebie: product discount with free_item
		- Combo: handled at POS level (no single Pricing Rule equivalent)
		"""
		# Combo offers are resolved at POS cart level — no ERPNext Pricing Rule
		if self.offer_type == "Combo":
			return

		# Spin Wheel freebies are delivered post-sale via CH Gift Redemption.
		# An instant-gift Pricing Rule would hand the reward out a second time
		# in the original cart, so none is created (and any stale one from a
		# Direct→Spin Wheel toggle is disabled).
		if self._is_spin_wheel_freebie():
			self._disable_linked_pricing_rules()
			return

		erp_pr = self.get("erp_pricing_rule")
		if erp_pr and frappe.db.exists("Pricing Rule", erp_pr):
			pr = frappe.get_doc("Pricing Rule", erp_pr)
			pr.items = []
			pr.item_groups = []
			pr.brands = []
		else:
			pr = frappe.new_doc("Pricing Rule")
			pr.selling = 1

		self._populate_pricing_rule(pr)

		pr.flags.ignore_permissions = True
		pr.flags.ignore_validate_update_after_submit = True
		pr.save()

		frappe.db.set_value(
			"CH Item Offer", self.name, "erp_pricing_rule", pr.name, update_modified=False
		)
		# Keep the in-memory doc in step — approve() re-syncs right after the
		# on_update sync, and a stale None here would create a duplicate rule
		# with identical criteria (MultiplePricingRuleConflict at billing).
		self.erp_pricing_rule = pr.name

	def _sync_additional_companies(self):
		"""Create a Pricing Rule in each additional company for cross-company offers."""
		if not self.get("additional_companies"):
			return

		# No instant-gift rules for Spin Wheel freebies (see _sync_to_erp_pricing_rule).
		if self._is_spin_wheel_freebie():
			return

		for row in self.additional_companies:
			if row.company == self.company:
				continue

			# Check if a Pricing Rule already exists for this offer + company
			existing = frappe.db.get_value(
				"Pricing Rule",
				{"rule_description": ["like", f"CH Offer: {self.offer_name}%"], "company": row.company, "disable": 0},
				"name",
			)
			if existing:
				pr = frappe.get_doc("Pricing Rule", existing)
				pr.items = []
				pr.item_groups = []
				pr.brands = []
			else:
				pr = frappe.new_doc("Pricing Rule")
				pr.selling = 1

			self._populate_pricing_rule(pr, override_company=row.company)
			pr.flags.ignore_permissions = True
			pr.save()

	def _populate_pricing_rule(self, pr, override_company=None):
		"""Populate a Pricing Rule doc from this offer's fields."""
		if self.value_type == "Amount":
			rate_or_discount = "Discount Amount"
		elif self.value_type == "Price Override":
			rate_or_discount = "Rate"
		else:
			rate_or_discount = "Discount Percentage"

		price_list = None
		if self.channel:
			price_list = frappe.db.get_value("CH Price Channel", self.channel, "price_list")

		offer_level = self.get("offer_level") or "Item"
		apply_on = self.get("apply_on") or "Item Code"

		# --- Attachment / Freebie: uses product discount ---
		if self.offer_type in ("Attachment", "Freebie"):
			pr.price_or_product_discount = "Product"
			pr.apply_on = "Item Code"
			pr.append("items", {"item_code": self.trigger_item})
			pr.free_item = self.reward_item
			pr.free_qty = self.reward_qty or 1
			pr.free_item_rate = flt(self.reward_price) if self.offer_type == "Attachment" else 0
			pr.same_item = 0
		elif offer_level == "Bill":
			pr.price_or_product_discount = "Price"
			pr.apply_on = "Transaction"
			pr.apply_discount_on = self.get("apply_discount_on") or "Grand Total"
		elif apply_on == "Item Group":
			pr.price_or_product_discount = "Price"
			pr.apply_on = "Item Group"
			pr.append("item_groups", {"item_group": self.get("target_item_group")})
		elif apply_on == "Brand":
			pr.price_or_product_discount = "Price"
			pr.apply_on = "Brand"
			pr.append("brands", {"brand": self.get("target_brand")})
		else:
			pr.price_or_product_discount = "Price"
			pr.apply_on = "Item Code"
			pr.append("items", {"item_code": self.item_code})

		pr.title             = self.offer_name or self.name
		pr.company           = override_company or self.company or ""
		pr.disable           = 0
		pr.valid_from        = self.start_date
		pr.valid_upto        = self.end_date
		pr.priority          = self._pricing_rule_priority()
		pr.for_price_list    = price_list
		pr.min_amt           = self.min_bill_amount or 0
		pr.rule_description  = (
			f"CH Offer: {self.offer_name} | Type: {self.offer_type} | "
			f"Level: {offer_level} | "
			+ (f"Bank: {self.bank_name} {self.card_type or ''}" if self.bank_name else "")
		)

		# Price discount fields (not for Attachment/Freebie)
		if self.offer_type not in ("Attachment", "Freebie"):
			pr.rate_or_discount = rate_or_discount
			if rate_or_discount == "Discount Percentage":
				pr.discount_percentage = self.value
			elif rate_or_discount == "Discount Amount":
				pr.discount_amount = self.value
			else:
				pr.rate = self.value

	def _pricing_rule_priority(self) -> int:
		"""SAP-style specificity for template vs variant targeting.

		ERPNext natively matches a Pricing Rule holding a *template* item
		against every variant, but resolves overlapping rules purely by
		priority — equal priorities throw MultiplePricingRuleConflict at
		billing. Bumping variant-targeted rules +10 makes "variant offer
		overrides template offer" the default while leaving the user's
		relative priorities intact within each level.
		"""
		base = cint(self.priority) or 1
		target = (
			self.trigger_item
			if self.offer_type in ("Attachment", "Freebie")
			else self.item_code
		)
		if target and frappe.get_cached_value("Item", target, "variant_of"):
			base += 10
		return min(base, 20)

	def _validate_combo(self):
		"""Validate combo offer has required items."""
		if self.offer_type != "Combo":
			return
		if not self.get("combo_items") or len(self.combo_items) < 2:
			frappe.throw(
				_("Combo offers require at least 2 items in the Combo Items table"),
				title=_("Invalid Combo"),
			)

	def _validate_attachment(self):
		"""Validate attachment / freebie offers have trigger + reward items."""
		if self.offer_type not in ("Attachment", "Freebie"):
			return
		if not self.trigger_item:
			frappe.throw(
				_("Trigger Item is required for {0} offers").format(self.offer_type),
				title=_("Missing Trigger Item"),
			)
		if not self.reward_item:
			frappe.throw(
				_("Reward Item is required for {0} offers").format(self.offer_type),
				title=_("Missing Reward Item"),
			)
		if self.trigger_item == self.reward_item:
			frappe.throw(
				_("Trigger Item and Reward Item cannot be the same"),
				title=_("Invalid Attachment Offer"),
			)

	# ── TPD Compensation ────────────────────────────────────────────────────

	def _get_offer_brand(self):
		"""Resolve the brand for this offer (item brand, target_brand, or None)."""
		if self.get("apply_on") == "Brand" and self.get("target_brand"):
			return self.target_brand
		if self.item_code:
			return frappe.db.get_value("Item", self.item_code, "brand")
		return None

	def _create_tpd_scheme(self):
		"""Auto-create a Supplier Scheme Circular for TPD compensation tracking.

		Called on approve() when tpd_compensation_per_unit > 0.
		Creates an active scheme so the engine immediately picks up matching sales.
		"""
		if not flt(self.tpd_compensation_per_unit) > 0:
			return

		# Skip if already linked (re-approving an offer)
		if self.get("linked_scheme_circular") and frappe.db.exists(
			"Supplier Scheme Circular", self.linked_scheme_circular
		):
			return

		brand = self._get_offer_brand()
		if not brand:
			frappe.msgprint(
				_("TPD compensation set but item brand could not be determined. "
				  "Create the Supplier Scheme Circular manually."),
				indicator="orange",
				title=_("TPD Scheme Not Created"),
			)
			return

		from frappe.utils import getdate

		# ── Build rule + detail ─────────────────────────────────────────────
		rule = frappe.new_doc("Supplier Scheme Rule")
		rule.rule_name = f"TPD — {self.offer_name}"
		rule.rule_type = "RRP Band Scheme" if self.value_type == "Price Override" else "Quantity Slab"
		rule.payout_basis = "Per Unit"
		rule.achievement_basis = "Invoice Date"
		rule.notes = (
			f"Auto-created for TPD offer {self.name}. "
			f"Price override: ₹{flt(self.value):,.0f}, "
			f"Compensation: ₹{flt(self.tpd_compensation_per_unit):,.0f}/unit."
		)

		detail = rule.append("details", {})
		# Target: item code, item group, or brand-level (blank = brand gate on circular handles it)
		apply_on = self.get("apply_on") or "Item Code"
		if apply_on == "Item Code" and self.item_code:
			detail.item_code = self.item_code
		elif apply_on == "Item Group" and self.get("target_item_group"):
			detail.item_group = self.target_item_group
		# For Brand-level offers: leave item_code/item_group blank;
		# the brand field on the circular will gate matching.

		# Add RRP band so only sales at the TPD price qualify
		if self.value_type == "Price Override" and flt(self.value) > 0:
			# 5 % tolerance below TPD price for rounding/discount variations
			detail.rrp_from = flt(self.value) * 0.95
			detail.rrp_to = flt(self.value) * 1.02

		detail.qty_from = 1
		detail.qty_to = 0  # unlimited
		detail.payout_per_unit = flt(self.tpd_compensation_per_unit)
		detail.include_in_slab = 1
		detail.eligible_for_payout = 1
		detail.remarks = f"TPD: {self.offer_name}"

		# ── Build circular ──────────────────────────────────────────────────
		circular = frappe.new_doc("Supplier Scheme Circular")
		circular.scheme_name = f"TPD — {self.offer_name}"
		circular.company = self.company
		circular.brand = brand
		circular.supplier = self.tpd_supplier or ""
		circular.valid_from = getdate(self.start_date)
		circular.valid_to = getdate(self.end_date)
		circular.settlement_type = "Credit Note"
		circular.description = (
			f"<p>Auto-created TPD scheme for offer <b>{self.offer_name}</b> ({self.name}).</p>"
			f"<p>Price override: ₹{flt(self.value):,.0f} | "
			f"Compensation: ₹{flt(self.tpd_compensation_per_unit):,.0f}/unit</p>"
		)
		circular.append("rules", rule)

		circular.flags.ignore_permissions = True
		circular.flags.tpd_auto_create = True  # bypass approver-role check
		circular._authorize_approval_transition()
		circular.insert()

		# Frappe v16: grandchild rows (Scheme Rule Detail) are NOT cascade-saved
		# by circular.insert(). Fetch each saved rule row and save() it with details.
		saved_rule = circular.rules[0]  # only one rule per TPD offer
		rule_doc = frappe.get_doc("Supplier Scheme Rule", saved_rule.name)
		for i, d in enumerate(saved_rule.details or []):
			row = rule_doc.append("details", {})
			row.update({k: v for k, v in d.as_dict().items()
						if k not in ("name", "parent", "parenttype", "parentfield", "idx")})
		rule_doc.save(ignore_permissions=True)

		circular.flags.tpd_auto_create = True
		circular._authorize_approval_transition()
		circular.submit()

		self.db_set("linked_scheme_circular", circular.name, update_modified=False)

		frappe.msgprint(
			_("TPD Scheme Circular {0} created and activated. All sales at the TPD price "
			  "will be tracked automatically.").format(
				frappe.bold(circular.name)
			),
			indicator="blue",
			title=_("TPD Scheme Created"),
		)
