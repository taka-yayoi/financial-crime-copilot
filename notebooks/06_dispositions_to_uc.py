# Databricks notebook source
# MAGIC %md
# MAGIC # Dispositions back to Unity Catalog — closing the loop
# MAGIC Analysts record dispositions in the case cockpit; they are written transactionally to
# MAGIC Lakebase (`fcc_app.case_dispositions`). This task appends new dispositions to the Delta
# MAGIC table `aml_gold.case_dispositions` (incremental by `disposition_id`) and maintains the
# MAGIC `aml_gold.case_status` view, so Genie, KPIs and auditors work from governed UC data.

# COMMAND ----------

# MAGIC %pip install "psycopg[binary]>=3.2" "databricks-sdk>=0.81.0" -q
# MAGIC %restart_python

# COMMAND ----------

dbutils.widgets.text("catalog", "serverless_stable_unstructured_catalog")
dbutils.widgets.text("lakebase_endpoint", "projects/fcc-case-queue/branches/production/endpoints/primary")
CATALOG = dbutils.widgets.get("catalog")
ENDPOINT = dbutils.widgets.get("lakebase_endpoint")
GOLD = f"{CATALOG}.aml_gold"

import psycopg
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
host = w.postgres.get_endpoint(name=ENDPOINT).status.hosts.host
token = w.postgres.generate_database_credential(endpoint=ENDPOINT).token

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {GOLD}.case_dispositions (
  disposition_id BIGINT, alert_id STRING, action STRING, analyst STRING, note STRING,
  sar_narrative STRING, created_at TIMESTAMP, _loaded_at TIMESTAMP)
COMMENT 'Analyst dispositions from the Lakebase case cockpit (append-only audit trail)'
""")
last_id = spark.sql(f"SELECT coalesce(max(disposition_id), 0) FROM {GOLD}.case_dispositions").first()[0]

with psycopg.connect(host=host, dbname="databricks_postgres", user=w.current_user.me().user_name,
                     password=token, sslmode="require") as conn:
    rows = conn.execute(
        """SELECT disposition_id, alert_id, action, analyst, note, sar_narrative, created_at
           FROM fcc_app.case_dispositions WHERE disposition_id > %s ORDER BY disposition_id""",
        (last_id,)).fetchall()

if rows:
    from pyspark.sql import functions as F
    df = spark.createDataFrame(rows, "disposition_id BIGINT, alert_id STRING, action STRING, analyst STRING, "
                                     "note STRING, sar_narrative STRING, created_at TIMESTAMP")
    df.withColumn("_loaded_at", F.current_timestamp()).write.mode("append").saveAsTable(f"{GOLD}.case_dispositions")

spark.sql(f"""
CREATE OR REPLACE VIEW {GOLD}.case_status
COMMENT 'Latest analyst disposition per alert (from the Lakebase case cockpit)'
AS SELECT alert_id, action, analyst, note, created_at AS actioned_at,
          sar_narrative IS NOT NULL AS has_filed_narrative
FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY alert_id ORDER BY disposition_id DESC) AS rn
      FROM {GOLD}.case_dispositions)
WHERE rn = 1
""")

# COMMAND ----------

REPORT = ["# Dispositions: Lakebase -> Unity Catalog",
          f"new dispositions loaded this run: {len(rows)} (after disposition_id {last_id})",
          f"total in {GOLD}.case_dispositions: {spark.table(f'{GOLD}.case_dispositions').count()}",
          "", "case_status (latest disposition per alert):"]
for r in spark.sql(f"""SELECT s.alert_id, q.priority_rank, q.priority_band, s.action, s.analyst, s.actioned_at
                      FROM {GOLD}.case_status s JOIN {GOLD}.case_queue q USING (alert_id)
                      ORDER BY q.priority_rank""").collect():
    REPORT.append(f"  #{r.priority_rank:<5} {r.priority_band:<6} {r.action:<21} {r.analyst} {r.actioned_at} {r.alert_id}")
print("\n".join(REPORT))
with open(f"/Volumes/{CATALOG}/aml_gold/artifacts/06_dispositions_to_uc_report.txt", "w") as f:
    f.write("\n".join(REPORT))
