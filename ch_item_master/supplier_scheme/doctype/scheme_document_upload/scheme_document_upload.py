"""Scheme Document Upload — AI-powered scheme circular parsing.

Upload a brand scheme PDF/image → AI extracts structured data
→ review → create one or more Supplier Scheme Circulars.
"""

import json
from pathlib import Path

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

from ch_item_master.config import get_int_setting
from ch_item_master.outbound_security import parse_exact_host_allowlist, post_json_with_credentials
from ch_item_master.security import require_scoped_document_action
_SCHEME_MANAGEMENT_ROLES = ("Accounts Manager", "Purchase Manager", "Scheme Manager")


class SchemeDocumentUpload(Document):
	pass


def _require_upload_action(doc, action):
	if not doc.company:
		frappe.throw(_("Company is required for a scheme upload."), frappe.ValidationError)
	require_scoped_document_action(
		doc,
		"supplier_scheme_management_roles",
		_SCHEME_MANAGEMENT_ROLES,
		action=action,
		permission_types=("read", "write"),
		company_field="company",
		lock=True,
	)


def _enforce_scheme_ai_rate_limit(endpoint):
	try:
		from ch_pos.api.ai import _enforce_ai_rate_limit
	except (ImportError, ModuleNotFoundError):
		frappe.throw(_("POS AI rate-limit service is unavailable."), frappe.PermissionError)
	_enforce_ai_rate_limit(endpoint)


def _max_payload_chars():
	configured = get_int_setting(
		"supplier_scheme_ai_payload_char_limit",
		8000,
		minimum=1000,
	)
	return min(configured, 1_000_000)


def _validate_payload_size(value):
	serialized = value if isinstance(value, str) else json.dumps(value or {}, default=str)
	if len(serialized) > _max_payload_chars():
		frappe.throw(_("Scheme payload exceeds the configured AI payload limit."), frappe.ValidationError)


def _get_owned_private_file(upload_doc):
	if not upload_doc.document_file:
		frappe.throw(_("Please attach a document first"), title=_("Scheme Document Upload Error"))
	file_names = frappe.get_all(
		"File",
		filters={
			"file_url": upload_doc.document_file,
			"attached_to_doctype": upload_doc.doctype,
			"attached_to_name": upload_doc.name,
		},
		pluck="name",
		order_by="creation desc",
		limit_page_length=2,
	)
	if len(file_names) != 1:
		frappe.throw(_("The uploaded file reference is missing or ambiguous."), frappe.ValidationError)
	frappe.db.get_value("File", file_names[0], "name", for_update=True)
	file_doc = frappe.get_doc("File", file_names[0])
	file_doc.check_permission("read")
	if (
		not file_doc.is_private
		or not (file_doc.file_url or "").startswith("/private/files/")
		or file_doc.attached_to_doctype != upload_doc.doctype
		or file_doc.attached_to_name != upload_doc.name
		or (file_doc.attached_to_field and file_doc.attached_to_field != "document_file")
	):
		frappe.throw(_("Scheme documents must be private files attached to this upload."), frappe.PermissionError)

	file_path = Path(file_doc.get_full_path()).resolve()
	private_root = Path(frappe.get_site_path("private", "files")).resolve()
	if private_root not in file_path.parents:
		frappe.throw(_("Scheme document path is outside the private file store."), frappe.PermissionError)
	return file_doc


def _validate_upload_references(doc):
	company = frappe.get_doc("Company", doc.company)
	company.check_permission("read")
	for doctype, name in (("Brand", doc.brand), ("Supplier", doc.supplier)):
		if name:
			reference = frappe.get_doc(doctype, name)
			reference.check_permission("read")


