# Execution evidence

Everything below was produced by running this repo on a Databricks workspace (AWS, serverless
compute, Unity Catalog catalog `serverless_stable_unstructured_catalog`) on 2026-10-21.
Files are plain-text output: job run states, notebook reports, query results, psql output,
HTTP responses from the deployed app, and Genie answers with the SQL Genie generated.

| File | Layer | What it shows |
|---|---|---|
| [00_end_to_end_job.txt](00_end_to_end_job.txt) | orchestration | One run of job `fcc_end_to_end`: all 6 tasks SUCCESS, ingest → Lakebase sync |
| [01_data_generation.txt](01_data_generation.txt) | data | Synthetic population: 2,417 rule alerts, 95.1% false positives, per-rule precision |
| [02_lakeflow.txt](02_lakeflow.txt) | Lakeflow | Pipeline update COMPLETED, row counts bronze → silver → gold, expectation pass/fail counts |
| [03_unity_catalog.txt](03_unity_catalog.txt) | Unity Catalog | Column mask returning `XXX-XX-XXXX`, masked email view, PII tags, grants |
| [04_ml_alert_scoring.txt](04_ml_alert_scoring.txt) | ML | ROC-AUC / PR-AUC, recall at top-k% of the queue, UC model `@champion`, SHAP attributions |
| [05_genai_sar.txt](05_genai_sar.txt) | Gen AI | 121 SAR drafts via `ai_query`; three full drafts with analyst rationale |
| [06_case_queue.txt](06_case_queue.txt) | serving | `gold.case_queue` by band; top cases with per-alert SHAP reasons |
| [07_lakebase.txt](07_lakebase.txt) | Lakebase | Synced table ONLINE + last sync; psql on the synced queue and the dispositions table |
| [08_app.txt](08_app.txt) | App | Deployed app API: dispositions written, KPIs before/after, queue, case detail, app log |
| [09_genie.txt](09_genie.txt) | Genie Room | Four questions asked through the app: answers, generated SQL, result rows |
| [10_dispositions_to_uc.txt](10_dispositions_to_uc.txt) | Lakebase → UC | Dispositions landed in `gold.case_dispositions`; `gold.case_status` |

How the stages connect, end to end:
`01` raw CSVs → `02` pipeline tables → `03` governed silver/gold → `04` scores + SHAP reasons →
`05` SAR drafts → `06` case queue → `07` Lakebase synced table → `08` app reads it and writes
dispositions → `10` dispositions back in UC → `09` Genie answers from the same tables.
