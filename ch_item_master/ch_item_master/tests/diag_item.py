"""Diagnostic: fetch sub-category specs and model spec values."""
import frappe, json

def run():
    run2()


def run2():
    # QA Phone specs
    specs = frappe.get_all(
        "CH Sub Category Spec",
        filters={"parent": "QA Smartphones-QA Phone"},
        fields=["spec", "is_variant"],
        order_by="idx asc",
    )
    print("QA Phone sub-category specs:")
    for s in specs:
        print(f"  spec={s.spec}  is_variant={s.is_variant}")

    # iPhone 13 model spec values
    msv = frappe.get_all(
        "CH Model Spec Value",
        filters={"parent": "QA Smartphones-QA Phone-Apple-QA iPhone 13"},
        fields=["spec", "spec_value"],
        order_by="idx asc",
    )
    print("\niPhone 13 model spec values:")
    for m in msv:
        print(f"  spec={m.spec}  value={m.spec_value}")

    # Accessible Item Attributes
    attrs = frappe.get_all("Item Attribute", pluck="name", limit=20)
    print("\nAvailable Item Attributes:", attrs)

    # HSN on all sub-categories
    subs = frappe.get_all("CH Sub Category", fields=["name", "hsn_code", "prefix"], filters={"prefix": ["!=", ""]})
    print("\nSub-category HSN codes:")
    for s in subs:
        print(f"  {s.name:40s}  prefix={s.prefix:5s}  hsn={s.hsn_code}")

    # Models for Smart Phone-PHONE
    ph_models = frappe.get_all("CH Model", filters={"sub_category": "Smart Phone-PHONE"}, fields=["name", "manufacturer", "brand"], limit=10)
    print("\nSmart Phone-PHONE models:")
    for m in ph_models:
        existing_tpl = frappe.db.get_value("Item", {"ch_model": m.name, "has_variants": 1}, "name")
        msv_count = frappe.db.count("CH Model Spec Value", {"parent": m.name})
        print(f"  {m.name:60s}  template={existing_tpl}  spec_values={msv_count}")

    # Smart Phone-PHONE sub-category specs
    ph_specs = frappe.get_all("CH Sub Category Spec", filters={"parent": "Smart Phone-PHONE"}, fields=["spec", "is_variant"], order_by="idx asc")
    print("\nSmart Phone-PHONE specs:")
    for s in ph_specs:
        print(f"  spec={s.spec}  is_variant={s.is_variant}")

    # Valid HSN codes in the master (first 10)
    hsn_codes = frappe.get_all("GST HSN Code", fields=["name", "hsn_code", "description"], limit=10)
    print("\nSample GST HSN Codes in master:")
    for h in hsn_codes:
        print(f"  hsn_code={h.hsn_code}  name={h.name}  desc={h.description[:40] if h.description else ''}")

    # CH Model Galaxy S25 spec values
    s25_specs = frappe.get_all("CH Model Spec Value", filters={"parent": "Galaxy S25"}, fields=["spec", "spec_value"], order_by="idx asc", limit=20)
    print("\nGalaxy S25 spec values:")
    for s in s25_specs:
        print(f"  spec={s.spec}  value={s.spec_value}")
