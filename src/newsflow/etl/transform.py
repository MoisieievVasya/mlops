"""Task 3 — transform: raw rows become typed, deduplicated, mapped clean rows.

Three decisions are enforced here, each named in the design document:

* **`link` is dropped** (design 5.2). The URL is minted by the CMS after the section
  is chosen, so it is a perfect offline predictor and does not exist at serving time.
  It is dropped at the layer boundary — `dwh.articles` has no such column — rather
  than merely "not selected" by whoever writes the next model.
* **the byline is hashed** (design 6.5). Plaintext author names exist in the raw
  layer and nowhere else.
* **raw labels are mapped, never guessed** (design R1). An unknown label becomes
  UNMAPPED and the gate fails the batch on it.

Two cleaning rules drop rows, and both are counted so the gate can see them:
  * a row with neither headline nor dek has no text to classify — 5 rows in the
    whole archive;
  * a repeated `article_id` is the same article delivered twice — 41 rows in the
    whole archive, all of them mangled `huffingtonpost.comhttp://...` links. The
    check is against the **warehouse**, not just the batch: some of those
    re-deliveries land in a different month from the original, where an in-batch
    `seen` set cannot see them. The first delivery wins.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from newsflow.config import AUTHOR_SALT, SOURCE_FEED_ID
from newsflow.lineage import git_commit
from newsflow.taxonomy import to_serving
from newsflow.text import NO_DEK, hash_author, normalise, normalise_dek, word_count
from newsflow.warehouse import fetch_dicts

CLEAN_COLUMNS = (
    "article_id",
    "batch_month",
    "event_ts",
    "headline_norm",
    "dek_norm",
    "has_dek",
    "text_len_words",
    "author_hash",
    "raw_category",
    "label_category",
    "source_feed_id",
    "data_version",
    "git_commit",
)


def run(batch_month: date) -> dict[str, Any]:
    raw_rows = fetch_dicts(
        """
        SELECT article_id, published_at, headline, short_description, authors,
               raw_category, data_version
          FROM raw.articles
         WHERE batch_month = %s
         ORDER BY published_at, article_id
        """,
        (batch_month,),
    )

    commit = git_commit()
    seen: set[str] = set()
    clean: list[tuple] = []
    dropped_empty = 0
    dropped_dup = 0
    unmapped = 0

    for row in raw_rows:
        headline_norm = normalise(row["headline"])
        dek_norm = normalise_dek(row["short_description"])

        if not headline_norm and dek_norm == NO_DEK:
            dropped_empty += 1
            continue
        if row["article_id"] in seen:
            dropped_dup += 1
            continue
        seen.add(row["article_id"])

        label = to_serving(row["raw_category"])
        if label == "UNMAPPED":
            unmapped += 1

        clean.append(
            (
                row["article_id"],
                batch_month,
                row["published_at"],
                headline_norm,
                dek_norm,
                dek_norm != NO_DEK,
                word_count(headline_norm, dek_norm),
                hash_author(row["authors"], AUTHOR_SALT),
                (row["raw_category"] or "").strip().upper(),
                label,
                SOURCE_FEED_ID,
                row["data_version"],
                commit,
            )
        )

    # Deduplication is warehouse-wide, not batch-wide. The archive delivers 41 rows
    # under a link it has already used, and some of those re-deliveries land in a
    # *different* month from the original — which an in-batch `seen` set cannot see
    # and which `dwh.articles`' primary key rejects outright. The first delivery
    # wins: a re-delivered article is one article, and keeping both would double its
    # weight in training and corrupt the author's point-in-time prior.
    dropped_cross_month = 0
    if clean:
        already = {
            row["article_id"]
            for row in fetch_dicts(
                """
                SELECT article_id FROM dwh.articles
                 WHERE batch_month <> %(month)s AND article_id = ANY(%(ids)s)
                """,
                {"month": batch_month, "ids": [row[0] for row in clean]},
            )
        }
        if already:
            clean = [row for row in clean if row[0] not in already]
            dropped_cross_month = len(already)

    _STAGE[batch_month] = clean
    return {
        "rows_in": len(raw_rows),
        "rows_clean": len(clean),
        "rows_dropped_empty": dropped_empty,
        "rows_dropped_dup": dropped_dup + dropped_cross_month,
        "rows_dropped_dup_cross_month": dropped_cross_month,
        "rows_unmapped_label": unmapped,
        "git_commit": commit,
    }


#: Clean rows between `transform` and `load`, inside one process.
#: LocalExecutor runs both tasks on the same machine but not in the same process, so
#: `load` recomputes from the raw layer when the stage is cold. The transform is a
#: pure function of raw.articles, which is what makes that safe — and is the reason
#: the clean rows are not pushed through XCom, where a 3,000-row batch does not fit.
_STAGE: dict[date, list[tuple]] = {}


def clean_rows(batch_month: date) -> list[tuple]:
    """The clean rows for a month, computed if this process has not already."""
    if batch_month not in _STAGE:
        run(batch_month)
    return _STAGE[batch_month]
