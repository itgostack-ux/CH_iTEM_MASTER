# Copyright (c) 2026, Congruence Holdings and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from ch_item_master.outbound_security import parse_exact_host_allowlist, validate_allowed_https_url


class CHWhatsAppSettings(Document):
    def validate(self):
        self.allowed_hosts = self.allowed_hosts or "server.gallabox.com"
        self.base_url = self.base_url or "https://server.gallabox.com/devapi/messages/whatsapp"
        self.gateway_timeout_seconds = cint(self.gateway_timeout_seconds) or 30
        self.gateway_response_max_bytes = cint(self.gateway_response_max_bytes) or 65536
        if self.gateway_timeout_seconds < 1 or self.gateway_timeout_seconds > 60:
            frappe.throw(_("Gateway Timeout must be between 1 and 60 seconds."))
        if self.gateway_response_max_bytes < 1024 or self.gateway_response_max_bytes > 1048576:
            frappe.throw(_("Gateway Response Maximum Bytes must be between 1 KB and 1 MB."))
        if not self.enabled:
            return
        allowed_hosts = parse_exact_host_allowlist(self.allowed_hosts, label="WhatsApp Gateway")
        validate_allowed_https_url(
            self.base_url,
            allowed_hosts,
            label="WhatsApp Gateway",
            resolve_dns=False,
        )
