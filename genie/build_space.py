#!/usr/bin/env python3
"""Build the Genie Room definition (serialized_space v2) for the Financial Crime Copilot.

Pure standard library. Writes genie/space.json, which genie/deploy_space.sh passes to
`databricks genie create-space` / `update-space`.
Usage: python3 genie/build_space.py <catalog> [--without-status]
  --without-status: first deploy, before Lakehouse Sync has created the case_status source.
"""
import json
import os
import sys

args = [a for a in sys.argv[1:] if not a.startswith("--")]
CATALOG = args[0] if args else "serverless_stable_unstructured_catalog"
WITH_STATUS = "--without-status" not in sys.argv
G = f"{CATALOG}.aml_gold"


def hid(n):  # stable 32-hex ids so re-deploys produce identical payloads
    return f"{n:032x}"


tables = [
    {"identifier": f"{G}.alert_kpis",
     "description": ["Daily alert volume by transaction-monitoring rule, with historically confirmed "
                     "suspicious cases and the false-positive rate."]},
    {"identifier": f"{G}.case_queue",
     "description": ["One row per rule-based alert, ranked by the ML alert-priority model "
                     "(priority_rank 1 = work first), with SHAP risk drivers and the GenAI SAR draft."],
     "column_configs": [
         {"column_name": "priority_band", "enable_format_assistance": True, "enable_entity_matching": True,
          "synonyms": ["risk band", "priority"]},
         {"column_name": "rule_code", "enable_format_assistance": True, "enable_entity_matching": True,
          "synonyms": ["rule", "scenario", "TM rule"]},
         {"column_name": "sar_draft", "exclude": True},
     ]},
    {"identifier": f"{G}.case_status",
     "description": ["Latest analyst disposition per alert, written in the case cockpit (Lakebase) and "
                     "streamed back to Unity Catalog. Alerts with no row here have not been worked yet."],
     "column_configs": [
         {"column_name": "action", "enable_format_assistance": True, "enable_entity_matching": True,
          "synonyms": ["disposition", "status", "outcome"]},
     ]},
    {"identifier": f"{G}.customers_governed",
     "description": ["Customer master with PII masked by Unity Catalog for non-privileged users."]},
]
if not WITH_STATUS:
    tables = [t for t in tables if not t["identifier"].endswith(".case_status")]
for t in tables:
    if "column_configs" in t:
        t["column_configs"].sort(key=lambda c: c["column_name"])
tables.sort(key=lambda t: t["identifier"])

instructions = [
    "This room supports AML investigators and the Head of Financial Crime at a commercial bank.",
    "Alerts come from a legacy rules-based transaction-monitoring system; most are false positives.",
    "priority_score is the model's probability that an alert is genuinely suspicious; priority_band "
    "is HIGH (>0.6), MEDIUM (0.3-0.6) or LOW (<0.3). priority_rank 1 is the most urgent case.",
    "Analyst work status comes from case_status.action: ESCALATE, SAR_FILED or CLOSE_FALSE_POSITIVE. "
    "An alert is 'open' when it has no case_status row or its action is ESCALATE.",
    "False-positive rate = 1 - suspicious / alerts, from alert_kpis. Report percentages to one decimal.",
    "Never show national IDs or full email addresses; use customers_governed for customer attributes.",
]

examples = [
    ("Which rules generate the most false positives?",
     f"SELECT rule_code, SUM(alerts) AS alerts, SUM(suspicious) AS suspicious, "
     f"ROUND(100.0 * (1 - SUM(suspicious) / SUM(alerts)), 1) AS false_positive_pct "
     f"FROM {G}.alert_kpis GROUP BY rule_code ORDER BY alerts - suspicious DESC"),
] + ([] if not WITH_STATUS else [
    ("How many high-priority alerts are still open?",
     f"SELECT COUNT(*) AS open_high_priority FROM {G}.case_queue q "
     f"LEFT JOIN {G}.case_status s ON q.alert_id = s.alert_id "
     f"WHERE q.priority_band = 'HIGH' AND (s.action IS NULL OR s.action = 'ESCALATE')"),
    ("Show analyst dispositions by outcome",
     f"SELECT action, COUNT(*) AS cases FROM {G}.case_status GROUP BY action ORDER BY cases DESC"),
])

space = {
    "version": 2,
    "config": {"sample_questions": sorted([
        {"id": hid(0x100 + i), "question": [q]} for i, q in enumerate([
            "Which rules generate the most false positives?",
            "How many high-priority alerts are still open?",
            "Show the top 10 open cases for PEP customers",
            "How many SARs have analysts filed so far, and for which rules?",
        ])], key=lambda x: x["id"])},
    "data_sources": {"tables": tables},
    "instructions": {
        "text_instructions": [{"id": hid(0x300), "content": [line + "\n" for line in instructions]}],
        "example_question_sqls": sorted([
            {"id": hid(0x200 + i), "question": [q], "sql": [s]} for i, (q, s) in enumerate(examples)],
            key=lambda x: x["id"]),
    },
}

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "space.json")
with open(out, "w") as f:
    json.dump(space, f, indent=2)
print(out)
