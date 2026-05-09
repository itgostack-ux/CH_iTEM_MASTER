# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
v10: Oracle Procurement Excellence — Vendor Performance Scoring + Contract-Integrated Sourcing

Creates:
  - tabCH Vendor Performance  (standalone doctype)
  - tabCH Vendor Contract     (child table, linked from CH Vendor Info Record)

Safe to re-run: uses CREATE TABLE IF NOT EXISTS and conditional ALTER TABLE.
"""

import frappe


def execute():
	# ── 1. CH Vendor Performance ───────────────────────────────────────────────
	if not frappe.db.table_exists("CH Vendor Performance"):
		frappe.db.sql("""
			CREATE TABLE `tabCH Vendor Performance` (
				`name`                VARCHAR(140) NOT NULL PRIMARY KEY,
				`creation`            DATETIME,
				`modified`            DATETIME,
				`modified_by`         VARCHAR(140),
				`owner`               VARCHAR(140),
				`docstatus`           INT(1) DEFAULT 0,
				`idx`                 INT(8) DEFAULT 0,
				`item_code`           VARCHAR(140),
				`supplier`            VARCHAR(140),
				`evaluation_date`     DATE,
				`evaluation_period`   VARCHAR(140),
				`evaluator`           VARCHAR(140),
				`otif_pct`            DECIMAL(18,4) DEFAULT 0,
				`on_time_delivery_pct` DECIMAL(18,4) DEFAULT 0,
				`quality_score`       DECIMAL(18,4) DEFAULT 0,
				`defect_rate`         DECIMAL(18,4) DEFAULT 0,
				`risk_level`          VARCHAR(20),
				`auto_block`          INT(1) DEFAULT 0,
				`block_reason`        TEXT,
				INDEX `idx_vp_item_supplier` (`item_code`, `supplier`),
				INDEX `idx_vp_risk_level` (`risk_level`),
				INDEX `idx_vp_eval_date` (`evaluation_date`)
			) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
		""")
		frappe.db.commit()

	# ── 2. CH Vendor Contract (child table) ────────────────────────────────────
	if not frappe.db.table_exists("CH Vendor Contract"):
		frappe.db.sql("""
			CREATE TABLE `tabCH Vendor Contract` (
				`name`           VARCHAR(140) NOT NULL PRIMARY KEY,
				`creation`       DATETIME,
				`modified`       DATETIME,
				`modified_by`    VARCHAR(140),
				`owner`          VARCHAR(140),
				`docstatus`      INT(1) DEFAULT 0,
				`idx`            INT(8) DEFAULT 0,
				`parent`         VARCHAR(140),
				`parentfield`    VARCHAR(140),
				`parenttype`     VARCHAR(140),
				`contract_no`    VARCHAR(140),
				`contract_type`  VARCHAR(20),
				`valid_from`     DATE,
				`valid_to`       DATE,
				`contract_price` DECIMAL(18,4) DEFAULT 0,
				`currency`       VARCHAR(10),
				`notes`          TEXT,
				INDEX `idx_vc_parent` (`parent`),
				INDEX `idx_vc_valid` (`valid_from`, `valid_to`)
			) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
		""")
		frappe.db.commit()
