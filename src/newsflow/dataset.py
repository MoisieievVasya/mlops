"""Reading the feature table into the frame both the model and the baselines see.

One function builds the training frame, and the column names it produces are the
column names the serving request will carry. That is deliberate: the model's input
schema is logged to MLflow from this frame, so a serving payload that does not match
it fails loudly at the boundary instead of quietly scoring a different feature.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from newsflow.taxonomy import UNMAPPED, serving_categories
from newsflow.text import model_text
from newsflow.warehouse import fetch_dicts

#: The 17 columns the author_desk_prior vector is unpacked into.
PRIOR_COLUMNS = [
    f"prior_{name.lower().replace(' & ', '_').replace(' ', '_')}" for name in serving_categories()
]

TEXT_COLUMN = "model_text"
NUMERIC_COLUMNS = ["text_len_words", "author_seen_count"]
CATEGORICAL_COLUMNS = ["publish_dow", "publish_month", "has_dek"]
LABEL_COLUMN = "label_category"

FEATURE_COLUMNS = [TEXT_COLUMN, *PRIOR_COLUMNS, *NUMERIC_COLUMNS, *CATEGORICAL_COLUMNS]


def load(since: date | None = None, until: date | None = None) -> pd.DataFrame:
    """The feature table as a frame, ordered by time.

    UNMAPPED rows cannot appear here — the quality gate rejects any batch containing
    one — but the filter stays as a second line of defence against a hand-loaded row.
    """
    clauses = ["label_category <> %(unmapped)s"]
    params: dict[str, object] = {"unmapped": UNMAPPED}
    if since is not None:
        clauses.append("event_ts >= %(since)s")
        params["since"] = since
    if until is not None:
        clauses.append("event_ts < %(until)s")
        params["until"] = until

    rows = fetch_dicts(
        f"""
        SELECT article_id, event_ts, batch_month, headline_norm, dek_norm,
               text_len_words, has_dek, publish_dow, publish_month,
               author_hash, author_seen_count, author_desk_prior,
               source_feed_id, label_category, feature_version
          FROM features.article_features
         WHERE {" AND ".join(clauses)}
         ORDER BY event_ts, article_id
        """,  # noqa: S608 - clauses are literals, values are bound
        params,
    )
    if not rows:
        raise RuntimeError(
            "features.article_features is empty for the requested window. "
            "Run `make backfill` first — the model trains on the warehouse, not on the archive."
        )

    frame = pd.DataFrame(rows)
    # `features.article_features.event_ts` is `timestamptz`, built from a date, so
    # every value is UTC midnight. Normalising to naive UTC here keeps the split
    # bounds (plain dates, from the design document) comparable without each call
    # site having to remember which side carries a timezone.
    frame["event_ts"] = pd.to_datetime(frame["event_ts"], utc=True).dt.tz_localize(None)
    frame[TEXT_COLUMN] = [
        model_text(h, d) for h, d in zip(frame["headline_norm"], frame["dek_norm"], strict=True)
    ]
    priors = pd.DataFrame(
        frame.pop("author_desk_prior").tolist(), columns=PRIOR_COLUMNS, index=frame.index
    ).astype("float32")
    return pd.concat([frame, priors], axis=1)


def time_split(
    frame: pd.DataFrame,
    train_end: date,
    valid_end: date,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Train / validation / hold-out, cut on the time axis (design 5.4).

    Never a random split: news arrives in bursts, so a single event produces many
    near-duplicate headlines on one day and a random split puts those near-duplicates
    on both sides of the cut. The score would then measure memorisation of a news
    cycle, and the model is asked about *future* articles anyway.
    """
    ts = frame["event_ts"]
    train = frame[ts < pd.Timestamp(train_end)]
    valid = frame[(ts >= pd.Timestamp(train_end)) & (ts < pd.Timestamp(valid_end))]
    holdout = frame[ts >= pd.Timestamp(valid_end)]
    return train, valid, holdout


def rolling_origin_folds(
    frame: pd.DataFrame,
    first_month: date,
    n_folds: int,
) -> list[tuple[pd.DataFrame, pd.DataFrame, str]]:
    """For each month m: train on everything strictly before m, score m.

    Returns (train, test, label) per fold. The mean across folds is the reported
    validation metric and the spread across folds is reported with it — a model with
    a good mean and a bad worst month is a model that will generate an incident.
    """
    folds = []
    months = pd.period_range(start=pd.Timestamp(first_month), periods=n_folds, freq="M")
    for month in months:
        start = month.to_timestamp()
        end = (month + 1).to_timestamp()
        train = frame[frame["event_ts"] < start]
        test = frame[(frame["event_ts"] >= start) & (frame["event_ts"] < end)]
        if len(test) == 0 or len(train) == 0:
            continue
        folds.append((train, test, str(month)))
    return folds
