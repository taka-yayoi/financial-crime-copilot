# Databricks notebook source
# MAGIC %md
# MAGIC # Gen AI — SAR narrative drafting + analyst rationale
# MAGIC Uses Databricks Foundation Model APIs via `ai_query` (Claude Opus 4.8).
# MAGIC Runs on every HIGH-priority alert from the ML layer, using only facts an analyst would
# MAGIC have (rule, features, SHAP reasons) — never the synthetic ground-truth label.
# MAGIC Human-in-the-loop: these are DRAFTS shown in the case cockpit, never auto-filed.

# COMMAND ----------

dbutils.widgets.text("catalog", "serverless_stable_unstructured_catalog")
dbutils.widgets.text("llm_endpoint", "databricks-claude-opus-4-8")
CATALOG = dbutils.widgets.get("catalog")
LLM = dbutils.widgets.get("llm_endpoint")
GOLD = f"{CATALOG}.aml_gold"

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE TABLE {GOLD}.alert_sar_drafts
COMMENT 'GenAI SAR narrative drafts + one-line rationale for HIGH-priority alerts (human review required)'
AS
WITH cases AS (
  SELECT
    s.alert_id, s.customer_id, s.account_id, s.rule_code, s.priority_score, s.top_reasons,
    s.trigger_amount, s.is_pep, s.kyc_risk_rating,
    f.high_risk_txn_count, f.high_risk_txn_amount, f.cash_txn_count,
    f.cash_credit_amount, f.distinct_cp_countries, f.txn_count, f.max_amount
  FROM {GOLD}.alert_scores s
  JOIN {GOLD}.alert_features f USING (alert_id)
  WHERE s.priority_band = 'HIGH'
),
prompted AS (
  SELECT *,
    concat(
      'You are an AML analyst assistant at a commercial bank. Draft a concise, ',
      'regulator-style Suspicious Activity Report (SAR) narrative from the case facts. ',
      'Use these sections: 1) Subject, 2) Suspicious activity summary, 3) Transaction detail, ',
      '4) Basis for suspicion, 5) Recommended action. Keep it under 220 words, factual, ',
      'use only the facts given, and mark it DRAFT FOR ANALYST REVIEW. ',
      'CASE FACTS: customer_id=', customer_id, '; account_id=', account_id,
      '; triggering_rule=', rule_code,
      '; model_priority_score=', round(priority_score, 3),
      '; top_model_risk_drivers=', coalesce(top_reasons, 'n/a'),
      '; is_politically_exposed_person=', is_pep, '; kyc_risk_rating=', kyc_risk_rating,
      '; triggering_amount_usd=', round(trigger_amount, 2),
      '; high_risk_country_txns=', high_risk_txn_count,
      '; high_risk_country_amount_usd=', round(high_risk_txn_amount, 2),
      '; cash_txns=', cash_txn_count, '; cash_credit_usd=', round(cash_credit_amount, 2),
      '; distinct_counterparty_countries=', distinct_cp_countries,
      '; total_txns=', txn_count, '; largest_txn_usd=', round(max_amount, 2), '.'
    ) AS sar_prompt,
    concat(
      'In one sentence, explain to a busy AML analyst why this alert scored high risk. ',
      'Use only these facts: rule=', rule_code,
      ', model risk drivers=', coalesce(top_reasons, 'n/a'),
      ', high_risk_country_amount_usd=', round(high_risk_txn_amount, 2),
      ', cash_credit_usd=', round(cash_credit_amount, 2),
      ', distinct_counterparty_countries=', distinct_cp_countries, '.'
    ) AS rationale_prompt
  FROM cases
)
SELECT
  alert_id, customer_id, rule_code, round(priority_score, 3) AS priority_score,
  ai_query('{LLM}', rationale_prompt) AS analyst_rationale,
  ai_query('{LLM}', sar_prompt)       AS sar_draft,
  '{LLM}'                             AS llm_endpoint,
  current_timestamp()                 AS drafted_at
FROM prompted
""")

# COMMAND ----------

REPORT = [f"# Gen AI — SAR drafts + analyst rationale ({LLM} via ai_query)",
          "# Human-in-the-loop drafts for HIGH-priority alerts. Not auto-filed.", ""]
n = spark.table(f"{GOLD}.alert_sar_drafts").count()
REPORT.append(f"drafts generated: {n}")
# Evaluation against the synthetic ground truth (kept out of the prompt): how many of the
# drafted cases are genuinely suspicious?
prec = spark.sql(f"""
  SELECT COUNT(*) AS drafted, SUM(CASE WHEN s.label_is_suspicious THEN 1 ELSE 0 END) AS truly_suspicious
  FROM {GOLD}.alert_sar_drafts d JOIN {GOLD}.alert_scores s USING (alert_id)""").first()
REPORT.append(f"drafted cases that are truly suspicious (ground truth): "
              f"{prec.truly_suspicious}/{prec.drafted}")
for i, r in enumerate(spark.sql(f"""
    SELECT * FROM {GOLD}.alert_sar_drafts ORDER BY priority_score DESC LIMIT 3""").collect(), 1):
    REPORT += ["", "=" * 78,
               f"CASE {i}  alert_id={r.alert_id}  rule={r.rule_code}  score={r.priority_score}",
               "-" * 78, "ANALYST RATIONALE:", r.analyst_rationale, "", "SAR DRAFT:", r.sar_draft]
print("\n".join(REPORT))
with open(f"/Volumes/{CATALOG}/aml_gold/artifacts/04_genai_sar_report.txt", "w") as f:
    f.write("\n".join(REPORT))
