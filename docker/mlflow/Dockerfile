# MLflow 3.16.1 tracking server + model registry (design 6.2).
# The official image carries neither a PostgreSQL driver nor an S3 client, and this
# server is explicitly *not* file-backed: backend store on PostgreSQL 16, artifact
# store on the MinIO-compatible object store.
FROM ghcr.io/mlflow/mlflow:v3.16.1

RUN pip install --no-cache-dir psycopg2-binary==2.9.10 boto3==1.40.49
