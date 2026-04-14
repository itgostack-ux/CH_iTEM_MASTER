"""Scheme Document Upload — AI-powered scheme circular parsing.

Upload a brand scheme PDF/image → AI extracts structured data
→ review → create one or more Supplier Scheme Circulars.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate


class SchemeDocumentUpload(Document):
	pass


# ---------------------------------------------------------------------------
# AI Extraction API
# ---------------------------------------------------------------------------

@frappe.whitelist()
def extract_scheme_data(upload_name) -> dict:
	"""Parse the uploaded document using AI and store extracted JSON."""
	doc = frappe.get_doc("Scheme Document Upload", upload_name)
	if not doc.document_file:
		frappe.throw(_("Please attach a document first"), title=_("Scheme Document Upload Error"))

	ai_settings = _get_ai_settings()
	if not ai_settings:
		frappe.throw(_("POS AI Settings not configured. Please set up API key."), title=_("Scheme Document Upload Error"))

	try:
		file_url = doc.document_file
		file_content = _read_file_content(file_url)

		extracted = _call_ai_extraction(file_content, ai_settings)

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

		log_lines = []
		schemes = extracted.get("schemes", [])
		log_lines.append(f"Extracted {len(schemes)} scheme part(s)")
		for i, s in enumerate(schemes):
			rules_count = len(s.get("rules", []))
			log_lines.append(f"  Part {i+1}: {s.get('scheme_name', '?')} — {rules_count} rule lines")
		doc.extraction_log = "\n".join(log_lines)

		doc.save(ignore_permissions=True)
		return {
			"success": True,
			"schemes_count": len(schemes),
			"extracted": extracted,
		}
	except Exception:
		doc.status = "Failed"
		doc.extraction_log = frappe.get_traceback()[-2000:]
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.log_error(frappe.get_traceback(), f"Scheme extraction failed: {upload_name}")
		frappe.throw(_("AI extraction failed. Check Extraction Log for details."), title=_("Scheme Document Upload Error"))


@frappe.whitelist()
def create_schemes_from_upload(upload_name, schemes_json=None) -> dict:
	"""Create Supplier Scheme Circular(s) from extracted data.

	If schemes_json is provided, use it (user may have edited).
	Otherwise use the stored extracted_json.
	"""
	doc = frappe.get_doc("Scheme Document Upload", upload_name)

	if schemes_json:
		if isinstance(schemes_json, str):
			data = json.loads(schemes_json)
	else:
		if not doc.extracted_json:
			frappe.throw(_("No extracted data. Please run extraction first."), title=_("Scheme Document Upload Error"))
		data = json.loads(doc.extracted_json)

	schemes = data.get("schemes", [])
	if not schemes:
		frappe.throw(_("No schemes found in extracted data"), title=_("Scheme Document Upload Error"))

	created = []
	for scheme_data in schemes:
		try:
			circular = _create_single_circular(scheme_data, doc)
			created.append(circular.name)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Failed to create scheme: {scheme_data.get('scheme_name', '?')}"
			)
			frappe.msgprint(
				_("Failed to create: {0}. Check Error Log.").format(
					scheme_data.get("scheme_name", "Unknown")
				),
				indicator="orange",
			)

	if created:
		doc.created_schemes = ", ".join(created)
		doc.status = "Schemes Created"
		doc.save(ignore_permissions=True)
	return {
		"created": created,
		"count": len(created),
		"failed": len(schemes) - len(created),
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
			"model": settings.comparison_model or "gpt-4o",
			"timeout": max(settings.timeout_sec or 30, 30),
		}
	except frappe.DoesNotExistError:
		return None


def _read_file_content(file_url):
	"""Read file and return base64 content + mime type for AI vision."""
	import base64
	import mimetypes

	# Get file from Frappe file system
	file_doc = frappe.get_doc("File", {"file_url": file_url})
	file_path = file_doc.get_full_path()

	mime_type = mimetypes.guess_type(file_path)[0] or "application/pdf"

	with open(file_path, "rb") as f:
		raw = f.read()

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
  "terms": ["...", "..."],
  "schemes": [
    {
      "scheme_name": "...",
      "rule_type": "...",
      "payout_basis": "...",
      "achievement_basis": "...",
      "stackable": false,
      "notes": "...",
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
	import requests

	resp = requests.post(
		ai_settings["endpoint"],
		headers={
			"Authorization": f"Bearer {ai_settings['api_key']}",
			"Content-Type": "application/json",
		},
		json={
			"model": ai_settings["model"],
			"messages": messages,
			"max_tokens": 16000,
			"response_format": {"type": "json_object"},
		},
		timeout=ai_settings["timeout"],
	)
	resp.raise_for_status()
	content = resp.json()["choices"][0]["message"]["content"]
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


def _create_single_circular(scheme_data, upload_doc):
	"""Create one Supplier Scheme Circular from parsed scheme data."""
	# Get top-level data from parent document's extracted JSON
	top_data = {}
	if upload_doc.extracted_json:
		top_data = json.loads(upload_doc.extracted_json)

	circular = frappe.new_doc("Supplier Scheme Circular")
	circular.scheme_name = scheme_data.get("scheme_name", "Untitled Scheme")
	circular.circular_number = top_data.get("circular_number", "")
	circular.brand = upload_doc.brand or ""
	circular.supplier = upload_doc.supplier or ""

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
	if terms:
		terms_html = "<h4>Terms & Conditions</h4><ol>"
		for t in terms:
			terms_html += f"<li>{frappe.utils.escape_html(t)}</li>"
		terms_html += "</ol>"
		circular.description = terms_html

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

	circular.insert(ignore_permissions=True)
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
