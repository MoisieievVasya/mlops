"""Task 4 — load: write the month into `dwh.articles`, idempotently.

This is the task Appendix 2 asks about. The statement that makes a second run of the
same interval harmless is in `newsflow.warehouse.replace_partition`:

    DELETE FROM dwh.articles WHERE batch_month = %s;
    INSERT INTO dwh.articles (...) VALUES %s;      -- same transaction

Delete-then-insert rather than an upsert, because an upsert is only idempotent when
the rerun produces a superset of the first run's rows. If a batch shrinks — a source
correction, a cleaning rule that now drops more — an upsert leaves the surplus rows
behind and the month quietly contains a mixture of two runs. The delete makes the
partition equal to the batch, not merely consistent with it.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from newsflow.etl.transform import CLEAN_COLUMNS, clean_rows
from newsflow.warehouse import replace_partition, scalar


def run(batch_month: date) -> dict[str, Any]:
    rows = clean_rows(batch_month)
    replace_partition("dwh.articles", CLEAN_COLUMNS, rows, batch_month)

    stored = scalar("SELECT count(*) FROM dwh.articles WHERE batch_month = %s", (batch_month,))
    return {"rows_loaded": int(stored or 0)}
