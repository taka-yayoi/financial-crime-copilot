# Databricks notebook source
# MAGIC %md
# MAGIC # Case queue — the operational slice served to Lakebase
# MAGIC Joins the ML priority score + SHAP reasons, the GenAI SAR draft and the governed
# MAGIC (PII-masked) customer view into one row per alert. This Delta table is the source of
# MAGIC the Lakebase synced table the analyst app reads. The synthetic ground-truth columns are
# MAGIC deliberately excluded: an analyst never sees them.

# COMMAND ----------

dbutils.widgets.text("catalog", "serverless_stable_unstructured_catalog")
CATALOG = dbutils.widgets.get("catalog")
GOLD = f"{CATALOG}.aml_gold"

spark.sql(f"""
CREATE OR REPLACE TABLE {GOLD}.case_queue
COMMENT 'One row per alert, prioritised by the ML model, with SAR draft; synced to Lakebase'
TBLPROPERTIES (delta.enableChangeDataFeed = true)
AS SELECT
  s.alert_id,
  s.priority_rank,
  s.priority_band,
  round(s.priority_score, 4)         AS priority_score,
  s.top_reasons,
  s.rule_code,
  s.alert_ts,
  round(s.trigger_amount, 2)         AS trigger_amount,
  s.account_id,
  s.customer_id,
  c.full_name                        AS customer_name,
  c.country                          AS customer_country,
  c.kyc_risk_rating,
  c.is_pep,
  d.analyst_rationale,
  d.sar_draft
FROM {GOLD}.alert_scores s
LEFT JOIN {GOLD}.customers_governed c USING (customer_id)
LEFT JOIN {GOLD}.alert_sar_drafts d USING (alert_id)
""")

# COMMAND ----------

REPORT = ["# Case queue (source of the Lakebase synced table)"]
for r in spark.sql(f"""
    SELECT priority_band, COUNT(*) AS alerts, SUM(CASE WHEN sar_draft IS NOT NULL THEN 1 ELSE 0 END) AS with_sar_draft
    FROM {GOLD}.case_queue GROUP BY priority_band ORDER BY priority_band""").collect():
    REPORT.append(f"  {r.priority_band:<7} alerts={r.alerts:>5}  with_sar_draft={r.with_sar_draft}")
REPORT.append("\nTop 5 of the queue:")
for r in spark.sql(f"""
    SELECT priority_rank, alert_id, rule_code, priority_score, top_reasons
    FROM {GOLD}.case_queue ORDER BY priority_rank LIMIT 5""").collect():
    REPORT.append(f"  #{r.priority_rank} {r.alert_id} {r.rule_code} score={r.priority_score} | {r.top_reasons}")
print("\n".join(REPORT))
with open(f"/Volumes/{CATALOG}/aml_gold/artifacts/05_case_queue_report.txt", "w") as f:
    f.write("\n".join(REPORT))
