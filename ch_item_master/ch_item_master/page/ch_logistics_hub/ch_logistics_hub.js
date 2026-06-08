/* CH Logistics Hub — Online/Bot Claim Pickup Queue
   Shows only Online/Bot and Phone channel claims in pickup stages.
   Walk-in / Store claims are handled at the store and never appear here. */

frappe.pages["ch-logistics-hub"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Logistics Hub"),
		single_column: true,
	});
	wrapper._hub = new LogisticsHub(page);
};

frappe.pages["ch-logistics-hub"].refresh = function (wrapper) {
	wrapper._hub && wrapper._hub.refresh();
};

const STATUS_COLORS = {
	"Pickup Requested":  "orange",
	"Pickup Scheduled":  "blue",
	"Picked Up":         "purple",
	"Device Received":   "green",
};

class LogisticsHub {
	constructor(page) {
		this.page = page;
		this._setup_filters();
		this._setup_container();
		this.refresh();
	}

	_setup_filters() {
		this.status_field = this.page.add_field({
			fieldname: "status", label: __("Status"), fieldtype: "MultiSelectList",
			options: ["Pickup Requested","Pickup Scheduled","Picked Up","Device Received"],
			default: [],
			change: () => this.refresh(),
		});
		this.company_field = this.page.add_field({
			fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			change: () => this.refresh(),
		});
		this.page.add_button(__("Refresh"), () => this.refresh(), { icon: "refresh" });
	}

	_setup_container() {
		this.$root = $(`<div class="lh-root" style="padding:16px 20px;"></div>`)
			.appendTo(this.page.body);
	}

	refresh() {
		const filters = {
			company: this.company_field?.get_value() || "",
			status: this.status_field?.get_value() || [],
		};
		this.$root.html(`<div style="padding:40px;text-align:center;color:var(--text-muted)">
			<i class="fa fa-spinner fa-spin"></i> ${__("Loading...")}
		</div>`);
		frappe.xcall(
			"ch_item_master.ch_item_master.page.ch_logistics_hub.ch_logistics_hub.get_logistics_queue",
			{ filters }
		).then(rows => this._render(rows))
		 .catch(() => {
			this.$root.html(`<div style="padding:40px;text-align:center;color:var(--error)">
				${__("Failed to load queue. Please retry.")}
			</div>`);
		});
	}

	_render(rows) {
		if (!rows.length) {
			this.$root.html(`<div style="padding:60px;text-align:center;color:var(--text-muted);font-size:13px;">
				<i class="fa fa-inbox" style="font-size:32px;display:block;margin-bottom:10px;opacity:.4;"></i>
				${__("No claims in the logistics queue.")}
			</div>`);
			return;
		}

		const cards = rows.map(r => this._card(r)).join("");
		this.$root.html(`
			<div style="margin-bottom:12px;font-size:12px;color:var(--text-muted);">
				${rows.length} ${__("claim(s) in queue")}
			</div>
			<div class="lh-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px;">
				${cards}
			</div>
		`);
		this.$root.find(".lh-action-btn").on("click", (e) => {
			const btn = $(e.currentTarget);
			const action = btn.data("action");
			const claim  = btn.data("claim");
			this[`_do_${action}`](claim, btn);
		});
	}

	_card(r) {
		const color = STATUS_COLORS[r.claim_status] || "grey";
		const slot  = r.pickup_slot ? frappe.datetime.str_to_user(r.pickup_slot) : "—";
		const actions = this._actions_html(r);
		return `
		<div class="lh-card" style="background:var(--card-bg);border:1px solid var(--border-color);
		     border-radius:8px;padding:14px;position:relative;">
			<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
				<span class="indicator-pill ${color}" style="flex-shrink:0;"></span>
				<a href="/app/ch-warranty-claim/${r.name}" target="_blank"
				   style="font-weight:700;font-size:13px;">${r.name}</a>
				<span style="margin-left:auto;font-size:10px;color:var(--text-muted);
				      background:var(--subtle-fg);padding:2px 8px;border-radius:4px;">
					${__(r.claim_channel)}
				</span>
			</div>
			<div style="font-size:12px;line-height:1.8;color:var(--text-color);">
				<b>${__("Customer")}:</b> ${frappe.utils.escape_html(r.customer_name || r.customer)} &nbsp;
				<span style="color:var(--text-muted);">${r.customer_phone || ""}</span><br>
				<b>${__("Device")}:</b> ${frappe.utils.escape_html(r.brand || "")} ${frappe.utils.escape_html(r.item_code || "")}
				 &nbsp; <code style="font-size:11px;">${r.serial_no || ""}</code><br>
				<b>${__("Slot")}:</b> ${slot}<br>
				<b>${__("Partner")}:</b> ${frappe.utils.escape_html(r.pickup_partner || "—")}<br>
				<b>${__("Tracking")}:</b> ${frappe.utils.escape_html(r.pickup_tracking_no || "—")}<br>
				<b>${__("Destination")}:</b> ${__(r.repair_destination || "Not Set")}
			</div>
			<div style="margin-top:6px;">
				${r.parcel_photo_1 ? `<a href="${r.parcel_photo_1}" target="_blank" style="font-size:11px;margin-right:6px;">📦 ${__("Parcel Photo 1")}</a>` : ""}
				${r.parcel_photo_2 ? `<a href="${r.parcel_photo_2}" target="_blank" style="font-size:11px;">📦 ${__("Parcel Photo 2")}</a>` : ""}
			</div>
			<div style="margin-top:12px;display:flex;flex-wrap:wrap;gap:6px;">
				${actions}
			</div>
		</div>`;
	}

