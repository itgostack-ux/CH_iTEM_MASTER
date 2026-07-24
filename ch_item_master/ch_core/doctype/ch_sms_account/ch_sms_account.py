import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from ch_item_master.outbound_security import parse_exact_host_allowlist, validate_allowed_https_url


class CHSMSAccount(Document):
    def validate(self):
        timeout = cint(self.gateway_timeout_seconds) or 10
        if timeout < 1 or timeout > 60:
            frappe.throw(_("Gateway Timeout must be between 1 and 60 seconds."))
        self.gateway_timeout_seconds = timeout
        if not self.enabled:
            return
        allowed_hosts = parse_exact_host_allowlist(self.allowed_hosts, label="SMS Gateway")
        validate_allowed_https_url(
            self.sms_gateway_url,
            allowed_hosts,
            label="SMS Gateway",
            resolve_dns=False,
        )
