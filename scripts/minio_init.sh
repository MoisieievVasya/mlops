#!/bin/sh
# Create the three buckets the stack needs, with the AWS CLI rather than `mc`
# (neither official MinIO image can be pulled anonymously any more). Runs on every
# `make up`; creating a bucket that exists is not an error here.
#
#   newsflow-dvc       the DVC remote - the raw archive, addressed by its md5
#   newsflow-mlflow    MLflow artifacts - models, signatures, the per-class report
#   newsflow-features  Feast offline store - the point-in-time parquet
set -eu

ENDPOINT="${MINIO_ENDPOINT:-http://minio:9000}"

for bucket in newsflow-dvc newsflow-mlflow newsflow-features; do
    if aws --endpoint-url "$ENDPOINT" s3 mb "s3://$bucket" 2>/dev/null; then
        echo "created  s3://$bucket"
    else
        echo "exists   s3://$bucket"
    fi
done