	_actions_html(r) {
		const btn = (label, action, style="") =>
			`<button class="btn btn-xs btn-default lh-action-btn"
			         data-action="${action}" data-claim="${r.name}"
			         style="${style}">${__(label)}</button>`;
		const s = r.claim_status;
		let html = "";
		if (s === "Pickup Requested")  html += btn("Schedule Pickup", "schedule_pickup", "border-color:#f59e0b;color:#b45309;");
		if (s === "Pickup Scheduled")  html += btn("Mark Picked Up", "picked_up", "border-color:#8b5cf6;color:#6d28d9;");
		if (s === "Picked Up")         html += btn("Mark Device Received", "device_received", "border-color:#10b981;color:#065f46;");
		html += btn("View Claim", "view_claim", "");
		return html;
	}

	_do_schedule_pickup(claim, btn) {
		const d = new frappe.ui.Dialog({
			title: __("Schedule Pickup — {0}", [claim]),
			fields: [
				{ fieldname: "pickup_slot", fieldtype: "Datetime", label: __("Pickup Slot"), reqd: 1 },
				{ fieldname: "pickup_partner", fieldtype: "Data", label: __("Logistics Partner") },
				{ fieldname: "pickup_tracking_no", fieldtype: "Data", label: __("Tracking Number") },
			],
			primary_action_label: __("Schedule"),
			primary_action: (v) => {
				frappe.xcall(
					"ch_item_master.ch_item_master.page.ch_logistics_hub.ch_logistics_hub.schedule_pickup",
					{ claim_name: claim, ...v }
				).then(() => {
					d.hide();
					frappe.show_alert({ message: __("Pickup scheduled"), indicator: "green" });
					this.refresh();
				});
			},
		});
		d.show();
	}

	_do_picked_up(claim) {
		const d = new frappe.ui.Dialog({
			title: __("Mark Picked Up — {0}", [claim]),
			fields: [
				{ fieldname: "html_warn", fieldtype: "HTML",
				  options: `<div class="alert alert-warning" style="font-size:12px;">
				    ${__("Upload parcel photos BEFORE clicking Confirm for Online/Bot claims.")}
				  </div>` },
				{ fieldname: "delivery_otp", fieldtype: "Data", label: __("Delivery OTP") },
				{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") },
			],
			primary_action_label: __("Confirm Pickup"),
			primary_action: (v) => {
				frappe.xcall(
					"ch_item_master.ch_item_master.page.ch_logistics_hub.ch_logistics_hub.mark_picked_up",
					{ claim_name: claim, delivery_otp: v.delivery_otp, remarks: v.remarks }
				).then(() => {
					d.hide();
					frappe.show_alert({ message: __("Marked as picked up"), indicator: "green" });
					this.refresh();
				}).catch(err => {
					frappe.msgprint({ title: __("Error"), message: err.message || err, indicator: "red" });
				});
			},
		});
		d.show();
	}

	_do_device_received(claim) {
		const d = new frappe.ui.Dialog({
			title: __("Mark Device Received — {0}", [claim]),
			fields: [
				{ fieldname: "condition_on_receipt", fieldtype: "Select", label: __("Device Condition"),
				  options: "\nGood\nMinor Damage\nMajor Damage\nWrong Device\nEmpty Parcel\nIMEI Mismatch\nMissing Accessories",
				  reqd: 1 },
				{ fieldname: "accessories_received", fieldtype: "Data", label: __("Accessories Received") },
				{ fieldname: "imei_verified", fieldtype: "Check", label: __("IMEI Verified") },
				{ fieldname: "receiving_remarks", fieldtype: "Small Text", label: __("Remarks") },
			],
			primary_action_label: __("Confirm Receipt"),
			primary_action: (v) => {
				frappe.xcall(
					"ch_item_master.ch_item_master.page.ch_logistics_hub.ch_logistics_hub.mark_device_received",
					{ claim_name: claim, ...v }
				).then(() => {
					d.hide();
					frappe.show_alert({ message: __("Device received"), indicator: "green" });
					this.refresh();
				});
			},
		});
		d.show();
	}

	_do_view_claim(claim) {
		frappe.set_route("Form", "CH Warranty Claim", claim);
	}
}
