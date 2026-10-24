"""`ingest_month` — one calendar month of the archive, per run.

    extract -> validate -> transform -> load -> build_features -> quality_check

**The batch is the data interval, never the clock.** Every task below receives
`data_interval_start` / `data_interval_end` from the run and filters the archive on
the article's own `date` field. No task derives its window from the clock, which is
what makes a backfill of 2015-03 produce the rows of March 2015 rather than the rows
of whatever month it is when the backfill is launched. The single `datetime.now()`
below stamps the run ledger with which attempt wrote the row — a record about the
execution, never an input to what it selects.

**Why `CronDataIntervalTimetable` and not `schedule="@monthly"`.** Airflow 3 changed
the default: a plain cron schedule produces runs where `data_interval_start` and
`data_interval_end` are the same instant, so "the interval this run owns" would be
empty and every task would have to invent a window. `CronDataIntervalTimetable` keeps
the 2.x semantics — a run covers the period *preceding* its trigger — so the run
triggered on 2015-04-01 owns [2015-03-01, 2015-04-01). That is the window the tasks
filter on, and the README says so.

**Breaking it on purpose.** Trigger with `{"fault": "unmapped_label"}` (or any id in
conf/quality_contract.yaml:faults) to corrupt the batch between the archive and the
raw layer and watch `quality_check` reject the month.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

# The project is mounted at /opt/project and is not pip-installed into the image, so
# that a code change does not need a rebuild.
sys.path.insert(0, "/opt/project/src")

import pendulum  # noqa: E402
from airflow.sdk import Param, dag, get_current_context, task  # noqa: E402
from airflow.timetables.interval import CronDataIntervalTimetable  # noqa: E402

from newsflow.etl import extract, features, load, quality, transform, validate  # noqa: E402
from newsflow.warehouse import record_run  # noqa: E402

DOC = __doc__


#: Airflow Variable that lets a fault be injected into a *re-run* of an existing
#: interval. A backfill reuses the run id for a logical date and keeps that run's
#: original conf, so `--dag-run-conf '{"fault": ...}'` is silently ignored when the
#: date has been processed before — which is exactly the case the demo needs. The
#: Variable is read at task time, so it applies to a re-run. Params still win when
#: they are set, so a first-time trigger can carry the fault in its conf.
FAULT_VARIABLE = "newsflow_fault"


def _fault() -> str:
    """Which fault to inject, from the run's conf or the override Variable."""
    from airflow.sdk import Variable

    fault = get_current_context()["params"].get("fault", "none")
    if fault and fault != "none":
        return str(fault)
    return str(Variable.get(FAULT_VARIABLE, default="none"))


def _interval() -> tuple[datetime, datetime, datetime]:
    """(batch_month, interval_start, interval_end) for the run that is executing."""
    context = get_current_context()
    start = context["data_interval_start"]
    end = context["data_interval_end"]
    if start == end:  # a defensive check, not a fallback: see the module docstring
        raise RuntimeError(
            "data_interval_start == data_interval_end. The DAG is not using "
            "CronDataIntervalTimetable and the batch would not be a month."
        )
    return start.date().replace(day=1), start.date(), end.date()


@dag(
    dag_id="ingest_month",
    description="One calendar month of the archive: extract, validate, transform, load, gate",
    doc_md=DOC,
    schedule=CronDataIntervalTimetable("@monthly", timezone="UTC"),
    start_date=pendulum.datetime(2012, 1, 1, tz="UTC"),
    end_date=pendulum.datetime(2022, 10, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 0},
    tags=["newsflow", "lab-2", "etl"],
    params={
        "fault": Param(
            "none",
            type="string",
            enum=["none", "unmapped_label", "null_headline", "duplicate_rows", "truncate_batch"],
            title="Inject a fault",
            description="Corrupt this batch on purpose to show the quality gate rejecting it.",
        )
    },
)
def ingest_month() -> None:
    @task
    def extract_task() -> dict:
        month, start, end = _interval()
        result = extract.run(month, start, end, fault=_fault())
        print(f"extract {month:%Y-%m}: {result}")
        return result

    @task
    def validate_task(upstream: dict) -> dict:
        month, start, end = _interval()
        result = validate.run(month, start, end)
        print(f"validate {month:%Y-%m}: {result}")
        return {**upstream, **result}

    @task
    def transform_task(upstream: dict) -> dict:
        month, _, _ = _interval()
        result = transform.run(month)
        print(f"transform {month:%Y-%m}: {result}")
        return {**upstream, **result}

    @task
    def load_task(upstream: dict) -> dict:
        month, _, _ = _interval()
        result = load.run(month)
        print(f"load {month:%Y-%m}: {result}")
        return {**upstream, **result}

    @task
    def build_features_task(upstream: dict) -> dict:
        month, _, _ = _interval()
        result = features.run(month)
        print(f"build_features {month:%Y-%m}: {result}")
        return {**upstream, **result}

    @task
    def quality_check_task(upstream: dict) -> dict:
        context = get_current_context()
        month, start, end = _interval()
        ledger = {
            "batch_month": month,
            "dag_run_id": context["dag_run"].run_id,
            # The only clock read in this DAG, and it is a record of the execution,
            # never an input to it: which attempt wrote this ledger row. Nothing
            # downstream branches on it.
            "task_attempt": datetime.now(UTC),
            "data_interval_start": start,
            "data_interval_end": end,
            "rows_extracted": upstream.get("rows_extracted"),
            "rows_loaded": upstream.get("rows_loaded"),
            "rows_features": upstream.get("rows_features"),
            "rows_dropped_dup": upstream.get("rows_dropped_dup"),
            "rows_dropped_empty": upstream.get("rows_dropped_empty"),
            "data_version": upstream.get("data_version"),
            "git_commit": upstream.get("git_commit"),
            "feature_version": upstream.get("feature_version"),
            "fault_injected": upstream.get("fault_injected"),
        }
        try:
            result = quality.run(month, int(upstream["rows_extracted"]))
        except quality.QualityGateError as failure:
            # The ledger records the rejection *after* the rows have been rolled
            # back, so the warehouse and the ledger never disagree.
            record_run({**ledger, "status": "rejected", "detail": _detail(str(failure))})
            raise
        record_run({**ledger, "status": result["gate"], "detail": _detail(result)})
        print(f"quality_check {month:%Y-%m}: {result}")
        return result

    quality_check_task(
        build_features_task(load_task(transform_task(validate_task(extract_task()))))
    )


def _detail(payload: object) -> str:
    import json

    return json.dumps(payload if isinstance(payload, dict) else {"message": str(payload)})


ingest_month()
