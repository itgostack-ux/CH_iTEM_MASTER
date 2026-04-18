"""
Seed Status Registry — Single source of truth for all status/workflow values
across GoGizmo custom apps.

Validates during `bench migrate` that:
1. All code-referenced status values exist in their doctype field options
2. Cross-app status mappings (e.g. SR_TO_CLAIM_STATUS) are valid
3. Workflow state update_values match their target field options

Run manually:
  bench --site erpnext.local execute ch_item_master.seed_status_registry.validate_status_registry

Added to after_migrate hook so every deploy catches mismatches early.
"""

import frappe
from frappe.utils import cstr

# ═══════════════════════════════════════════════════════════════════════
# MASTER REGISTRY — All status field options that code depends on
# Format: { "Doctype": { "fieldname": [list of valid options] } }
# ═══════════════════════════════════════════════════════════════════════
STATUS_REGISTRY = {
	"CH Warranty Claim": {
		"claim_status": [
			"Draft",
			"Pending Coverage Check",
			"Coverage Identified",
			"Pending Approval",
			"Need More Information",
			"Approved",
			"Partially Approved",
			"Rejected",
			"Pickup Requested",
			"Pickup Scheduled",
			"Picked Up",
			"Device Received",
			"QC Pending",
			"QC Passed",
			"QC Failed",
			"Fee Pending",
			"Fee Paid",
			"Fee Waived",
			"Ticket Created",
			"In Repair",
			"Additional Approval Pending",
			"Awaiting Spare",
			"Repair Complete",
			"Final QC Pending",
			"Final QC Passed",
			"Invoice Pending",
			"Invoice Raised",
			"Payment Pending",
			"Payment Received",
			"Out for Delivery",
			"Delivered",
			"Closed",
			"Cancelled",
			"Not Repairable",
		],
	},
	"Service Request": {
		"decision": [
			"Draft",
			"Accepted",
			"In Service",
			"Completed",
			"Invoiced",
			"Delivered",
			"Withdrawn",
			"Rejected",
			"Expired",
			"Cancelled",
		],
	},
	"Job Assignment": {
		"assignment_status": [
			"Open",
			"In Progress",
			"On Hold",
			"Completed",
			"Cancelled",
		],
	},
	"CH POS Session": {
		"status": [
			"Open",
			"Suspended",
			"Locked",
			"Closing",
			"Pending Close",
			"Closed",
		],
	},
	"CH POS Settlement": {
		"settlement_status": [
			"Draft",
			"Submitted",
			"Approved",
			"Closed",
		],
	},
	"CH Free Sale Approval": {
		"status": [
			"Pending",
			"Approved",
			"Rejected",
			"Expired",
		],
	},
	"CH Cash Drop": {
		"status": [
			"Draft",
			"Submitted",
			"Approved",
			"Posted",
			"Cancelled",
		],
	},
	"POS Kiosk Token": {
		"status": [
			"Waiting",
			"Engaged",
			"In Progress",
			"Completed",
			"Converted",
			"Dropped",
			"Cancelled",
		],
	},
	"CH Exception Request": {
		"status": [
			"Pending",
			"Approved",
			"Rejected",
			"Auto-Approved",
			"Expired",
		],
	},
	"CH Store Material Request": {
		"status": [
			"Draft",
			"Pending Approval",
			"Approved",
			"Rejected",
			"Under Review",
			"Allocation Planned",
			"Partially Allocated",
			"Procurement Initiated",
			"In Transit",
			"Partially Received",
			"Fulfilled",
			"Closed With Reason",
			"Cancelled",
		],
	},
	"CH Transfer Manifest": {
		"status": [
			"Draft",
			"Packed",
			"Assigned",
			"Pickup Started",
			"In Transit",
			"Delivered",
			"Received",
			"Closed",
			"Cancelled",
		],
	},
	"Buyback Order": {
		"status": [
			"Draft",
			"Awaiting Approval",
			"Approved",
			"Awaiting Customer Approval",
			"Customer Approved",
			"Awaiting OTP",
			"OTP Verified",
			"Ready to Pay",
			"Paid",
			"Closed",
			"Rejected",
			"Cancelled",
		],
	},
	"Buyback Assessment": {
		"status": [
			"Draft",
			"Submitted",
			"Inspection Created",
			"Expired",
			"Cancelled",
		],
	},
	"Buyback Inspection": {
		"status": [
			"Draft",
			"In Progress",
			"Completed",
			"Rejected",
		],
	},
	"Buyback Exchange Order": {
		"status": [
			"Draft",
			"New Device Delivered",
			"Awaiting Pickup",
			"Old Device Received",
			"Inspected",
			"Settled",
			"Closed",
			"Cancelled",
		],
	},
	"POS Repair Intake": {
		"status": [
			"Draft",
			"Converted",
			"Cancelled",
		],
	},
}

# ═══════════════════════════════════════════════════════════════════════
# CROSS-APP STATUS MAPPINGS — Values that one app writes to another
# These are the most dangerous for mismatches
# Format: { "label": { source_value: target_value, ... },
#            "_target": ("Target Doctype", "target_field") }
# ═══════════════════════════════════════════════════════════════════════
CROSS_APP_MAPPINGS = {
	"SR_TO_CLAIM_STATUS": {
		"_source": ("Service Request", "decision"),
		"_target": ("CH Warranty Claim", "claim_status"),
		"Completed": "Repair Complete",
		"Invoiced": "Repair Complete",
		"Delivered": "Delivered",
		"Cancelled": "Cancelled",
		"Rejected": "Rejected",
	},
	"SR_TO_LIFECYCLE_STATUS": {
		"_source": ("Service Request", "decision"),
		"_target": None,  # CH Serial Lifecycle uses free-text, no Select validation
		"Completed": "In Stock",
		"Delivered": "Sold",
		"Cancelled": "In Stock",
	},
}


