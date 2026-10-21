-- ============================================================================
-- Lakeflow — SILVER: cleansed & conformed, with data-quality expectations.
-- Streaming reads from bronze so the flow stays incremental end-to-end.
-- ============================================================================

CREATE OR REFRESH STREAMING TABLE aml_silver.customers (
  CONSTRAINT valid_customer EXPECT (customer_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_risk EXPECT (kyc_risk_rating IN ('LOW','MEDIUM','HIGH'))
) COMMENT 'Cleansed customers'
AS SELECT
     customer_id, full_name, CAST(date_of_birth AS DATE) AS date_of_birth,
     national_id, email, country, occupation, kyc_risk_rating, is_pep,
     CAST(account_open_date AS DATE) AS account_open_date
FROM STREAM aml_bronze.customers;

CREATE OR REFRESH STREAMING TABLE aml_silver.accounts (
  CONSTRAINT valid_account EXPECT (account_id IS NOT NULL AND customer_id IS NOT NULL) ON VIOLATION DROP ROW
) COMMENT 'Cleansed accounts'
AS SELECT account_id, customer_id, account_type,
          CAST(open_date AS DATE) AS open_date, opening_balance
FROM STREAM aml_bronze.accounts;

CREATE OR REFRESH STREAMING TABLE aml_silver.transactions (
  CONSTRAINT positive_amount EXPECT (amount > 0) ON VIOLATION DROP ROW,
  CONSTRAINT valid_direction EXPECT (direction IN ('credit','debit')),
  CONSTRAINT valid_ts EXPECT (txn_ts IS NOT NULL) ON VIOLATION DROP ROW
) COMMENT 'Cleansed transactions'
AS SELECT txn_id, account_id, txn_ts, amount, direction, channel,
          upper(counterparty_country) AS counterparty_country
FROM STREAM aml_bronze.transactions;

CREATE OR REFRESH STREAMING TABLE aml_silver.alerts (
  CONSTRAINT valid_alert EXPECT (alert_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT positive_trigger EXPECT (trigger_amount >= 0)
) COMMENT 'Cleansed rule-based alerts'
AS SELECT alert_id, account_id, customer_id, rule_code, alert_ts, trigger_amount,
          is_pep, is_suspicious, true_typology
FROM STREAM aml_bronze.alerts;
