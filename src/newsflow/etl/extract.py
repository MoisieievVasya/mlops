"""Task 1 — extract: pull the rows of one data interval out of the versioned archive.

The batch is `[interval_start, interval_end)` on the article's own `date` field. The
clock is never consulted: a run for 2015-03 reads the rows of March 2015 whether it
is triggered today, backfilled next year, or rerun twice in a row.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from newsflow.config import RAW_ARCHIVE
from newsflow.lineage import data_version
from newsflow.warehouse import replace_partition

RAW_COLUMNS = (
    "article_id",
    "batch_month",
    "published_at",
    "headline",
    "short_description",
    "authors",
    "link",
    "raw_category",
    "source_file",
    "data_version",
)


def _parse_date(value: str) -> date:
    return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()


def read_interval(
    interval_start: date,
    interval_end: date,
    archive: Path | None = None,
) -> list[dict[str, Any]]:
    """Every archive row whose own date falls in [interval_start, interval_end).

    A row with an unparseable or absent date is kept and handed to `validate`, which
    is the task that owns the schema contract. Swallowing it here would turn a
    contract breach into a quietly shorter batch.
    """
    path = archive or RAW_ARCHIVE
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. The archive is DVC-tracked, not committed: run `make data`."
        )

    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            raw_date = str(record.get("date", ""))
            try:
                published = _parse_date(raw_date)
            except (ValueError, TypeError):
                rows.append({**record, "_date_unparseable": True})
                continue
            if interval_start <= published < interval_end:
                rows.append(record)
    return rows


# --------------------------------------------------------------------- faults ---
# Breaking a batch on purpose is a first-class, documented operation: the quality
# gate is a property we show, not one we claim (conf/quality_contract.yaml:faults).


def inject_fault(rows: list[dict[str, Any]], fault: str) -> list[dict[str, Any]]:
    if fault in ("", "none", None):
        return rows
    if not rows:
        raise ValueError(f"cannot inject fault {fault!r} into an empty batch")

    broken = [dict(row) for row in rows]
    if fault == "unmapped_label":
        # A label the upstream desk invented and nobody told us about -> Q4.
        broken[0]["category"] = "CRYPTO"
    elif fault == "null_headline":
        # A field that stopped being delivered -> rows lose all text -> Q7.
        for row in broken[: max(1, len(broken) // 20)]:
            row["headline"] = ""
            row["short_description"] = ""
    elif fault == "duplicate_rows":
        # The same batch delivered twice and merged -> Q5.
        broken.extend(dict(row) for row in broken[:10])
    elif fault == "truncate_batch":
        # A half-delivered month -> Q8.
        broken = broken[:10]
    else:
        raise ValueError(f"unknown fault {fault!r}; see conf/quality_contract.yaml")
    return broken


# ---------------------------------------------------------------------- task ---


def run(
    batch_month: date,
    interval_start: date,
    interval_end: date,
    *,
    fault: str = "none",
) -> dict[str, Any]:
    """Load the interval into `raw.articles`, replacing anything already there."""
    from newsflow.text import article_id  # local import keeps text.py free of config

    rows = read_interval(interval_start, interval_end)
    rows = inject_fault(rows, fault)

    version = data_version()
    source = RAW_ARCHIVE.name
    payload = [
        (
            article_id(row.get("link"), row.get("headline"), str(row.get("date", ""))),
            batch_month,
            _safe_date(row.get("date"), batch_month),
            row.get("headline"),
            row.get("short_description"),
            row.get("authors"),
            row.get("link"),
            row.get("category"),
            source,
            version,
        )
        for row in rows
    ]
    # The raw layer keeps the batch as delivered, duplicates included: deduplication
    # is a decision of the clean layer, and Q5 must still be able to see the double
    # delivery. Only an exact (month, id) collision is collapsed, because the table's
    # key forbids it.
    deduped: dict[tuple[date, str], tuple] = {(r[1], r[0]): r for r in payload}
    replace_partition("raw.articles", RAW_COLUMNS, list(deduped.values()), batch_month)

    return {
        "rows_extracted": len(payload),
        "rows_raw_stored": len(deduped),
        "data_version": version,
        "fault_injected": fault if fault not in ("", "none") else None,
    }


def _safe_date(value: Any, fallback: date) -> date:
    try:
        return _parse_date(str(value))
    except (ValueError, TypeError):
        return fallback