def validate_status_registry():
	"""
	Validate all status field options match the registry.
	Called during after_migrate and can be run manually.
	"""
	errors = []
	warnings = []
	fixed = []

	print("\n" + "=" * 70)
	print("  STATUS REGISTRY VALIDATION")
	print("=" * 70)

	# 1. Validate field options match registry
	for doctype, fields in STATUS_REGISTRY.items():
		if not frappe.db.exists("DocType", doctype):
			warnings.append(f"  DocType '{doctype}' not installed — skipped")
			continue

		meta = frappe.get_meta(doctype)
		for fieldname, expected_options in fields.items():
			field = meta.get_field(fieldname)
			if not field:
				# Check Custom Field
				field = frappe.db.get_value(
					"Custom Field",
					{"dt": doctype, "fieldname": fieldname},
					["options"],
					as_dict=True,
				)
				if field:
					actual_options = [o.strip() for o in cstr(field.options).split("\n") if o.strip()]
				else:
					errors.append(
						f"  {doctype}.{fieldname}: field NOT FOUND in doctype or Custom Fields"
					)
					continue
			else:
				actual_options = [o.strip() for o in cstr(field.options).split("\n") if o.strip()]

			# Check each expected option exists
			missing = [opt for opt in expected_options if opt not in actual_options]
			extra = [opt for opt in actual_options if opt not in expected_options]

			if missing:
				errors.append(
					f"  {doctype}.{fieldname}: MISSING options → {missing}"
				)
			if extra:
				warnings.append(
					f"  {doctype}.{fieldname}: extra options (OK) → {extra}"
				)

			if not missing:
				print(f"  ✅ {doctype}.{fieldname} — {len(expected_options)} options verified")

	# 2. Validate cross-app mappings
	print(f"\n{'─' * 70}")
	print("  CROSS-APP MAPPING VALIDATION")
	print(f"{'─' * 70}")

	for label, mapping in CROSS_APP_MAPPINGS.items():
		source_info = mapping.get("_source")
		target_info = mapping.get("_target")

		data_keys = {k: v for k, v in mapping.items() if not k.startswith("_")}

		# Validate source values exist in source doctype
		if source_info:
			src_dt, src_field = source_info
			src_options = STATUS_REGISTRY.get(src_dt, {}).get(src_field, [])
			for src_val in data_keys:
				if src_val not in src_options:
					errors.append(
						f"  {label}: source value '{src_val}' not in {src_dt}.{src_field} options"
					)

		# Validate target values exist in target doctype
		if target_info:
			tgt_dt, tgt_field = target_info
			tgt_options = STATUS_REGISTRY.get(tgt_dt, {}).get(tgt_field, [])
			for src_val, tgt_val in data_keys.items():
				if tgt_val not in tgt_options:
					errors.append(
						f"  {label}: target value '{tgt_val}' not in {tgt_dt}.{tgt_field} options"
					)

		if not any(label in e for e in errors):
			print(f"  ✅ {label} — {len(data_keys)} mappings verified")

	# 3. Validate active workflows match registry
	print(f"\n{'─' * 70}")
	print("  WORKFLOW STATE VALIDATION")
	print(f"{'─' * 70}")

	active_workflows = frappe.get_all(
		"Workflow",
		{"is_active": 1},
		["name", "document_type"],
	)

	for wf in active_workflows:
		dt = wf.document_type
		if dt not in STATUS_REGISTRY:
			continue

		states = frappe.get_all(
			"Workflow Document State",
			{"parent": wf.name, "update_field": ["!=", ""]},
			["state", "update_field", "update_value"],
		)

		for st in states:
			field = st.update_field
			value = st.update_value
			if not value:
				continue

			expected = STATUS_REGISTRY.get(dt, {}).get(field, [])
			if expected and value not in expected:
				errors.append(
					f"  Workflow '{wf.name}' state '{st.state}': "
					f"sets {dt}.{field}='{value}' — NOT in registry!"
				)

		if not any(wf.name in e for e in errors):
			print(f"  ✅ {wf.name} → {dt} — all states match registry")

	# Summary
	print(f"\n{'=' * 70}")
	if warnings:
		print(f"  ⚠️  WARNINGS ({len(warnings)}):")
		for w in warnings:
			print(w)

	if errors:
		print(f"\n  ❌ ERRORS ({len(errors)}):")
		for e in errors:
			print(e)
		print(f"\n  FIX: Update the doctype JSON field options or the code constants.")
		print(f"  Registry file: ch_item_master/seed_status_registry.py")
		print(f"{'=' * 70}")
		frappe.log_error(
			title="Status Registry Validation Failed",
			message="\n".join(errors),
		)
	else:
		print(f"\n  ✅ ALL {sum(len(f) for f in STATUS_REGISTRY.values())} status fields validated OK")
		print(f"{'=' * 70}")

	if fixed:
		print(f"\n  🔧 AUTO-FIXED ({len(fixed)}):")
		for f in fixed:
			print(f)

	return {"errors": errors, "warnings": warnings}
