"""Feast definitions — the entity, the source and the view.

One entity (the hashed byline) and one feature view (what that byline's desk mix
looked like as of a point in time). That is a deliberately small feature store: design
6.2 rejects the managed alternative precisely because there is **one** online feature
here, and a feature platform priced for hundreds is not worth $219 a month to serve a
single 17-element vector.

The source is a Parquet file exported from `features.article_features` by
`newsflow.feast_export`. Each row says: as of `event_ts`, this author had published
`author_seen_count` articles and their section mix was this distribution. A
point-in-time join for an entity at time T therefore returns the newest row with
`event_ts <= T` — the state that was actually knowable at T, not the state at the end
of history.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from feast import Entity, FeatureView, Field, FileSource
from feast.types import Float32, Int64, String

REPO = Path(__file__).parent
SERVING_CATEGORIES = [
    "POLITICS",
    "WORLD NEWS",
    "BUSINESS & MONEY",
    "SCIENCE & TECH",
    "ENVIRONMENT",
    "HEALTH & WELLNESS",
    "FOOD & DRINK",
    "TRAVEL",
    "STYLE & BEAUTY",
    "HOME & LIVING",
    "FAMILY & RELATIONSHIPS",
    "ENTERTAINMENT",
    "ARTS & CULTURE",
    "SPORTS",
    "CRIME & JUSTICE",
    "EDUCATION",
    "SOCIETY & IDENTITY",
]
PRIOR_FIELDS = [
    f"prior_{name.lower().replace(' & ', '_').replace(' ', '_')}" for name in SERVING_CATEGORIES
]

author = Entity(
    name="author",
    join_keys=["author_hash"],
    value_type=String,
    description="A journalist's byline, salted-hashed. Never the plaintext name (design 6.5).",
)

author_desk_source = FileSource(
    name="author_desk_history",
    path=str(REPO / "data" / "author_desk_state.parquet"),
    timestamp_field="event_ts",
    created_timestamp_column="created_ts",
    description="Running section mix per byline, one row per article they published.",
)

author_desk_stats = FeatureView(
    name="author_desk_stats",
    entities=[author],
    # A byline that has not published for two years tells us little about the next
    # article, and an unbounded TTL would serve a stale prior as if it were current.
    ttl=timedelta(days=730),
    schema=[Field(name="author_seen_count", dtype=Int64)]
    + [Field(name=field, dtype=Float32) for field in PRIOR_FIELDS],
    source=author_desk_source,
    online=True,
    description="author_desk_prior and author_seen_count, as of a point in time.",
)
