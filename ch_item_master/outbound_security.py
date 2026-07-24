from __future__ import annotations

import ipaddress
import json
import socket
from urllib.parse import urlsplit

import frappe
import requests
from frappe import _


def parse_exact_host_allowlist(raw_hosts, *, label: str) -> set[str]:
	hosts = {
		entry.strip().lower().rstrip(".")
		for entry in str(raw_hosts or "").replace(",", "\n").splitlines()
		if entry.strip()
	}
	if not hosts:
		frappe.throw(
			_("Configure at least one allowed host for {0}.").format(label),
			frappe.ValidationError,
		)
	for host in hosts:
		if (
			"://" in host
			or "/" in host
			or "@" in host
			or ":" in host
			or any(character.isspace() for character in host)
		):
			frappe.throw(
				_("The {0} host allowlist contains an invalid hostname.").format(label),
				frappe.ValidationError,
			)
		try:
			ascii_host = host.encode("idna").decode("ascii")
		except UnicodeError:
			frappe.throw(_("The {0} host allowlist is invalid.").format(label), frappe.ValidationError)
		if ascii_host != host:
			frappe.throw(
				_("Use ASCII hostnames in the {0} host allowlist.").format(label),
				frappe.ValidationError,
			)
		try:
			ipaddress.ip_address(host)
		except ValueError:
			pass
		else:
			frappe.throw(
				_("IP literals are not permitted in the {0} host allowlist.").format(label),
				frappe.ValidationError,
			)
	return hosts


def validate_allowed_https_url(
	endpoint,
	allowed_hosts: set[str],
	*,
	label: str,
	resolve_dns: bool = True,
) -> str:
	endpoint = str(endpoint or "").strip()
	if not endpoint or "\\" in endpoint or any(character.isspace() for character in endpoint):
		frappe.throw(_("{0} URL is invalid.").format(label), frappe.ValidationError)
	try:
		parsed = urlsplit(endpoint)
		port = parsed.port
	except ValueError:
		frappe.throw(_("{0} URL is invalid.").format(label), frappe.ValidationError)
	hostname = (parsed.hostname or "").lower().rstrip(".")
	if (
		parsed.scheme.lower() != "https"
		or not hostname
		or parsed.username
		or parsed.password
		or parsed.fragment
		or port not in (None, 443)
	):
		frappe.throw(
			_("{0} must use HTTPS on port 443 without embedded credentials.").format(label),
			frappe.ValidationError,
		)
	if hostname not in allowed_hosts:
		frappe.throw(
			_("{0} host {1} is not allowlisted.").format(label, hostname),
			frappe.PermissionError,
		)
	if not resolve_dns:
		return endpoint
	try:
		addresses = {
			row[4][0]
			for row in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
		}
	except (socket.gaierror, OSError):
		frappe.throw(_("{0} host could not be resolved safely.").format(label))
	if not addresses:
		frappe.throw(_("{0} host did not resolve to an address.").format(label))
	for address in addresses:
		try:
			parsed_address = ipaddress.ip_address(address)
		except ValueError:
			frappe.throw(_("{0} resolved to an invalid address.").format(label))
		if not parsed_address.is_global:
			frappe.throw(
				_("{0} cannot target a private, loopback, or link-local address.").format(label),
				frappe.PermissionError,
			)
	return endpoint


def post_json_with_credentials(
	endpoint,
	*,
	allowed_hosts: set[str],
	label: str,
	payload: dict,
	timeout: int,
	max_response_bytes: int,
	auth=None,
	headers: dict | None = None,
) -> dict:
	endpoint = validate_allowed_https_url(endpoint, allowed_hosts, label=label)
	response = requests.post(
		endpoint,
		auth=auth,
		headers=headers,
		json=payload,
		timeout=timeout,
		allow_redirects=False,
		stream=True,
	)
	try:
		if 300 <= response.status_code < 400:
			frappe.throw(_("{0} redirects are not permitted.").format(label), frappe.ValidationError)
		response.raise_for_status()
		content_length = response.headers.get("Content-Length")
		if content_length and int(content_length) > max_response_bytes:
			frappe.throw(_("{0} response exceeded the configured size limit.").format(label))
		body = bytearray()
		for chunk in response.iter_content(chunk_size=min(max_response_bytes + 1, 8192)):
			body.extend(chunk)
			if len(body) > max_response_bytes:
				frappe.throw(_("{0} response exceeded the configured size limit.").format(label))
		try:
			data = json.loads(body.decode("utf-8"))
		except (UnicodeDecodeError, json.JSONDecodeError):
			frappe.throw(_("{0} returned an invalid JSON response.").format(label))
		if not isinstance(data, dict):
			frappe.throw(_("{0} returned an invalid JSON response.").format(label))
		return data
	finally:
		response.close()
