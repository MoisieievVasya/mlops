"""Task 5 — build_features: the feature table, and the point-in-time join.

Every column here has a serving-time source declared in conf/features.yaml, and that
file is hashed into `feature_version`, which is stamped on every row. A feature whose
serving-time source is unknown is the feature that will differ between training and
serving, so the contract is machine-readable and the version moves when it changes.

`author_desk_prior` is the one that needs care. It is the distribution of an author's
*previous* sections, and computed naively over the whole training set it encodes the
answer. The join below is restricted to rows with `event_ts` **strictly before** the
row being built, which is what a point-in-time (as-of) join means:

    FROM dwh.articles h
    JOIN batch b ON h.author_hash = b.author_hash
                AND h.event_ts <  b.event_ts      -- never <=

Two consequences, both deliberate:

* the archive has day granularity, so two articles by the same author on the same day
  do not see each other. That is the conservative direction: a prior that is slightly
  too empty cannot leak, a prior that is slightly too full can.
* the join filters on `event_ts`, not on load time, so re-running `build_features`
  over an unchanged warehouse always produces the same vectors — which is what makes
  the task idempotent.

The ordering caveat, stated rather than hidden: the join can only see history that is
*already in* `dwh.articles`. Backfilling chronologically (which `max_active_runs=1`
on the DAG enforces) gives every month the full history that preceded it, exactly as
production would. Ingesting 2015 after 2016 would give 2015 a correct prior but leave
2016's prior short of 2015's rows until `build_features` is re-run for it. The month
is a pure function of the warehouse; the warehouse is not a pure function of one run.
"""

from __future__ import annotations

import functools
import hashlib
from datetime import date
from typing import Any

from newsflow.config import FEATURES_PATH, TAXONOMY_PATH
from newsflow.taxonomy import category_index, serving_categories
from newsflow.warehouse import fetch_dicts, replace_partition, scalar

FEATURE_COLUMNS = (
    "article_id",
    "batch_month",
    "event_ts",
    "headline_norm",
    "dek_norm",
    "text_len_words",
    "has_dek",
    "publish_dow",
    "publish_month",
    "author_hash",
    "author_seen_count",
    "author_desk_prior",
    "source_feed_id",
    "label_category",
    "feature_version",
)

#: Below this many previous articles an author's own prior is not trusted and the
#: row carries the zero vector instead (design 5.3, `author_seen_count`).
MIN_AUTHOR_HISTORY = 5

#: An absent byline is not an identity. 17.9% of the archive has none, and treating
#: them as one prolific author would both leak across unrelated writers and build a
#: 37,000-row bucket. They get the zero vector and a seen count of 0.
ANON = "anon"


@functools.lru_cache(maxsize=1)
def feature_version() -> str:
    """`fv1.<8 hex>` over the feature contract and the taxonomy it references.

    Both files are hashed: the taxonomy decides the length and the meaning of the
    author_desk_prior vector, so a taxonomy change is a feature change.
    """
    digest = hashlib.sha256()
    for path in (FEATURES_PATH, TAXONOMY_PATH):
        digest.update(path.read_bytes())
    return f"fv1.{digest.hexdigest()[:8]}"


def _prior_counts(batch_month: date) -> dict[str, dict[str, int]]:
    """For each article in the month: {previous section -> count}, strictly earlier."""
    rows = fetch_dicts(
        """
        WITH batch AS (
            SELECT article_id, author_hash, event_ts
              FROM dwh.articles
             WHERE batch_month = %(month)s
               AND author_hash <> %(anon)s
        )
        SELECT b.article_id,
               h.label_category AS prior_label,
               count(*)         AS n
          FROM batch b
          JOIN dwh.articles h
            ON h.author_hash = b.author_hash
           AND h.event_ts    < b.event_ts
         GROUP BY b.article_id, h.label_category
        """,
        {"month": batch_month, "anon": ANON},
    )
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        counts.setdefault(row["article_id"], {})[row["prior_label"]] = int(row["n"])
    return counts


def run(batch_month: date) -> dict[str, Any]:
    articles = fetch_dicts(
        """
        SELECT article_id, batch_month, event_ts, headline_norm, dek_norm,
               text_len_words, has_dek, author_hash, source_feed_id, label_category
          FROM dwh.articles
         WHERE batch_month = %s
         ORDER BY event_ts, article_id
        """,
        (batch_month,),
    )

    counts = _prior_counts(batch_month)
    index = category_index()
    width = len(serving_categories())
    version = feature_version()

    rows: list[tuple] = []
    trusted_priors = 0
    for article in articles:
        prior_counts = counts.get(article["article_id"], {})
        seen = sum(prior_counts.values())
        vector = [0.0] * width
        if seen >= MIN_AUTHOR_HISTORY:
            for label, n in prior_counts.items():
                position = index.get(label)
                if position is not None:  # an UNMAPPED prior contributes nothing
                    vector[position] = n / seen
            trusted_priors += 1

        event_ts = article["event_ts"]
        rows.append(
            (
                article["article_id"],
                batch_month,
                event_ts,
                article["headline_norm"],
                article["dek_norm"],
                article["text_len_words"],
                article["has_dek"],
                event_ts.weekday(),
                event_ts.month,
                article["author_hash"],
                seen,
                vector,
                article["source_feed_id"],
                article["label_category"],
                version,
            )
        )

    replace_partition("features.article_features", FEATURE_COLUMNS, rows, batch_month)
    stored = scalar(
        "SELECT count(*) FROM features.article_features WHERE batch_month = %s",
        (batch_month,),
    )
    return {
        "rows_features": int(stored or 0),
        "rows_with_trusted_prior": trusted_priors,
        "feature_version": version,
    }
