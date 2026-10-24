"""Point-in-time correctness, shown rather than asserted.

    uv sync --extra feast
    make feast-demo

The demo does the one thing a feature store exists for. It picks a byline with at
least two articles on different dates, builds an entity dataframe whose timestamp
falls **between** those two feature rows, retrieves offline features for it, and
checks that what comes back is the *earlier* row.

That is the whole property. A plain join on the latest value would return the later
row — the state of the world after the article was published — and a model trained on
that has been told part of its own answer. Here the future is not visible, so the
training-time value of `author_desk_prior` is the value serving would have had.

The export step builds the source: for each article, the author's running section mix
*including* that article, stamped with that article's date. A point-in-time join at
time T returns the newest row with `event_ts <= T`, which is exactly what the nightly
online refresh of design 5.3 would hold at T.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta

import pandas as pd

from newsflow.config import PROJECT_ROOT
from newsflow.taxonomy import category_index, serving_categories
from newsflow.warehouse import fetch_dicts

REPO_PATH = PROJECT_ROOT / "feature_repo"
EXPORT_PATH = REPO_PATH / "data" / "author_desk_state.parquet"

PRIOR_FIELDS = [
    f"prior_{name.lower().replace(' & ', '_').replace(' ', '_')}" for name in serving_categories()
]


def export_author_state() -> pd.DataFrame:
    """Build the offline source: the running desk mix per byline, over time.

    `author_desk_prior` in the warehouse is the mix *before* each article, which is
    what training needs. Feast's source is the mix *as of* each article — the two
    differ by exactly one row, and the difference is why the join below has something
    to get right.
    """
    rows = fetch_dicts(
        """
        SELECT author_hash, event_ts::date AS event_ts, label_category
          FROM dwh.articles
         WHERE author_hash <> 'anon'
         ORDER BY author_hash, event_ts
        """
    )
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise SystemExit("dwh.articles is empty — run `make backfill` first.")

    index = category_index()
    width = len(index)
    records: list[dict] = []
    for author_hash, group in frame.groupby("author_hash", sort=False):
        counts = [0] * width
        pairs = zip(group["event_ts"], group["label_category"], strict=True)
        for seen, (event_ts, label) in enumerate(pairs, start=1):
            position = index.get(label)
            if position is not None:
                counts[position] += 1
            record = {
                "author_hash": author_hash,
                "event_ts": pd.Timestamp(event_ts, tz="UTC"),
                "author_seen_count": seen,
            }
            record.update({field: counts[i] / seen for i, field in enumerate(PRIOR_FIELDS)})
            records.append(record)

    state = pd.DataFrame.from_records(records)
    # One row per (byline, day): if a byline published twice on one day, the day's
    # final state is the one that was knowable at the end of that day.
    state = state.drop_duplicates(subset=["author_hash", "event_ts"], keep="last")
    state["created_ts"] = state["event_ts"]
    state["author_seen_count"] = state["author_seen_count"].astype("int64")
    for field in PRIOR_FIELDS:
        state[field] = state[field].astype("float32")

    EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    state.to_parquet(EXPORT_PATH, index=False)
    print(f"exported {len(state):,} rows for {state['author_hash'].nunique():,} bylines")
    print(f"  -> {EXPORT_PATH.relative_to(PROJECT_ROOT)}")
    return state


def pick_demo_author(state: pd.DataFrame) -> tuple[str, pd.Timestamp, pd.Timestamp]:
    """A byline with two feature rows far enough apart to query between them."""
    counts = state.groupby("author_hash")["event_ts"].agg(["count", "min", "max"])
    candidates = counts[counts["count"] >= 6]
    if candidates.empty:
        raise SystemExit("no byline has enough history for the demo — backfill more months")

    author_hash = candidates.sort_values("count", ascending=False).index[0]
    rows = state[state["author_hash"] == author_hash].sort_values("event_ts")
    # The two consecutive rows with the widest gap between them, so the query
    # timestamp sits unambiguously between the two. `diff()` leaves NaT in the first
    # position — dropping it is what keeps the pair consecutive and ordered.
    gaps = rows["event_ts"].diff().iloc[1:]
    position = 1 + int(gaps.reset_index(drop=True).idxmax())
    return author_hash, rows["event_ts"].iloc[position - 1], rows["event_ts"].iloc[position]


def main() -> int:
    from feast import FeatureStore

    os.environ.setdefault("NEWSFLOW_PG_HOST", "localhost")
    os.environ.setdefault("NEWSFLOW_PG_PORT", os.environ.get("POSTGRES_HOST_PORT", "5433"))

    state = export_author_state()
    author_hash, earlier, later = pick_demo_author(state)
    between = earlier + (later - earlier) / 2

    print(f"\nbyline {author_hash}")
    print(f"  feature row A  {earlier.date()}")
    print(f"  feature row B  {later.date()}")
    print(f"  querying as of {between.date()}  (between A and B)")

    # Register the definitions, then retrieve. `feast apply` from the CLI does the
    # same thing; doing it here keeps the demo to one command.
    sys.path.insert(0, str(PROJECT_ROOT))
    from feature_repo.features import author, author_desk_source, author_desk_stats

    store = FeatureStore(repo_path=str(REPO_PATH))
    store.apply([author, author_desk_source, author_desk_stats])

    entity_frame = pd.DataFrame(
        {
            "author_hash": [author_hash, author_hash, author_hash],
            "event_timestamp": [
                earlier + timedelta(hours=1),
                between,
                later + timedelta(hours=1),
            ],
            "label": ["just after A", "between A and B", "just after B"],
        }
    )

    retrieved = store.get_historical_features(
        entity_df=entity_frame,
        features=[
            "author_desk_stats:author_seen_count",
            *[f"author_desk_stats:{field}" for field in PRIOR_FIELDS[:3]],
        ],
    ).to_df()

    print("\nwhat the offline store returned:")
    print(retrieved[["label", "event_timestamp", "author_seen_count"]].to_string(index=False))

    truth = state[state["author_hash"] == author_hash].set_index("event_ts")
    expected_at_a = int(truth.loc[earlier, "author_seen_count"])
    expected_at_b = int(truth.loc[later, "author_seen_count"])
    between_row = retrieved.loc[retrieved["label"] == "between A and B", "author_seen_count"]
    got_between = int(between_row.iloc[0])

    print(f"\n  row A says the byline had published {expected_at_a}")
    print(f"  row B says the byline had published {expected_at_b}")
    print(f"  the query between them returned      {got_between}")

    if got_between == expected_at_a:
        print("\nPOINT-IN-TIME CORRECT: the earlier row was returned; row B is in the")
        print("query's future and stayed invisible.")
        return 0
    print("\nLEAKAGE: the join returned a value from after the query timestamp.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