@frappe.whitelist(methods=["POST"])
def extract_scheme_data(upload_name) -> dict:
	"""Parse the uploaded document using AI and store extracted JSON."""
	doc = frappe.get_doc("Scheme Document Upload", upload_name)
	_require_upload_action(doc, _("extract supplier scheme data"))
	_validate_upload_references(doc)
	file_doc = _get_owned_private_file(doc)
	_enforce_scheme_ai_rate_limit("supplier-scheme-extraction")

	ai_settings = _get_ai_settings()
	if not ai_settings:
		frappe.throw(_("POS AI Settings not configured. Please set up API key."), title=_("Scheme Document Upload Error"))

	# Clear previous log rows so a retry starts fresh
	doc.extraction_logs = []

	try:
		_add_log(doc, "Info", "File Read", "Reading uploaded document from file system")
		file_content = _read_file_content(file_doc)
		mime = file_content.get("mime_type", "unknown")
		_add_log(doc, "Info", "File Read", f"File loaded — type: {mime}, size: {len(file_content.get('base64', '')) * 3 // 4} bytes")

		_add_log(doc, "Info", "AI Request", f"Sending to AI model {ai_settings.get('model')} (timeout {ai_settings['timeout']}s)")
		extracted = _call_ai_extraction(file_content, ai_settings)
		_validate_payload_size(extracted)
		_add_log(doc, "Info", "AI Request", "AI responded successfully", ai_model=ai_settings.get("model"))

		doc.extracted_json = json.dumps(extracted, indent=2, ensure_ascii=False)
		doc.ai_model_used = ai_settings.get("model")
		doc.status = "Extracted"

		# Auto-detect brand
		brand_name = extracted.get("brand", "")
		if brand_name and frappe.db.exists("Brand", brand_name):
			doc.brand = brand_name
		elif brand_name:
			# Try fuzzy match
			match = frappe.db.get_value("Brand", {"name": ["like", f"%{brand_name}%"]}, "name")
			if match:
				doc.brand = match

		schemes = extracted.get("schemes", [])
		_add_log(doc, "Info", "Extraction Complete", f"Extracted {len(schemes)} scheme part(s)")
		for i, s in enumerate(schemes):
			rules_count = len(s.get("rules", []))
			_add_log(doc, "Info", "Extraction Complete",
				f"Part {i+1}: {s.get('scheme_name', '?')} — {rules_count} rule lines")

		# Keep legacy text field in sync for backward compat
		summary_lines = [f"Extracted {len(schemes)} scheme part(s)"]
		for i, s in enumerate(schemes):
			summary_lines.append(f"  Part {i+1}: {s.get('scheme_name', '?')} — {len(s.get('rules', []))} rule lines")
		doc.extraction_log = "\n".join(summary_lines)

		doc.save()
		return {
			"success": True,
			"schemes_count": len(schemes),
			"extracted": extracted,
		}
	except Exception:
		tb = frappe.get_traceback()
		# User-friendly message depending on error type
		if "ReadTimeoutError" in tb or "Timeout" in tb or "timed out" in tb.lower():
			user_msg = _("AI request timed out. The document may be large or the server is busy. "
				"Please retry — the timeout is set to {0}s. You can increase it in POS AI Settings."
			).format(ai_settings.get("timeout", 120))
			step_label = "AI Request Timeout"
		elif "ConnectionError" in tb or "NewConnectionError" in tb:
			user_msg = _("Could not reach the AI server (api.openai.com). Check internet connectivity and retry.")
			step_label = "AI Connection Error"
		elif "401" in tb or "Unauthorized" in tb:
			user_msg = _("AI API key rejected (401 Unauthorized). Please update the API key in POS AI Settings.")
			step_label = "AI Auth Error"
		elif "429" in tb or "RateLimitError" in tb:
			user_msg = _("AI rate limit reached. Wait a minute and retry.")
			step_label = "AI Rate Limit"
		else:
			user_msg = _("AI extraction failed. See the Extraction Log table for the full error.")
			step_label = "Extraction Error"

		_add_log(doc, "Error", step_label, user_msg, traceback=tb[-3000:])
		doc.status = "Failed"
		doc.extraction_log = user_msg + "\n\n" + tb[-1500:]
		doc.save()
		frappe.log_error(tb, f"Scheme extraction failed: {upload_name}")
		return {"success": False, "error": user_msg}


