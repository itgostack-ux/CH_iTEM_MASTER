"""Bench-CLI entry points for ch_item_master.

Registered via ``ch_item_master.commands`` → picked up automatically
by ``frappe.utils.bench_helper.get_app_commands`` (see frappe's own
bench_helper.py — the loader imports ``<app>.commands`` and reads the
``commands`` list).

Currently exposes:

* ``bench --site X ch-locations-export --out <path> [--company <c>]``
* ``bench --site X ch-locations-import --in <path> [--apply]
    [--company-map <json>]``

The import CLI defaults to dry-run — no writes happen unless
``--apply`` is passed.  This matches SAP LSMW / Oracle FBDI's
"validate-then-post" workflow: you always inspect the plan before
mutating the target site.
"""

from __future__ import annotations

import json

import click
from frappe.commands import get_site, pass_context


@click.command("ch-locations-export")
@click.option("--out", "out_path", required=True, help="File to write the JSON dump to.")
@click.option("--company", default=None, help="Restrict Zones + Stores to this company (States + Cities are always full).")
@pass_context
def ch_locations_export(context, out_path: str, company: str | None):
    """Export the retail geography (State → City → Zone → Store) as JSON."""
    import frappe

    site = get_site(context)
    frappe.init(site=site)
    frappe.connect()
    try:
        from ch_item_master.ch_core.location_hierarchy_seed import export_to_file

        result = export_to_file(out_path=out_path, company=company)
        click.echo(json.dumps(result, indent=2, default=str))
    finally:
        frappe.destroy()


@click.command("ch-locations-import")
@click.option("--in", "in_path", required=True, help="Path to JSON dump produced by ch-locations-export.")
@click.option("--apply", is_flag=True, default=False, help="Actually write to the site.  Default is DRY-RUN.")
@click.option(
    "--company-map",
    "company_map_json",
    default=None,
    help='JSON string mapping source company abbr → target company name, e.g. \'{"GSPL":"GoFix South Pvt Ltd"}\'.',
)
@pass_context
def ch_locations_import(context, in_path: str, apply: bool, company_map_json: str | None):
    """Import a location-hierarchy JSON.  DRY-RUN by default — use --apply to write."""
    import frappe

    site = get_site(context)
    frappe.init(site=site)
    frappe.connect()
    try:
        from ch_item_master.ch_core.location_hierarchy_seed import import_from_file

        result = import_from_file(
            in_path=in_path,
            apply=apply,
            company_map_json=company_map_json,
        )
        click.echo(json.dumps(result, indent=2, default=str))
        if apply:
            click.secho(
                f"APPLIED: {result['summary']['to_create']} created, "
                f"{result['summary']['skipped']} skipped, "
                f"{result['summary']['errors']} errors.",
                fg="green" if result["summary"]["errors"] == 0 else "red",
            )
        else:
            click.secho(
                f"DRY-RUN: would create {result['summary']['to_create']}, "
                f"skip {result['summary']['skipped']}, "
                f"needs {result['summary']['manual_followups']} manual follow-ups. "
                f"Re-run with --apply to write.",
                fg="yellow",
            )
    finally:
        frappe.destroy()


commands = [
    ch_locations_export,
    ch_locations_import,
]
