#!/usr/bin/env bash
# Break one batch on purpose and watch the quality gate reject it.
#
# The gate must fail for real, so the fault is injected into the data between the
# archive and the raw layer - not simulated by flipping a flag the gate reads. The
# faults are declared in conf/quality_contract.yaml:
#
#   unmapped_label   one row's category becomes CRYPTO, absent from taxonomy.yaml -> Q4
#   null_headline    5% of the batch loses headline and dek                        -> Q7
#   duplicate_rows   the first 10 rows are delivered twice                         -> Q5
#   truncate_batch   the month arrives with 10 rows                                -> Q8
#
# Usage: ./scripts/break_a_batch.sh 2022-03 unmapped_label
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f .env ]] && set -a && . ./.env && set +a

MONTH="${1:-2022-03}"
FAULT="${2:-unmapped_label}"
DAG=ingest_month

run_state() {
  docker compose exec -T postgres psql -U "${POSTGRES_USER:-newsflow}" -d airflow -tAc \
    "SELECT state FROM dag_run WHERE dag_id='ingest_month' AND logical_date::date='${MONTH}-01' ORDER BY start_date DESC NULLS LAST LIMIT 1;" \
    | tr -d '[:space:]'
}

echo "============================================================"
echo " feeding ${DAG} a batch broken on purpose"
echo "   month: ${MONTH}    fault: ${FAULT}"
echo "============================================================"

# The fault goes through an Airflow Variable rather than --dag-run-conf: a backfill
# reuses the run id for a logical date and keeps that run's original conf, so a conf
# passed to a re-run of an already-processed month is silently ignored. The Variable
# is read at task time and is removed again at the end of this script.
cleanup() {
  docker compose exec -T airflow-scheduler airflow variables delete newsflow_fault >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker compose exec -T airflow-scheduler airflow variables set newsflow_fault "$FAULT" >/dev/null 2>&1
# Deliberately no --dag-run-conf anywhere in this script. A conf written onto a
# DagRun persists across every later re-run of that logical date, so a fault passed
# that way would quietly poison the month for good; the Variable above is removed on
# exit, which is the property that makes this demo repeatable.
docker compose exec -T airflow-scheduler airflow dags clear "$DAG" \
    --run-id "backfill__$(date -d "${MONTH}-01 +1 month" +%Y-%m)-01T00:00:00+00:00" -y >/dev/null 2>&1 \
  || docker compose exec -T airflow-scheduler airflow backfill create \
       --dag-id "$DAG" --from-date "${MONTH}-01" --to-date "${MONTH}-01" \
       --reprocess-behavior completed >/dev/null 2>&1

sleep 20
state=""
for _ in $(seq 1 120); do
  sleep 3
  state=$(run_state)
  [[ "$state" == "success" || "$state" == "failed" ]] && break
done

echo
echo "run state: ${state:-unknown}"
echo
echo "== why the gate rejected it =="
# The task log is JSON; pull out the gate's own message and unescape its newlines.
RUN_DIR="/opt/airflow/logs/dag_id=${DAG}/run_id=backfill__$(date -d "${MONTH}-01 +1 month" +%Y-%m)-01T00:00:00+00:00"
docker compose exec -T airflow-scheduler sh -c \
  "grep -rhao 'QUALITY GATE FAILED[^\"]*' '${RUN_DIR}' 2>/dev/null | tail -1" \
  | sed 's/\\n/\n/g' || true

echo
echo "== the warehouse after the rejection =="
./scripts/counts.sh "$MONTH"

if [[ "$state" == "failed" ]]; then
  echo
  echo "The run FAILED, which is the expected outcome: the gate did its job."
  echo "Re-run './scripts/prove_idempotency.sh ${MONTH}' to put the month back."
else
  echo
  echo "The run did not fail. The gate missed the fault - that is a defect." >&2
  exit 1
fi