def _validate_scheme_payload(data):
	if not isinstance(data, dict):
		frappe.throw(_("Scheme data must be a JSON object."), frappe.ValidationError)
	_validate_payload_size(data)
	schemes = data.get("schemes", [])
	if not isinstance(schemes, list) or not schemes:
		frappe.throw(_("No schemes found in extracted data"), title=_("Scheme Document Upload Error"))
	row_limit = min(get_int_setting("supplier_scheme_link_limit", 200, minimum=1), 1000)
	if len(schemes) > row_limit:
		frappe.throw(_("A maximum of {0} schemes can be created at once.").format(row_limit))
	rule_count = 0
	for scheme in schemes:
		if not isinstance(scheme, dict) or not isinstance(scheme.get("rules", []), list):
			frappe.throw(_("Every scheme must be an object with a rules list."), frappe.ValidationError)
		rule_count += len(scheme.get("rules", []))
		if rule_count > row_limit:
			frappe.throw(_("A maximum of {0} scheme rule rows can be created at once.").format(row_limit))
		for row in scheme.get("rules", []):
			if not isinstance(row, dict):
				frappe.throw(_("Every scheme rule row must be an object."), frappe.ValidationError)
	return schemes


@frappe.whitelist(methods=["POST"])
def create_schemes_from_upload(upload_name, schemes_json=None) -> dict:
	"""Create Supplier Scheme Circular(s) from extracted data.

	If schemes_json is provided, use it (user may have edited).
	Otherwise use the stored extracted_json.
	"""
	doc = frappe.get_doc("Scheme Document Upload", upload_name)
	_require_upload_action(doc, _("create supplier schemes from an upload"))
	_validate_upload_references(doc)
	_get_owned_private_file(doc)
	_enforce_scheme_ai_rate_limit("supplier-scheme-create")

	if not doc.extracted_json:
		frappe.throw(_("No extracted data. Please run extraction first."), title=_("Scheme Document Upload Error"))
	_validate_payload_size(doc.extracted_json)
	stored_data = json.loads(doc.extracted_json)
	stored_schemes = _validate_scheme_payload(stored_data)
	for stored_scheme in stored_schemes:
		stored_scheme.pop("_created_doc", None)

	if schemes_json:
		if isinstance(schemes_json, str):
			_validate_payload_size(schemes_json)
			selected_data = json.loads(schemes_json)
		else:
			selected_data = schemes_json
		selected_schemes = _validate_scheme_payload(selected_data)
		selected_parts = []
		seen_parts = set()
		for selected in selected_schemes:
			part = cint(selected.get("_source_part"))
			if part < 1 or part > len(stored_schemes) or part in seen_parts:
				frappe.throw(_("Selected scheme part reference is invalid."), frappe.ValidationError)
			seen_parts.add(part)
			edited = dict(selected)
			edited.pop("_source_part", None)
			edited.pop("_created_doc", None)
			stored_schemes[part - 1] = edited
			selected_parts.append((part, edited))
		for key, value in selected_data.items():
			if key != "schemes":
				stored_data[key] = value
	else:
		selected_parts = [(index, scheme) for index, scheme in enumerate(stored_schemes, start=1)]

	stored_data["schemes"] = stored_schemes
	existing_rows = frappe.get_all(
		"Supplier Scheme Circular",
		filters={"source_upload": doc.name, "company": doc.company, "docstatus": ("!=", 2)},
		fields=["name", "source_upload_part", "company"],
		order_by="source_upload_part asc, creation asc",
		limit_page_length=len(stored_schemes) + 1,
	)
	existing_by_part = {}
	for row in existing_rows:
		part = cint(row.source_upload_part)
		if part < 1 or part > len(stored_schemes) or part in existing_by_part:
			frappe.throw(_("Scheme upload has duplicate or invalid source references."), frappe.ValidationError)
		existing = frappe.get_doc("Supplier Scheme Circular", row.name)
		existing.check_permission("read")
		if existing.company != doc.company or existing.source_upload != doc.name:
			frappe.throw(_("Existing scheme source reference failed integrity validation."), frappe.ValidationError)
		existing_by_part[part] = existing.name
		stored_schemes[part - 1]["_created_doc"] = existing.name

	created = []
	for part, scheme_data in selected_parts:
		if part in existing_by_part:
			created.append(existing_by_part[part])
			continue
		frappe.has_permission("Supplier Scheme Circular", ptype="create", throw=True)
		circular = _create_single_circular(scheme_data, doc, stored_data, part)
		created.append(circular.name)
		stored_schemes[part - 1]["_created_doc"] = circular.name

	# Persist updated extracted_json with _created_doc markers
	stored_data["schemes"] = stored_schemes
	doc.extracted_json = json.dumps(stored_data, indent=2, ensure_ascii=False)

	all_created = [scheme.get("_created_doc") for scheme in stored_schemes if scheme.get("_created_doc")]
	doc.created_schemes = ", ".join(dict.fromkeys(all_created))

	all_done = all(s.get("_created_doc") for s in stored_schemes)
	if all_done:
		doc.status = "Schemes Created"
	else:
		doc.status = "Partial"
	doc.save()
	return {
		"created": created,
		"failed_names": [],
		"count": len(created),
		"failed": 0,
	}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_ai_settings():
	"""Get AI config from POS AI Settings."""
	try:
		settings = frappe.get_cached_doc("POS AI Settings")
		if not settings.enable_ai:
			return None
		api_key = settings.get_password("api_key")
		if not api_key:
			return None
		return {
			"api_key": api_key,
			"endpoint": settings.api_endpoint or "https://api.openai.com/v1/chat/completions",
			"allowed_hosts": settings.get("allowed_api_hosts") or "api.openai.com",
			"model": settings.comparison_model or "gpt-4o",
			# Minimum 120s — scheme images can be large and GPT-4o vision is slow
			"timeout": max(settings.timeout_sec or 120, 120),
			"max_response_bytes": min(
				get_int_setting("supplier_scheme_ai_response_byte_limit", 1048576, minimum=1024),
				8388608,
			),
		}
	except frappe.DoesNotExistError:
		return None


