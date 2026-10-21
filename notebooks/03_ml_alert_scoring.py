# Databricks notebook source
# MAGIC %md
# MAGIC # Alert-priority model — Financial Crime Copilot
# MAGIC Trains a model that ranks each rule-based alert by probability of being genuinely
# MAGIC suspicious, so analysts work the top of the queue and the ~95% false positives sink.
# MAGIC Logs to MLflow, registers to Unity Catalog, computes SHAP explanations, and writes a
# MAGIC scored table for operational serving. All metrics are printed and also written to a
# MAGIC text report under the artifacts volume as committed execution evidence.

# COMMAND ----------

# MAGIC %pip install mlflow scikit-learn "shap>=0.48" -q
# MAGIC %restart_python

# COMMAND ----------

import json
import numpy as np
import pandas as pd
import mlflow
from mlflow.models.signature import infer_signature
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, average_precision_score, confusion_matrix, classification_report

dbutils.widgets.text("catalog", "serverless_stable_unstructured_catalog")
CATALOG, SCHEMA = dbutils.widgets.get("catalog"), "aml_gold"
MODEL_NAME = f"{CATALOG}.{SCHEMA}.alert_priority_model"
REPORT = []
def log(*a):
    line = " ".join(str(x) for x in a); print(line); REPORT.append(line)

# COMMAND ----------

# Load the feature table produced by the Lakeflow gold layer.
pdf = spark.table(f"{CATALOG}.{SCHEMA}.alert_features").toPandas()
log("=== Alert-priority model ===")
log(f"alerts (rows)      : {len(pdf):,}")
log(f"suspicious (label) : {int(pdf.label_is_suspicious.sum()):,} "
    f"({100*pdf.label_is_suspicious.mean():.1f}%)  -- legacy false-positive rate "
    f"{100*(1-pdf.label_is_suspicious.mean()):.1f}%")

NUMERIC = ["trigger_amount","account_age_days","txn_count","active_days","total_credit",
           "total_debit","avg_amount","max_amount","stddev_amount","distinct_cp_countries",
           "high_risk_txn_count","high_risk_txn_amount","cash_txn_count","cash_credit_amount",
           "wire_txn_count"]
CATEG = ["rule_code","kyc_risk_rating","account_type","customer_country","occupation"]
pdf["is_pep_int"] = pdf["is_pep"].astype(int)
NUMERIC_ALL = NUMERIC + ["is_pep_int"]
for c in NUMERIC_ALL:
    pdf[c] = pd.to_numeric(pdf[c], errors="coerce").fillna(0)
for c in CATEG:
    pdf[c] = pdf[c].fillna("UNK").astype(str)

X = pdf[NUMERIC_ALL + CATEG]
y = pdf["label_is_suspicious"].astype(int)
X_tr, X_te, y_tr, y_te, idx_tr, idx_te = train_test_split(
    X, y, pdf.index, test_size=0.25, random_state=42, stratify=y)
log(f"train / test rows  : {len(X_tr):,} / {len(X_te):,}   "
    f"(test suspicious: {int(y_te.sum())})")

# COMMAND ----------

pre = ColumnTransformer([
    ("num", "passthrough", NUMERIC_ALL),
    ("cat", OneHotEncoder(handle_unknown="ignore"), CATEG),
])
clf = RandomForestClassifier(n_estimators=400, max_depth=8, min_samples_leaf=5,
                             class_weight="balanced_subsample", random_state=42, n_jobs=-1)
model = Pipeline([("pre", pre), ("clf", clf)])

mlflow.set_registry_uri("databricks-uc")
mlflow.sklearn.autolog(disable=True)
with mlflow.start_run(run_name="alert_priority_rf") as run:
    model.fit(X_tr, y_tr)
    proba = model.predict_proba(X_te)[:, 1]

    auc = roc_auc_score(y_te, proba)
    ap = average_precision_score(y_te, proba)
    log("\n=== Model quality (test set) ===")
    log(f"ROC-AUC            : {auc:.3f}")
    log(f"PR-AUC (avg prec)  : {ap:.3f}")

    # Operational metric: analysts work the queue top-down. How much review workload is
    # eliminated while still catching the true suspicious cases?
    order = np.argsort(-proba)
    y_sorted = y_te.values[order]
    total_susp = y_sorted.sum()
    log("\n=== Workload reduction (rank alerts by model score, review top-down) ===")
    log(f"{'review top %':>12} | {'alerts reviewed':>15} | {'suspicious caught':>17} | {'recall':>7}")
    for pct in [0.10, 0.20, 0.30, 0.50]:
        k = max(1, int(len(y_sorted) * pct))
        caught = y_sorted[:k].sum()
        log(f"{int(pct*100):>11}% | {k:>15,} | {caught:>17,} | {100*caught/total_susp:>6.1f}%")
    # headline: recall achieved after reviewing only 20% of alerts
    k20 = max(1, int(len(y_sorted) * 0.20))
    recall20 = y_sorted[:k20].sum() / total_susp
    log(f"\nReviewing only the top 20% of alerts catches {100*recall20:.0f}% of all suspicious "
        f"cases -> ~80% of triage workload can be deprioritized.")

    mlflow.log_metrics({"roc_auc": auc, "pr_auc": ap, "recall_at_20pct": float(recall20)})

    sig = infer_signature(X_tr, model.predict_proba(X_tr)[:, 1])
    mlflow.sklearn.log_model(model, artifact_path="model", signature=sig,
                             input_example=X_tr.head(3), registered_model_name=MODEL_NAME)
    run_id = run.info.run_id
