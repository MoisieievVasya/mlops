-- One PostgreSQL 16 instance, three databases with three different jobs.
--   newsflow  the warehouse: raw, dwh and features schemas (design 6.1, layer 2-3)
--   mlflow    the tracking server's backend store (design 6.1, layer 5)
--   airflow   the orchestrator's own metadata database
-- Only `newsflow` is a project artifact; the other two are infrastructure state.
CREATE DATABASE mlflow;
CREATE DATABASE airflow;
CREATE DATABASE feast;
