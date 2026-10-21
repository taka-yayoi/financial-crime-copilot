# Databricks notebook source
# MAGIC %md
# MAGIC # Unity Catalog governance — Financial Crime Copilot
# MAGIC Runs after the Lakeflow pipeline. Demonstrates PII column masking (dynamic, by group),
# MAGIC PII classification tags, a governed serving view, and least-privilege grants.
# MAGIC Lineage bronze -> silver -> gold is captured automatically by Unity Catalog.

# COMMAND ----------

dbutils.widgets.text("catalog", "serverless_stable_unstructured_catalog")
CATALOG = dbutils.widgets.get("catalog")
BRONZE, SILVER, GOLD = f"{CATALOG}.aml_bronze", f"{CATALOG}.aml_silver", f"{CATALOG}.aml_gold"
REPORT = []
def log(*a):
    line = " ".join(str(x) for x in a); print(line); REPORT.append(line)
def show(title, sql):
    log(f"\n## {title}\n-- {sql.strip()}")
    rows = spark.sql(sql).collect()
    for r in rows:
        log("  " + str(r.asDict()))
    return rows

# COMMAND ----------

# 1. Column-mask function: members of the financial-crime analyst group see the real
#    national ID; everyone else sees a redacted value.
spark.sql(f"""
CREATE OR REPLACE FUNCTION {GOLD}.mask_national_id(val STRING)
RETURNS STRING
COMMENT 'Reveals national_id only to financial_crime_analysts'
RETURN CASE WHEN is_account_group_member('financial_crime_analysts') THEN val
            ELSE 'XXX-XX-XXXX' END
""")

# 2. Apply the mask to the sensitive column (canonical UC column mask).
spark.sql(f"ALTER TABLE {SILVER}.customers ALTER COLUMN national_id SET MASK {GOLD}.mask_national_id")

# 3. Classify PII columns with governance tags for discovery and policy.
#    Both keys are governed tag policies in this metastore:
#    pii (ssn, address) and data_sensitivity (official, official_sensitive).
spark.sql(f"ALTER TABLE {SILVER}.customers ALTER COLUMN national_id SET TAGS ('pii' = 'ssn')")
spark.sql(f"ALTER TABLE {SILVER}.customers ALTER COLUMN email SET TAGS ('data_sensitivity' = 'official_sensitive')")
spark.sql(f"ALTER TABLE {SILVER}.customers ALTER COLUMN full_name SET TAGS ('data_sensitivity' = 'official_sensitive')")

# 4. Governed serving view that the case queue, Genie and the app read customer data from.
spark.sql(f"""
CREATE OR REPLACE VIEW {GOLD}.customers_governed
COMMENT 'Customer view with PII masked for non-privileged roles'
AS SELECT
     customer_id,
     full_name,
     national_id,   -- already masked by the column mask
     CASE WHEN is_account_group_member('financial_crime_analysts') THEN email
          ELSE regexp_replace(email, '(^.).*(@.*$)', '$1***$2') END AS email,
     country, occupation, kyc_risk_rating, is_pep, account_open_date
FROM {SILVER}.customers
""")

# 5. Least-privilege grants: consumers read the gold layer only; bronze/silver stay restricted.
spark.sql(f"GRANT USE SCHEMA, SELECT ON SCHEMA {GOLD} TO `account users`")

# COMMAND ----------

log("# Unity Catalog governance — evidence")
log(f"current_user = {spark.sql('SELECT current_user()').first()[0]}")
show("national_id column mask active for current (non-analyst) user",
     f"SELECT customer_id, national_id FROM {SILVER}.customers ORDER BY customer_id LIMIT 3")
show("governed view masks email",
     f"SELECT customer_id, email, national_id FROM {GOLD}.customers_governed ORDER BY customer_id LIMIT 3")
show("PII tags on silver.customers",
     f"""SELECT column_name, tag_name, tag_value FROM {CATALOG}.information_schema.column_tags
         WHERE schema_name = 'aml_silver' AND table_name = 'customers' ORDER BY column_name""")
show("column masks",
     f"""SELECT table_name, column_name, mask_name FROM {CATALOG}.information_schema.column_masks
         WHERE table_schema = 'aml_silver'""")
show("grants on gold schema",
     f"""SELECT grantee, privilege_type FROM {CATALOG}.information_schema.schema_privileges
         WHERE schema_name = 'aml_gold' ORDER BY grantee, privilege_type""")

# COMMAND ----------

path = f"/Volumes/{CATALOG}/aml_gold/artifacts/02_unity_catalog_report.txt"
with open(path, "w") as f:
    f.write("\n".join(REPORT))
print("report written:", path)
