-- ============================================================================
-- Lakeflow — GOLD: analytics + the ML feature table.
-- Materialized views (batch aggregation/join over the full silver dataset).
-- gold.alert_features is the training/serving table for the alert-priority model.
-- ============================================================================

-- Per-account transaction behaviour (batch aggregation over silver.transactions)
CREATE OR REFRESH MATERIALIZED VIEW aml_gold.account_txn_features
  COMMENT 'Per-account transaction behaviour features'
AS SELECT
     account_id,
     COUNT(*)                                                    AS txn_count,
     COUNT(DISTINCT to_date(txn_ts))                             AS active_days,
     SUM(CASE WHEN direction = 'credit' THEN amount ELSE 0 END)  AS total_credit,
     SUM(CASE WHEN direction = 'debit'  THEN amount ELSE 0 END)  AS total_debit,
     ROUND(AVG(amount), 2)                                       AS avg_amount,
     MAX(amount)                                                 AS max_amount,
     ROUND(COALESCE(STDDEV(amount), 0), 2)                       AS stddev_amount,
     COUNT(DISTINCT counterparty_country)                        AS distinct_cp_countries,
     SUM(CASE WHEN counterparty_country IN ('IR','KP','SY','MM','AF','YE') THEN 1 ELSE 0 END) AS high_risk_txn_count,
     ROUND(SUM(CASE WHEN counterparty_country IN ('IR','KP','SY','MM','AF','YE') THEN amount ELSE 0 END), 2) AS high_risk_txn_amount,
     SUM(CASE WHEN channel = 'cash' THEN 1 ELSE 0 END)           AS cash_txn_count,
     ROUND(SUM(CASE WHEN channel = 'cash' AND direction = 'credit' THEN amount ELSE 0 END), 2) AS cash_credit_amount,
     SUM(CASE WHEN channel = 'wire' THEN 1 ELSE 0 END)           AS wire_txn_count
FROM aml_silver.transactions
GROUP BY account_id;

-- The feature table: one row per alert, ready for ML training and scoring.
CREATE OR REFRESH MATERIALIZED VIEW aml_gold.alert_features
  COMMENT 'One row per alert with customer/account/transaction features and the ground-truth label'
AS SELECT
     a.alert_id,
     a.account_id,
     a.customer_id,
     a.rule_code,
     a.alert_ts,
     a.trigger_amount,
     a.is_pep,
     c.kyc_risk_rating,
     c.country                                        AS customer_country,
     c.occupation,
     ac.account_type,
     datediff(a.alert_ts, ac.open_date)               AS account_age_days,
     f.txn_count,
     f.active_days,
     f.total_credit,
     f.total_debit,
     f.avg_amount,
     f.max_amount,
     f.stddev_amount,
     f.distinct_cp_countries,
     f.high_risk_txn_count,
     f.high_risk_txn_amount,
     f.cash_txn_count,
     f.cash_credit_amount,
     f.wire_txn_count,
     a.true_typology,
     a.is_suspicious                                  AS label_is_suspicious
FROM aml_silver.alerts a
LEFT JOIN aml_silver.customers c ON a.customer_id = c.customer_id
LEFT JOIN aml_silver.accounts  ac ON a.account_id = ac.account_id
LEFT JOIN aml_gold.account_txn_features f ON a.account_id = f.account_id;

-- A business-facing daily KPI view for the manager dashboard / Genie.
CREATE OR REFRESH MATERIALIZED VIEW aml_gold.alert_kpis
  COMMENT 'Daily alert KPIs by rule for the manager dashboard'
AS SELECT
     to_date(alert_ts)                                       AS alert_date,
     rule_code,
     COUNT(*)                                                AS alerts,
     SUM(CASE WHEN is_suspicious THEN 1 ELSE 0 END)          AS suspicious,
     ROUND(100.0 * (1 - SUM(CASE WHEN is_suspicious THEN 1 ELSE 0 END) / COUNT(*)), 1) AS false_positive_pct
FROM aml_silver.alerts
GROUP BY to_date(alert_ts), rule_code;
