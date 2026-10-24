# Apache Airflow 3.3.1 — the orchestrator named in design 6.2.
# The only thing added to the official image is the handful of packages the
# `newsflow` package imports; the package itself is mounted, not copied, so a code
# change does not require a rebuild.
FROM apache/airflow:3.3.1

COPY requirements.txt /tmp/newsflow-requirements.txt
RUN pip install --no-cache-dir -r /tmp/newsflow-requirements.txt
