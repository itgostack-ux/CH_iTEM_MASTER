"""Index Item.ch_model.

Every model->item rollup in the estate joins `tabItem` on `ch_model`, and that
column had no index: v3_lifecycle_and_indexes indexed ch_category and
ch_sub_category but missed the one below them. With 5,639 CH Models and 14,383
Items that made each rollup a full scan per model, and it showed: the Item
Master Dashboard took 24.3s to answer over HTTP, long enough that the page sat
on "Loading dashboard..." and was reported as broken. The three offenders were
_get_category_summary (10.7s), _get_coverage_data (8.6s) and _get_insights
(5.1s); with the index they are 0.07s, 0.13s and 0.06s, and the endpoint
answers in under a second.

The index list itself lives in v3 so a fresh install gets it in one pass; this
patch exists only to re-run the installer on sites where v3 has already been
recorded as applied. add_index is a no-op when the index exists, so this is
idempotent and safe to re-run.
"""

from ch_item_master.patches.v3_lifecycle_and_indexes import _add_indexes


def execute() -> None:
	_add_indexes()
