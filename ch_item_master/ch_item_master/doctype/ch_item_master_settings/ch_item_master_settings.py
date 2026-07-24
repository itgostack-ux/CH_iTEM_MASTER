import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class CHItemMasterSettings(Document):
	def validate(self):
		percent_fields = (
			"item_price_spread_percent",
			"vas_low_voucher_utilization_percent",
			"item_quality_grade_a_min",
			"item_quality_grade_b_min",
			"item_quality_grade_c_min",
			"customer_dormancy_insight_percent",
			"customer_churn_insight_percent",
			"customer_revenue_concentration_percent",
			"customer_referral_target_percent",
			"customer_conversion_target_percent",
			"coupon_low_redemption_percent",
			"coupon_medium_redemption_percent",
			"coupon_high_redemption_percent",
		)
		for fieldname in percent_fields:
			value = flt(self.get(fieldname))
			if value < 0 or value > 100:
				frappe.throw(_("{0} must be between 0 and 100.").format(self.meta.get_label(fieldname)))

		if not (
			flt(self.item_quality_grade_a_min)
			>= flt(self.item_quality_grade_b_min)
			>= flt(self.item_quality_grade_c_min)
		):
			frappe.throw(_("Item quality grade thresholds must be ordered A ≥ B ≥ C."))
		if flt(self.item_price_expiry_critical_days) > flt(self.item_price_expiry_warning_days):
			frappe.throw(_("Critical price expiry days cannot exceed warning days."))
		if flt(self.customer_regular_transaction_count) > flt(self.customer_vip_transaction_count):
			frappe.throw(_("Regular customer transactions cannot exceed the VIP threshold."))
		if flt(self.customer_dormant_months) > flt(self.customer_churn_months):
			frappe.throw(_("Customer dormancy months cannot exceed churn months."))
		if flt(self.coupon_medium_redemption_percent) > flt(self.coupon_high_redemption_percent):
			frappe.throw(_("Medium campaign redemption cannot exceed the high threshold."))
