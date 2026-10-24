#!/usr/bin/env bash
# Row counts per layer for one month, plus that month's run ledger.
# Usage: ./scripts/counts.sh 2022-03
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f .env ]] && set -a && . ./.env && set +a

MONTH="${1:-2022-03}"
PSQL=(docker compose exec -T postgres psql -U "${POSTGRES_USER:-newsflow}" -d newsflow -v ON_ERROR_STOP=1)

echo "== row counts per layer for ${MONTH} =="
"${PSQL[@]}" -c "
SELECT 'raw.articles'              AS layer, count(*) AS rows FROM raw.articles              WHERE batch_month = '${MONTH}-01'
UNION ALL
SELECT 'dwh.articles',                       count(*)        FROM dwh.articles              WHERE batch_month = '${MONTH}-01'
UNION ALL
SELECT 'features.article_features',          count(*)        FROM features.article_features WHERE batch_month = '${MONTH}-01';"

echo "== run ledger for ${MONTH} =="
"${PSQL[@]}" -c "
SELECT dag_run_id, status, rows_extracted, rows_loaded, rows_features,
       rows_dropped_dup AS dup, rows_dropped_empty AS empty,
       coalesce(fault_injected, '-') AS fault, left(data_version, 12) AS data_version
  FROM dwh.batch_runs
 WHERE batch_month = '${MONTH}-01'
 ORDER BY task_attempt;"
