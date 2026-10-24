"""PostgreSQL access for the three layers.

Two things are deliberate here:

* every write is scoped to one `batch_month` and is **delete-then-insert inside one
  transaction**, which is what makes a second run of the same interval a no-op rather
  than a duplicate (design: idempotency; Appendix 2's `load` question);
* no module outside this one writes SQL against the warehouse, so "what can touch the
  warehouse" is one file long.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Sequence
from datetime import date
from typing import Any

import psycopg2
import psycopg2.extras

from newsflow.config import DSN, SQL_DIR


@contextlib.contextmanager
def connect(dsn: str | None = None) -> Iterator[psycopg2.extensions.connection]:
    """A connection that commits on a clean exit and rolls back on any exception."""
    conn = psycopg2.connect(dsn or DSN)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_schema(dsn: str | None = None) -> None:
    """Apply sql/001_schema.sql. Idempotent; run on every `make up`."""
    script = (SQL_DIR / "001_schema.sql").read_text(encoding="utf-8")
    with connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(script)


def scalar(sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> Any:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row[0] if row else None


def fetch_all(sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> list[tuple]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_dicts(sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> list[dict]:
    with connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def replace_partition(
    table: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    batch_month: date,
    *,
    month_column: str = "batch_month",
) -> int:
    """Replace one month's rows in `table` with `rows`, atomically.

    This is the idempotency statement the README points at. The DELETE and the INSERT
    share one transaction, so a reader never sees the month half-written and a rerun
    of the same interval converges on exactly the same rows — including the case
    where the rerun produces *fewer* rows than the first run, which an upsert without
    a delete would silently get wrong by leaving the surplus behind.
    """
    placeholders = ", ".join(columns)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE {month_column} = %s", (batch_month,))  # noqa: S608
        if rows:
            psycopg2.extras.execute_values(
                cur,
                f"INSERT INTO {table} ({placeholders}) VALUES %s",  # noqa: S608
                rows,
                page_size=1000,
            )
    return len(rows)


def delete_partition(table: str, batch_month: date, *, month_column: str = "batch_month") -> int:
    """Remove one month. Used by the quality gate to reject a batch it has failed."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE {month_column} = %s", (batch_month,))  # noqa: S608
        return cur.rowcount


def record_run(record: dict[str, Any]) -> None:
    """Append one row to the ledger. Never updates.

    Airflow reuses a backfill's run_id when it reprocesses a date, so an upsert keyed
    on the run id would overwrite the first execution with the second — destroying
    the only evidence that the two produced the same counts.
    """
    columns = list(record)
    placeholders = ", ".join(["%s"] * len(columns))
    sql = f"INSERT INTO dwh.batch_runs ({', '.join(columns)}) VALUES ({placeholders})"  # noqa: S608
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, [record[c] for c in columns])
