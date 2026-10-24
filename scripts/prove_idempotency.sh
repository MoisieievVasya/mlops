#!/usr/bin/env bash
# Run the SAME data interval twice and print both row counts.
#
# Idempotency is a property you show, not one you claim. This script runs the month
# end to end, records the counts, runs the identical interval again as a separate
# DAG run, and prints the two side by side. If they differ, the load is not
# idempotent and the lab is not done.
#
# Usage: ./scripts/prove_idempotency.sh 2022-03
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f .env ]] && set -a && . ./.env && set +a

MONTH="${1:-2022-03}"
DAG=ingest_month

compose() { docker compose "$@"; }
psql_q() {
  compose exec -T postgres psql -U "${POSTGRES_USER:-newsflow}" -d "${2:-newsflow}" -tAq -c "$1"
}

counts() {
  psql_q "SELECT (SELECT count(*) FROM raw.articles              WHERE batch_month = '${MONTH}-01') || ' | ' ||
                 (SELECT count(*) FROM dwh.articles              WHERE batch_month = '${MONTH}-01') || ' | ' ||
                 (SELECT count(*) FROM features.article_features WHERE batch_month = '${MONTH}-01');"
}

run_once() {
  local label="$1"
  echo "--- ${label}: running ${DAG} for ${MONTH} ---"
  compose exec -T airflow-scheduler airflow backfill create \
      --dag-id "$DAG" --from-date "${MONTH}-01" --to-date "${MONTH}-01" \
      --reprocess-behavior completed >/dev/null 2>&1
  local state=""
  sleep 20
  for _ in $(seq 1 120); do
    sleep 3
    state=$(psql_q "SELECT state FROM dag_run WHERE dag_id='${DAG}' AND logical_date::date='${MONTH}-01' ORDER BY start_date DESC NULLS LAST LIMIT 1;" airflow | tr -d '[:space:]')
    [[ "$state" == "success" || "$state" == "failed" ]] && break
  done
  echo "    run state: ${state:-unknown}"
}

echo "============================================================"
echo " idempotency check - interval ${MONTH}-01 .. next month, run twice"
echo "============================================================"

run_once "first run"
FIRST=$(counts)
echo "    raw | dwh | features  =  ${FIRST}"

run_once "second run, identical interval"
SECOND=$(counts)
echo "    raw | dwh | features  =  ${SECOND}"

echo
echo "first  : ${FIRST}"
echo "second : ${SECOND}"
if [[ "$FIRST" == "$SECOND" ]]; then
  echo "IDENTICAL - the load is idempotent."
else
  echo "DIFFERENT - the load duplicated or lost rows." >&2
  exit 1
fi

echo
echo "== the ledger, one row per run of this month =="
compose exec -T postgres psql -U "${POSTGRES_USER:-newsflow}" -d newsflow -c "
SELECT dag_run_id, status, rows_extracted, rows_loaded, rows_features
  FROM dwh.batch_runs
 WHERE batch_month = '${MONTH}-01'
 ORDER BY task_attempt;"
