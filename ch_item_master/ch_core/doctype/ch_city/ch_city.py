import frappe
from frappe.model.document import Document


def _resolve_state_token(state: str | None) -> str | None:
	"""Return the token ``autoname`` would append for a given state, or None."""
	if not state:
		return None
	state_code = (frappe.db.get_value("CH State", state, "state_code") or "").strip().upper()
	if state_code:
		return state_code
	# Last-resort fallback if legacy states lack state_code.
	return "".join(ch for ch in state.upper() if ch.isalnum()) or None


def _strip_state_suffix(city: str, state_token: str | None) -> str:
	"""Strip a trailing ``-{state_token}`` from ``city`` if present.

	Prevents double-suffixing (``Chennai-33-33``) when a caller feeds the
	autoname PK back into the ``city_name`` field. Safe for genuine
	hyphenated district names (``Janjgir-Champa``, ``Medchal-Malkajgiri``)
	because they do not end with a state code token.
	"""
	if not city or not state_token:
		return city
	suffix = f"-{state_token}"
	if city.upper().endswith(suffix.upper()):
		stripped = city[: -len(suffix)].strip()
		if stripped:
			return stripped
	return city


class CHCity(Document):
	def autoname(self):
		"""Use a state-aware key so duplicate district names across states do not collide.

		Examples:
		- Bilaspur-CG
		- Bilaspur-HP
		
		If state/state_code is missing, fall back to city-only.

		Defensive: if ``city_name`` already ends with ``-{state_token}`` (i.e.
		a caller fed the previously-computed PK back in), strip the suffix
		before re-appending to avoid ``Chennai-33-33``.
		"""
		city = (self.city_name or "").strip().title()
		if not city:
			return

		state_token = _resolve_state_token(self.state)
		city = _strip_state_suffix(city, state_token)

		self.name = f"{city}-{state_token}" if state_token else city

	def validate(self):
		if self.city_name:
			# Normalize casing and strip any accidental state-suffix so the
			# field value never carries the PK form (``Chennai-33``); keeps
			# reports/exports readable and stops re-saves from double-suffixing.
			cleaned = self.city_name.strip().title()
			cleaned = _strip_state_suffix(cleaned, _resolve_state_token(self.state))
			self.city_name = cleaned

		if self.state and self.city_name:
			existing = frappe.db.get_value(
				"CH City",
				{"state": self.state, "city_name": self.city_name, "name": ["!=", self.name]},
				"name",
			)
			if existing:
				frappe.throw(
					frappe._("City {0} already exists in state {1}.").format(
						frappe.bold(self.city_name), frappe.bold(self.state)
					),
					title=frappe._("Duplicate City"),
				)
