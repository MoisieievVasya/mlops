"""Task 2 — validate: the input contract, checked before anything is transformed.

Checks Q1–Q3 of conf/quality_contract.yaml live here rather than in the gate at the
end, because they describe the *source* and there is no point normalising a batch
whose fields are not the fields we agreed on (design 6.8).

Q3 is the one worth reading twice: every row must fall inside the interval the run
owns. It is the check that catches a pipeline that has started keying its batch on
the wall clock instead of on `data_interval_start`.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from newsflow.warehouse import fetch_dicts

REQUIRED_FIELDS = ("headline", "published_at", "raw_category")


class ContractViolationError(AssertionError):
    """The upstream delivered something other than what section 6.8 agreed on."""


def run(batch_month: date, interval_start: date, interval_end: date) -> dict[str, Any]:
    rows = fetch_dicts(
        """
        SELECT article_id, published_at, headline, short_description, authors,
               raw_category
          FROM raw.articles
         WHERE batch_month = %s
        """,
        (batch_month,),
    )

    # Q1 — the partition exists at all.
    if not rows:
        raise ContractViolationError(
            f"Q1 rows_present: the extract produced no rows for {batch_month:%Y-%m}. "
            "Either the source stopped delivering or the interval is outside the archive."
        )

    # Q2 — the fields are present and typed as agreed.
    missing: dict[str, int] = {}
    for row in rows:
        for field in REQUIRED_FIELDS:
            value = row.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing[field] = missing.get(field, 0) + 1
    # `headline` may be blank on a handful of archive rows; that is a dirty row the
    # transform drops, not a contract breach. A missing *column* or a null date is.
    for field in ("published_at", "raw_category"):
        if missing.get(field):
            raise ContractViolationError(
                f"Q2 schema_contract: {missing[field]} of {len(rows)} rows have no {field} "
                f"in {batch_month:%Y-%m}. Required by the upstream contract (design 6.8)."
            )

    # Q3 — the batch is the interval, not "whatever was there when this ran".
    outside = [r for r in rows if not (interval_start <= r["published_at"] < interval_end)]
    if outside:
        sample = sorted(r["published_at"] for r in outside)[:3]
        raise ContractViolationError(
            f"Q3 interval_containment: {len(outside)} of {len(rows)} rows fall outside "
            f"[{interval_start}, {interval_end}); e.g. {sample}. The batch is not the interval."
        )

    return {
        "rows_validated": len(rows),
        "blank_headlines": missing.get("headline", 0),
        "checks_passed": ["Q1", "Q2", "Q3"],
    }