def _add_log(doc, level, step, message, traceback=None, ai_model=None):
	"""Append one row to the doc's extraction_logs child table."""
	from frappe.utils import now_datetime
	row = doc.append("extraction_logs", {
		"timestamp": now_datetime(),
		"level": level,
		"step": step,
		"message": (message or "")[:500],
		"traceback": traceback or "",
		"ai_model": ai_model or "",
	})
	return row


def _read_file_content(file_doc):
	"""Read file and return base64 content + mime type for AI vision.

	Validation (H16):
	  - System-configured maximum file size
	  - MIME type must be PDF / image (PNG, JPEG, GIF, WebP)
	  - Magic bytes must match MIME type (prevent spoofed extensions)
	"""
	import base64
	import mimetypes
	from frappe.core.api.file import get_max_file_size

	file_path = file_doc.get_full_path()
	mime_type = mimetypes.guess_type(file_path)[0] or "application/pdf"
	max_file_size = get_max_file_size()
	if cint(file_doc.file_size) > max_file_size:
		frappe.throw(
			_("File exceeds the configured maximum size of {0} MB.").format(
				round(max_file_size / 1024 / 1024, 2)
			),
			title=_("File Size Error"),
		)

	with open(file_path, "rb") as f:
		raw = f.read(max_file_size + 1)

	if len(raw) > max_file_size:
		frappe.throw(
			_("File exceeds the configured maximum size."),
			title=_("File Size Error"),
		)

	# MIME type whitelist (H16)
	allowed_mimes = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"}
	if mime_type not in allowed_mimes:
		frappe.throw(
			_("File type not supported. Please upload PDF or image (PNG, JPEG, GIF, WebP)."),
			title=_("File Type Error"),
		)

	# Magic byte validation (H16) — prevent spoofed file extensions
	magic_checks = {
		"application/pdf": b"%PDF",
		"image/png": b"\x89PNG",
		"image/jpeg": b"\xff\xd8\xff",
		"image/gif": b"GIF8",
		"image/webp": b"RIFF",  # partial — more complex, but catches most spoofs
	}

	expected_magic = magic_checks.get(mime_type)
	webp_invalid = mime_type == "image/webp" and (len(raw) < 12 or raw[8:12] != b"WEBP")
	if (expected_magic and not raw.startswith(expected_magic)) or webp_invalid:
		frappe.throw(
			_("File content does not match the declared type. Possible file spoofing."),
			title=_("File Validation Error"),
		)

	# For PDFs, try text extraction first (cheaper, faster)
	text_content = ""
	if mime_type == "application/pdf":
		text_content = _extract_pdf_text(raw)

	return {
		"base64": base64.b64encode(raw).decode("utf-8"),
		"mime_type": mime_type,
		"text_content": text_content,
		"file_name": file_doc.file_name,
		"_raw_bytes": raw,
	}


