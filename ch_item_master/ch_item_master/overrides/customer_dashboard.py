from frappe import _


def get_data(data):
    data["non_standard_fieldnames"].update({
        "CH Voucher": "issued_to",
    })

    data["transactions"].extend([
        {
            "label": _("Warranty & VAS"),
            "items": ["CH Sold Plan", "CH Warranty Claim"],
        },
        {
            "label": _("Devices"),
            "items": ["CH Customer Device", "CH Serial Lifecycle"],
        },
        {
            "label": _("Vouchers & Loyalty"),
            "items": ["CH Voucher", "CH Loyalty Transaction"],
        },
        {
            "label": _("Exceptions"),
            "items": ["CH Exception Request"],
        },
    ])

    return data
