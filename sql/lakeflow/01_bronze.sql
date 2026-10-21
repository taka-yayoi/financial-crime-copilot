-- ============================================================================
-- Lakeflow — BRONZE: raw ingest from the landing Volume via Auto Loader.
-- Streaming tables so new files dropped in the Volume are picked up incrementally.
-- ============================================================================

CREATE OR REFRESH STREAMING TABLE aml_bronze.customers
  COMMENT 'Raw customer master ingested from landing volume'
AS SELECT *, _metadata.file_path AS _source_file, current_timestamp() AS _ingested_at
FROM STREAM read_files(
  '${landing_path}/customers/',
  format => 'csv', header => true, inferColumnTypes => true,
  schemaHints => 'customer_id INT, is_pep BOOLEAN'
);

CREATE OR REFRESH STREAMING TABLE aml_bronze.accounts
  COMMENT 'Raw account master ingested from landing volume'
AS SELECT *, _metadata.file_path AS _source_file, current_timestamp() AS _ingested_at
FROM STREAM read_files(
  '${landing_path}/accounts/',
  format => 'csv', header => true, inferColumnTypes => true,
  schemaHints => 'account_id BIGINT, customer_id INT, opening_balance DOUBLE'
);

CREATE OR REFRESH STREAMING TABLE aml_bronze.transactions
  COMMENT 'Raw transactions ingested from landing volume'
AS SELECT *, _metadata.file_path AS _source_file, current_timestamp() AS _ingested_at
FROM STREAM read_files(
  '${landing_path}/transactions/',
  format => 'csv', header => true, inferColumnTypes => true,
  schemaHints => 'account_id BIGINT, amount DOUBLE, txn_ts TIMESTAMP'
);

CREATE OR REFRESH STREAMING TABLE aml_bronze.alerts
  COMMENT 'Raw rule-based TM alerts ingested from landing volume'
AS SELECT *, _metadata.file_path AS _source_file, current_timestamp() AS _ingested_at
FROM STREAM read_files(
  '${landing_path}/alerts/',
  format => 'csv', header => true, inferColumnTypes => true,
  schemaHints => 'alert_id STRING, account_id BIGINT, customer_id INT, trigger_amount DOUBLE, alert_ts TIMESTAMP, is_pep BOOLEAN, is_suspicious BOOLEAN'
);