def _extract_pdf_text(raw_bytes):
	"""Extract text from PDF bytes. Returns empty string on failure."""
	try:
		import io

		# Try PyMuPDF (fitz) — installed as pymupdf, best for tables/columns
		try:
			import fitz  # pymupdf
			doc = fitz.open(stream=raw_bytes, filetype="pdf")
			pages = []
			for page in doc:
				pages.append(page.get_text("text") or "")
			doc.close()
			return "\n\n--- PAGE BREAK ---\n\n".join(pages)
		except ImportError:
			pass

		# Fallback to pdfplumber if available
		try:
			import pdfplumber
			with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
				pages = []
				for page in pdf.pages:
					text = page.extract_text() or ""
					tables = page.extract_tables() or []
					for table in tables:
						for row in table:
							if row:
								text += "\n" + " | ".join(str(cell or "") for cell in row)
					pages.append(text)
				return "\n\n--- PAGE BREAK ---\n\n".join(pages)
		except ImportError:
			pass

		# Fallback to PyPDF2
		try:
			from PyPDF2 import PdfReader
			reader = PdfReader(io.BytesIO(raw_bytes))
			pages = []
			for page in reader.pages:
				pages.append(page.extract_text() or "")
			return "\n\n--- PAGE BREAK ---\n\n".join(pages)
		except ImportError:
			pass

	except Exception:
		pass

	return ""


EXTRACTION_SYSTEM_PROMPT = """You are an expert at parsing retail/mobile brand scheme circulars.

Extract ALL scheme parts from the document into structured JSON.

A single circular document often contains MULTIPLE scheme parts (e.g., Part-1: slab scheme for all models, Part-2: focus model add-on, Part-3: EOL scheme, Part-4: series-specific).

IMPORTANT for brand detection: The brand is the MANUFACTURER whose products the scheme is about (e.g., Oppo, Samsung, Vivo, Apple, OnePlus). Look for brand names in model names, branding clauses, or the document context. The brand is NOT the dealer/distributor type like "Super Dealers".

For EACH scheme part, extract:
- scheme_name: descriptive name
- rule_type: one of "Quantity Slab", "Focus Model Add-on", "EOL Scheme", "Series Scheme", "RRP Band Scheme"
- payout_basis: one of "Per Unit", "Slab Based", "Additional Margin"
- achievement_basis: "Warranty Date" (if mentions e-warranty), "Sell-out Date" (if sell-out), else "Invoice Date"
- stackable: true if this part adds ON TOP of another part
- notes: any special conditions for this part
- rules: array of line items. IMPORTANT: For large slab tables (like Part-1 with many models × slabs), extract ALL model+slab combinations as separate line items — do NOT summarize. Each unique (model, slab) = one rule line.

Each rule line item has:
  - series: model series/family name (e.g. "Reno 15 Pro", "F31", "FindX9")
  - model_variant: specific variant if applicable (e.g. "8+128", "12+256GB")
  - qty_from, qty_to: slab quantity range (0 for unlimited upper bound)
  - payout_per_unit: rebate amount per unit in Rs
  - rrp_from, rrp_to: RRP price band (for EOL/RRP band schemes)
  - include_in_slab: true if counts toward slab achievement
  - eligible_for_payout: true if gets payout
  - remarks: any special notes for this line

Also extract top-level info:
- brand: the MANUFACTURER brand name (Oppo, Samsung, etc.)
- circular_number: document reference number
- issue_date: YYYY-MM-DD
- valid_from: YYYY-MM-DD
- valid_to: YYYY-MM-DD
- settlement_type: "Credit Note" or "Cash"
- tds_applicable: true/false
- tds_percent: number
- terms: array of key terms & conditions (strings)

Also return a top-level "confidence" score (0-100) reflecting how complete and unambiguous the extraction was. Deduct points for: missing dates, ambiguous payout values, unclear model names, incomplete slab tables.

Return JSON with structure:
{
  "brand": "...",
  "circular_number": "...",
  "issue_date": "...",
  "valid_from": "...",
  "valid_to": "...",
  "settlement_type": "...",
  "tds_applicable": true/false,
  "tds_percent": 10,
  "confidence": 85,
  "terms": ["...", "..."],
  "schemes": [
    {
      "scheme_name": "...",
      "rule_type": "...",
      "payout_basis": "...",
      "achievement_basis": "...",
      "stackable": false,
      "notes": "...",
      "confidence": 90,
      "rules": [
        {
          "series": "...",
          "model_variant": "...",
          "qty_from": 0,
          "qty_to": 0,
          "payout_per_unit": 0,
          "rrp_from": 0,
          "rrp_to": 0,
          "include_in_slab": true,
          "eligible_for_payout": true,
          "remarks": ""
        }
      ]
    }
  ]
}"""


