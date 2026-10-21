#!/usr/bin/env bash
# Lakebase (operational serving) for the Financial Crime Copilot.
#
#   UC  gold.case_queue  ──synced table──►  Lakebase aml_serving.case_queue   (app reads)
#   Lakebase fcc_app.case_dispositions  ──job fcc_dispositions_to_uc──►  UC aml_gold.case_dispositions
#
# The case queue is analytical output, so Unity Catalog stays the source of truth and Lakebase
# holds a read-only synced copy. Dispositions are born in Lakebase (transactional writes from
# the app); notebooks/06_dispositions_to_uc.py lands them back in UC for Genie, KPIs and audit.
#
# DABs cannot declare Lakebase projects / synced tables yet, so this script uses the CLI.
# Usage: PROFILE=<cli-profile> CATALOG=<uc-catalog> ./lakebase/setup_lakebase.sh [project|sync|grant]
set -euo pipefail
PROFILE=${PROFILE:?set PROFILE}
CATALOG=${CATALOG:-serverless_stable_unstructured_catalog}
PROJECT=${PROJECT:-fcc-case-queue}
BRANCH=projects/$PROJECT/branches/production

STEP=${1:-all}

if [[ $STEP == project || $STEP == all ]]; then
  # Lakebase Autoscaling project (Postgres 17); auto-creates the production branch + endpoint.
  databricks postgres get-project projects/$PROJECT --profile "$PROFILE" >/dev/null 2>&1 ||
      databricks postgres create-project "$PROJECT" \
        --json '{"spec": {"display_name": "Financial Crime Copilot case queue"}}' --profile "$PROFILE"
fi

if [[ $STEP == sync || $STEP == all ]]; then
  databricks experimental aitools tools query "CREATE SCHEMA IF NOT EXISTS $CATALOG.aml_serving" --profile "$PROFILE"
  # UC -> Lakebase: the ML-prioritised case queue. SNAPSHOT mode: the end-to-end job rebuilds
  # gold.case_queue each scoring run and then refreshes this sync (its last task).
  databricks postgres create-synced-table "$CATALOG.aml_serving.case_queue" --json "{
      \"spec\": {
        \"source_table_full_name\": \"$CATALOG.aml_gold.case_queue\",
        \"primary_key_columns\": [\"alert_id\"],
        \"scheduling_policy\": \"SNAPSHOT\",
        \"branch\": \"$BRANCH\",
        \"postgres_database\": \"databricks_postgres\",
        \"create_database_objects_if_missing\": true,
        \"new_pipeline_spec\": {\"storage_catalog\": \"$CATALOG\", \"storage_schema\": \"aml_serving\"}
      }}" --profile "$PROFILE"
fi

if [[ $STEP == grant ]]; then
  # After `databricks bundle run fcc_cockpit`: let the app's service principal read the synced
  # case queue. (The app creates and owns its own schema fcc_app.)
  SP=$(databricks apps get fcc-case-cockpit --profile "$PROFILE" -o json |
       python3 -c "import json,sys; print(json.load(sys.stdin)['service_principal_client_id'])")
  databricks psql --project "$PROJECT" --profile "$PROFILE" -- -c "
    GRANT USAGE ON SCHEMA aml_serving TO \"$SP\";
    GRANT SELECT ON ALL TABLES IN SCHEMA aml_serving TO \"$SP\";
    ALTER DEFAULT PRIVILEGES IN SCHEMA aml_serving GRANT SELECT ON TABLES TO \"$SP\";"
  databricks experimental aitools tools query "GRANT USE CATALOG ON CATALOG $CATALOG TO \`$SP\`" --profile "$PROFILE"
fi
