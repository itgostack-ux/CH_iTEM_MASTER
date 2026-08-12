# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""Competitor buyback price intelligence.

Three moving parts:

* ``adapters``  — turn one competitor page into one price observation
* ``collector`` — decide what to fetch next, fetch it politely, log a snapshot
* ``rollup``    — reduce many snapshots into one band per item

The collector never sets a buy price. It produces evidence; the Buyback Price
Planner turns evidence into a proposal, and the existing CH Price Upload Batch
maker/checker turns a proposal into a price.
"""
