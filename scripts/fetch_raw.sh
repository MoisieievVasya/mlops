#!/usr/bin/env bash
# Put the raw archive on disk on a clean clone.
#
# The DVC remote is the primary path: it is the version this repository's commit
# points at. The public mirror is the fallback for someone who has cloned the repo
# but has no credentials for our MinIO, and the checksum below is what tells them
# whether they got the same bytes.
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f .env ]] && set -a && . ./.env && set +a

TARGET="data/raw/news_category_v3.json"
EXPECTED_MD5="bcd4791916daeac3fa5dc76786dd8bd5"
MIRROR="https://huggingface.co/datasets/heegyu/news-category-dataset/resolve/main/data.json"

mkdir -p data/raw

if [[ -f "$TARGET" ]]; then
  echo "already present: $TARGET"
else
  if uv run dvc pull "$TARGET.dvc" 2>/dev/null; then
    echo "pulled from the DVC remote"
  else
    echo "DVC remote unreachable - falling back to the public mirror"
    curl -fsSL -o "$TARGET" "$MIRROR"
  fi
fi

ACTUAL_MD5=$(md5sum "$TARGET" | cut -d' ' -f1)
echo "md5      $ACTUAL_MD5"
echo "expected $EXPECTED_MD5"
if [[ "$ACTUAL_MD5" != "$EXPECTED_MD5" ]]; then
  echo "CHECKSUM MISMATCH - this is not the archive the committed .dvc file names." >&2
  exit 1
fi
echo "rows     $(wc -l < "$TARGET")"
echo "ok"
