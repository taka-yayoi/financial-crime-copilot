#!/usr/bin/env bash
# Create (or update) the Genie Room from genie/space.json.
# Usage: PROFILE=<cli-profile> WAREHOUSE_ID=<id> CATALOG=<uc-catalog> [SPACE_ID=<id>] ./genie/deploy_space.sh [--without-status]
set -euo pipefail
PROFILE=${PROFILE:?set PROFILE}
WAREHOUSE_ID=${WAREHOUSE_ID:?set WAREHOUSE_ID}
CATALOG=${CATALOG:-serverless_stable_unstructured_catalog}
DIR=$(cd "$(dirname "$0")" && pwd)
python3 "$DIR/build_space.py" "$CATALOG" "$@" >/dev/null
BODY=$(python3 -c "import json,sys; print(json.dumps({
  'warehouse_id': sys.argv[1],
  'title': 'Financial Crime Copilot — AML investigations',
  'description': 'Ask about AML alerts, the ML-prioritised case queue, analyst dispositions and false-positive rates.',
  'serialized_space': open(sys.argv[2]).read()}))" "$WAREHOUSE_ID" "$DIR/space.json")
if [[ -n "${SPACE_ID:-}" ]]; then
  databricks genie update-space "$SPACE_ID" --json "$BODY" --profile "$PROFILE" -o json
else
  databricks genie create-space --json "$BODY" --profile "$PROFILE" -o json
fi