log(f"\nMLflow run_id      : {run_id}")

# COMMAND ----------

# Promote to @champion alias in Unity Catalog
from mlflow.tracking import MlflowClient
c = MlflowClient(registry_uri="databricks-uc")
mv = max(c.search_model_versions(f"name='{MODEL_NAME}'"), key=lambda m: int(m.version))
c.set_registered_model_alias(MODEL_NAME, "champion", mv.version)
log(f"Registered UC model: {MODEL_NAME}  version={mv.version}  alias=@champion")

# COMMAND ----------

# SHAP explanations — feature attributions for auditability
log("\n=== Top feature attributions (SHAP, mean |value|) ===")
import shap
log(f"shap version       : {shap.__version__}")
Xt_te = model.named_steps["pre"].transform(X_te)
feat_names = (NUMERIC_ALL +
              list(model.named_steps["pre"].named_transformers_["cat"]
                   .get_feature_names_out(CATEG)))
expl = shap.TreeExplainer(model.named_steps["clf"])
def positive_class(sv):
    return sv[1] if isinstance(sv, list) else (sv[:, :, 1] if sv.ndim == 3 else sv)
imp = np.abs(positive_class(expl.shap_values(Xt_te))).mean(axis=0)
for name, v in sorted(zip(feat_names, imp), key=lambda t: -t[1])[:12]:
    log(f"  {name:<32} {v:.4f}")
# Per-alert explanation: the 3 features pushing each alert's score up the most.
# Shipped with every score so the analyst (and an auditor) sees why a case ranked high.
sv_all = positive_class(expl.shap_values(model.named_steps["pre"].transform(X)))
pdf["top_reasons"] = [
    "; ".join(f"{feat_names[j]} (+{row[j]:.2f})" for j in np.argsort(-row)[:3] if row[j] > 0)
    for row in sv_all]

# COMMAND ----------

# Score ALL alerts and write the operational table the case queue is built from.
pdf["priority_score"] = model.predict_proba(X)[:, 1]
pdf["priority_rank"] = pdf["priority_score"].rank(ascending=False, method="first").astype(int)
pdf["priority_band"] = pd.cut(pdf["priority_score"], [-0.01, 0.3, 0.6, 1.01],
                              labels=["LOW", "MEDIUM", "HIGH"])
scored = pdf[["alert_id","account_id","customer_id","rule_code","alert_ts","trigger_amount",
              "is_pep","kyc_risk_rating","true_typology","label_is_suspicious",
              "priority_score","priority_rank","priority_band","top_reasons"]].copy()
scored["priority_band"] = scored["priority_band"].astype(str)
(spark.createDataFrame(scored)
      .write.mode("overwrite").option("overwriteSchema","true")
      .saveAsTable(f"{CATALOG}.{SCHEMA}.alert_scores"))
log(f"\nWrote scored table : {CATALOG}.{SCHEMA}.alert_scores ({len(scored):,} rows)")
log("Priority band counts:")
for band, n in scored["priority_band"].value_counts().items():
    log(f"  {band:<7} {n:,}")

# COMMAND ----------

# Persist the text report as committed execution evidence.
report_path = f"/Volumes/{CATALOG}/{SCHEMA}/artifacts/03_ml_alert_scoring_report.txt"
with open(report_path, "w") as f:
    f.write("\n".join(REPORT))
print("report written:", report_path)
dbutils.notebook.exit(json.dumps({"run_id": run_id, "roc_auc": round(auc,3),
                                  "pr_auc": round(ap,3), "recall_at_20pct": round(float(recall20),3),
                                  "model_version": mv.version}))