def _send_ai_request(messages, ai_settings):
	"""POST messages to OpenAI and return parsed JSON response."""
	endpoint = ai_settings["endpoint"]
	response = post_json_with_credentials(
		endpoint,
		allowed_hosts=parse_exact_host_allowlist(
			ai_settings["allowed_hosts"],
			label="Supplier Scheme AI",
		),
		label="Supplier Scheme AI",
		headers={
			"Authorization": f"Bearer {ai_settings['api_key']}",
			"Content-Type": "application/json",
		},
		payload={
			"model": ai_settings["model"],
			"messages": messages,
			"max_tokens": 16000,
			"response_format": {"type": "json_object"},
		},
		timeout=ai_settings["timeout"],
		max_response_bytes=ai_settings["max_response_bytes"],
	)
	content = response["choices"][0]["message"]["content"]
	return json.loads(content)


def _call_ai_extraction(file_content, ai_settings):
	"""Call OpenAI API (text or vision mode) to extract scheme data."""
	messages = [
		{"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
	]

	text = file_content.get("text_content", "")
	mime = file_content.get("mime_type", "")
	b64 = file_content.get("base64", "")

	# Text mode (cheaper): used when PDF text extraction succeeded
	if text and len(text) > 200:
		messages.append({
			"role": "user",
			"content": (
				f"Parse this scheme circular document:\n\n{text}\n\n"
				"Extract all scheme parts into the specified JSON structure."
			),
		})
		return _send_ai_request(messages, ai_settings)

	# Vision mode — OpenAI only accepts JPEG/PNG/WEBP/GIF, NOT application/pdf.
	if mime == "application/pdf":
		# Render PDF pages to PNG via PyMuPDF
		try:
			import base64 as _b64
			import fitz
			pdf_doc = fitz.open(stream=file_content.get("_raw_bytes", b""), filetype="pdf")
			for page_num in range(min(len(pdf_doc), 5)):  # cap at 5 pages
				pix = pdf_doc[page_num].get_pixmap(dpi=150)
				page_b64 = _b64.b64encode(pix.tobytes("png")).decode("utf-8")
				messages.append({
					"role": "user",
					"content": [
						{"type": "text", "text": f"Page {page_num + 1} of the scheme circular:"},
						{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{page_b64}"}},
					],
				})
			pdf_doc.close()
			messages.append({"role": "user", "content": "Extract all scheme parts into the specified JSON structure."})
		except Exception as e:
			frappe.throw(
				_("Could not render this PDF for AI analysis: {0}").format(str(e)),
				title=_("Scheme Document Upload Error"),
			)
	else:
		# Image file (JPEG, PNG, etc.) — send directly
		messages.append({
			"role": "user",
			"content": [
				{"type": "text", "text": "Parse this scheme circular document. Extract all scheme parts into the specified JSON structure."},
				{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
			],
		})

	return _send_ai_request(messages, ai_settings)


def _create_single_circular(scheme_data, upload_doc, top_data, part_number):
	"""Create one Supplier Scheme Circular from parsed scheme data."""
	circular = frappe.new_doc("Supplier Scheme Circular")
	circular.scheme_name = scheme_data.get("scheme_name", "Untitled Scheme")
	circular.circular_number = top_data.get("circular_number", "")
	circular.company = upload_doc.company
	circular.brand = upload_doc.brand or ""
	circular.supplier = upload_doc.supplier or ""
	circular.source_upload = upload_doc.name
	circular.source_upload_part = part_number

	# Dates
	valid_from = top_data.get("valid_from")
	valid_to = top_data.get("valid_to")
	issue_date = top_data.get("issue_date")
	if valid_from:
		circular.valid_from = getdate(valid_from)
	if valid_to:
		circular.valid_to = getdate(valid_to)
	if issue_date:
		circular.issue_date = getdate(issue_date)

	# Settlement
	circular.settlement_type = top_data.get("settlement_type", "Credit Note")
	circular.tds_applicable = 1 if top_data.get("tds_applicable") else 0
	if top_data.get("tds_percent"):
		circular.tds_percent = flt(top_data["tds_percent"])

	# Attach the original document
	if upload_doc.document_file:
		circular.circular_attachment = upload_doc.document_file

	# Description: combine terms as HTML
	terms = top_data.get("terms", [])
	if not isinstance(terms, list):
		frappe.throw(_("Scheme terms must be a list."), frappe.ValidationError)
	if terms:
		terms_html = "<h4>Terms & Conditions</h4><ol>"
		for t in terms:
			if not isinstance(t, str):
				frappe.throw(_("Every scheme term must be text."), frappe.ValidationError)
			terms_html += f"<li>{frappe.utils.escape_html(t)}</li>"
		terms_html += "</ol>"
		circular.description = terms_html

	# AI confidence: use per-scheme confidence if available, else top-level
	scheme_conf = flt(scheme_data.get("confidence", top_data.get("confidence", 0)))
	if scheme_conf:
		circular.ai_confidence_score = scheme_conf

	# Build rule via new_doc so its _table_fieldnames comes from meta
	# (Frappe v16: child rows created via parent.append() get empty _table_fieldnames,
	#  blocking grandchild .append() calls. Passing a BaseDocument skips that override.)
	rule = frappe.new_doc("Supplier Scheme Rule")
	rule.rule_name = scheme_data.get("scheme_name", "Rule 1")
	rule.rule_type = _map_rule_type(scheme_data.get("rule_type", "Quantity Slab"))
	rule.payout_basis = scheme_data.get("payout_basis", "Per Unit")
	rule.achievement_basis = scheme_data.get("achievement_basis", "Invoice Date")
	rule.stackable = 1 if scheme_data.get("stackable") else 0
	rule.notes = scheme_data.get("notes", "")

	# Build rule details (line items)
	for line in scheme_data.get("rules", []):
		detail = rule.append("details", {})
		detail.series = line.get("series", "")
		detail.qty_from = line.get("qty_from", 0)
		detail.qty_to = line.get("qty_to", 0)
		detail.payout_per_unit = flt(line.get("payout_per_unit", 0))
		detail.additional_payout = flt(line.get("additional_payout", 0))
		detail.rrp_from = flt(line.get("rrp_from", 0))
		detail.rrp_to = flt(line.get("rrp_to", 0))
		detail.include_in_slab = 1 if line.get("include_in_slab", True) else 0
		detail.eligible_for_payout = 1 if line.get("eligible_for_payout", True) else 0
		detail.exclusion_flag = 0
		remarks_parts = []
		if line.get("model_variant"):
			remarks_parts.append(f"Variant: {line['model_variant']}")
		if line.get("remarks"):
			remarks_parts.append(line["remarks"])
		detail.remarks = "; ".join(remarks_parts) if remarks_parts else ""

		# Apply resolved product mapping (set by product_mapper.resolve_scheme_products)
		if line.get("_item_code"):
			detail.item_code = line["_item_code"]
		if line.get("_model"):
			detail.model = line["_model"]
		if line.get("_item_group"):
			detail.item_group = line["_item_group"]

		# Store original supplier product name for traceability
		series = line.get("series", "")
		variant = line.get("model_variant", "")
		if series or variant:
			detail.supplier_product_name = f"{series} {variant}".strip() if series and variant else (series or variant)

	# Attach the fully-built rule (with its details) to the circular
	circular.append("rules", rule)

	circular.insert()
	return circular


def _map_rule_type(ai_type):
	"""Map AI-extracted rule type to valid DocType options."""
	valid = {
		"Quantity Slab", "Focus Model Add-on", "EOL Scheme",
		"Series Scheme", "RRP Band Scheme",
	}
	if ai_type in valid:
		return ai_type
	# Fuzzy matching
	lower = (ai_type or "").lower()
	if "focus" in lower or "add-on" in lower or "addon" in lower:
		return "Focus Model Add-on"
	if "eol" in lower or "end of life" in lower:
		return "EOL Scheme"
	if "series" in lower:
		return "Series Scheme"
	if "rrp" in lower or "price band" in lower:
		return "RRP Band Scheme"
	return "Quantity Slab"
