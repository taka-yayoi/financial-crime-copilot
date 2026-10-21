# Decisions & trade-offs

Short rationale for the choices a reviewer (or a skeptical architect) will ask about.

## ML augments the rules — it does not replace them
Regulators trust the deterministic rule set, and removing a rule creates missed-alert
(false-negative) liability. So the ML model is a **prioritization layer on top of** the
existing rules: every alert the rules raise is still there; the model only decides
**what order** analysts work them in. No alert is auto-closed by the model. This keeps the
regulatory story defensible while capturing most of the efficiency.

**Trade-off:** we do not get the full cost saving of auto-closing low-risk alerts, but we
avoid the far larger risk of the model silently suppressing a true positive.

## SAR drafting is human-in-the-loop
GenAI drafts the SAR narrative from structured case facts; an analyst reviews and files it.
We never auto-file. LLMs can hallucinate, and a filed SAR is a legal document.

**Trade-off:** analysts still spend ~20 min reviewing each SAR instead of zero, but the
draft removes the blank-page cost and enforces a consistent structure.

## Explainability is a first-class requirement
Every prioritization score ships with its **top-3 SHAP feature attributions**, shown on the
case screen, so an analyst (and an auditor) can see *why* a case ranked high. We prefer interpretable, documented features
(structuring counts, high-risk-country exposure, velocity, PEP flag) over opaque ones.

**Trade-off:** we may leave some predictive lift on the table by avoiding black-box
features, in exchange for audit defensibility.

## The case queue lives in Lakebase (OLTP), not the warehouse
Analysts read the prioritized queue and **write back dispositions** (escalated, SAR filed,
closed as false positive) with low latency and many concurrent users. That is a transactional
workload, not an analytical scan, so Lakebase (Postgres) is the right home. Gold analytics
tables stay in the lakehouse; the operational slice (`gold.case_queue`) is copied to Lakebase as
a **synced table**, refreshed as the last task of every scoring run.

**Trade-off:** two stores. We keep one source of truth per fact: scores and drafts are owned by
Unity Catalog (Lakebase holds a read-only copy), dispositions are owned by Lakebase (the app is
the only writer).

## Dispositions flow back to Unity Catalog
A disposition born in Lakebase is useless to Genie, KPIs and auditors unless it reaches the
lakehouse. A scheduled job (`fcc_dispositions_to_uc`, every 15 min) appends new dispositions to
the Delta table `gold.case_dispositions` (incremental by `disposition_id`) and maintains
`gold.case_status`. The Postgres table has `REPLICA IDENTITY FULL` so it is ready for Lakehouse
Sync (Lakebase CDC to Delta), which was not enabled on the build workspace.

**Trade-off:** up to 15 minutes before Genie sees a new disposition; the app itself always reads
Lakebase, so analysts see their own changes immediately.

## Analysts and the LLM never see the ground truth
The synthetic data carries a ground-truth label and typology so the model can be trained and
evaluated. Those columns are excluded from `gold.case_queue` and from the SAR prompt; drafts are
written only from facts an analyst would have (rule, transaction features, SHAP reasons).
Ground truth is used for evaluation only (e.g. 103 of the 121 drafted cases are truly suspicious).

## Data is synthetic by design
Real customer financial data can never leave the customer and must not appear in a
submission. The generator injects known laundering typologies (structuring, rapid movement,
high-risk-country wires, PEP activity) into a small set of bad actors, so we have
**ground-truth labels** to train and evaluate against — something real unlabeled alert data
rarely provides cleanly.

**Trade-off:** synthetic data is simpler than production; the point is to demonstrate the
integrated journey and the modeling approach, not to ship a production model.

## Foundation Model APIs over a bespoke fine-tune
SAR drafting and rationale use Databricks Foundation Model APIs (`ai_query`) rather than a
fine-tuned model. Faster to value, no training data assembly, and quality is sufficient for
a drafting aid.

**Trade-off:** less domain specialization than a fine-tune, acceptable for a
human-reviewed draft.
