"""SUPERSEDED — see patches/v31_merge_vas_plan_into_ch_warranty_plan.py

The original body of this patch backfilled VAS Plan / VAS Product /
VAS Claim from CH Warranty Plan / CH Warranty Claim. Those doctypes
no longer exist — they were merged back into CH Warranty Plan by
v31.

Kept as a no-op so servers whose `Installed Patch` log has this
module remain idempotent. If your server never ran the original,
patches.txt already skips this entry.
"""


def execute() -> None:
    return
