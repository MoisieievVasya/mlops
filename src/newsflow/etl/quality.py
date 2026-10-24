"""Task 6 — quality_check: MARK 2 of the data-flow diagram.

The checks are declared in conf/quality_contract.yaml and evaluated here. A failure
does three things, in this order:

1. **rolls the batch out of the warehouse** — the month's partition is deleted from
   `features.article_features` and `dwh.articles`, so a rejected batch is rejected
   rather than merely flagged. Design 6.1 mark 2: "FAIL = the batch is rejected,
   nothing downstream runs";
2. writes the failure into `dwh.batch_runs` with the check id and the observed value,
   so the ledger shows the rejection after the rows are gone;
3. raises, which fails the task, the run, and anything downstream of it.

The raw layer is deliberately *not* rolled back: keeping the rejected batch exactly
as delivered is what lets someone work out what the upstream actually sent.
"""

from __future__ import annotations

import functools
from datetime import date
from typing import Any

import yaml

from newsflow.config import QUALITY_PATH
from newsflow.etl.features import feature_version
from newsflow.warehouse import delete_partition, fetch_dicts, scalar


class QualityGateError(AssertionError):
    """A batch failed the gate. The partition has been rolled back."""


@functools.lru_cache(maxsize=1)
def contract() -> dict:
    with QUALITY_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _threshold(check_id: str, key: str, default: Any) -> Any:
    for check in contract()["checks"]:
        if check["id"] == check_id:
            return check.get(key, default)
    return default


def evaluate(batch_month: date, rows_extracted: int) -> tuple[list[dict], list[dict]]:
    """Run Q4–Q10. Returns (failures, observations); neither raises."""
    stats = fetch_dicts(
        """
        SELECT count(*)                                           AS rows_loaded,
               count(DISTINCT article_id)                         AS distinct_ids,
               count(*) FILTER (WHERE label_category = 'UNMAPPED') AS unmapped,
               count(*) FILTER (WHERE has_dek IS FALSE)            AS no_dek
          FROM dwh.articles
         WHERE batch_month = %s
        """,
        (batch_month,),
    )[0]
    feature_stats = fetch_dicts(
        """
        SELECT count(*)                                              AS rows_features,
               count(*) FILTER (WHERE feature_version = %s)          AS rows_current_version
          FROM features.article_features
         WHERE batch_month = %s
        """,
        (feature_version(), batch_month),
    )[0]

    loaded = int(stats["rows_loaded"])
    dropped_share = (rows_extracted - loaded) / rows_extracted if rows_extracted else 0.0
    no_dek_share = int(stats["no_dek"]) / loaded if loaded else 0.0

    checks = [
        {
            "id": "Q4",
            "name": "every_raw_label_mapped",
            "observed": int(stats["unmapped"]),
            "expected": "0 rows with label_category = UNMAPPED",
            "passed": int(stats["unmapped"]) == 0,
            "detail": "a raw label was observed that conf/taxonomy.yaml does not map",
        },
        {
            "id": "Q5",
            "name": "no_duplicate_ids",
            "observed": loaded - int(stats["distinct_ids"]),
            "expected": "0 duplicate article_id in the partition",
            "passed": loaded == int(stats["distinct_ids"]),
            "detail": "the same article appears twice in the month",
        },
        {
            "id": "Q6",
            "name": "missing_dek_share",
            "observed": round(no_dek_share, 4),
            "expected": f"<= {_threshold('Q6', 'threshold', 0.25)}",
            "passed": no_dek_share <= float(_threshold("Q6", "threshold", 0.25)),
            "detail": "the upstream has stopped delivering the dek field (M11)",
        },
        {
            "id": "Q7",
            "name": "dropped_row_share",
            "observed": round(dropped_share, 4),
            "expected": f"<= {_threshold('Q7', 'threshold', 0.02)}",
            "passed": dropped_share <= float(_threshold("Q7", "threshold", 0.02)),
            "detail": "cleaning dropped more of the batch than a healthy month ever needs",
        },
        {
            "id": "Q8",
            "name": "row_count_within_bounds",
            "observed": loaded,
            "expected": f"{_threshold('Q8', 'min_rows', 50)}..{_threshold('Q8', 'max_rows', 6000)}",
            "passed": int(_threshold("Q8", "min_rows", 50))
            <= loaded
            <= int(_threshold("Q8", "max_rows", 6000)),
            "detail": "a half-delivered or doubled month",
        },
        {
            "id": "Q9",
            "name": "feature_parity",
            "observed": int(feature_stats["rows_features"]),
            "expected": f"== {loaded} (rows in dwh.articles)",
            "passed": int(feature_stats["rows_features"]) == loaded,
            "detail": "the feature table is stale or the build skipped rows",
        },
        {
            "id": "Q10",
            "name": "feature_version_present",
            "observed": int(feature_stats["rows_current_version"]),
            "expected": f"== {feature_stats['rows_features']} (all rows at {feature_version()})",
            "passed": int(feature_stats["rows_current_version"])
            == int(feature_stats["rows_features"]),
            "detail": "rows built by a feature definition that is no longer the current one",
        },
    ]
    return [c for c in checks if not c["passed"]], checks


def waiver_for(check_id: str, batch_month: date) -> dict | None:
    """The recorded decision, if one covers this check and this month.

    A waiver is not a softer threshold. It names one check and one period, it carries
    a reason and an approver, and deleting it re-fails every month it covered. The
    check itself is unchanged everywhere else, which is what keeps "we looked at this
    once and decided" distinct from "we stopped looking".
    """
    month = f"{batch_month:%Y-%m}"
    for waiver in contract().get("waivers") or []:
        if waiver["check"] == check_id and str(waiver["from"]) <= month <= str(waiver["to"]):
            return waiver
    return None


def run(batch_month: date, rows_extracted: int) -> dict[str, Any]:
    failures, checks = evaluate(batch_month, rows_extracted)

    waived = [(c, w) for c in failures if (w := waiver_for(c["id"], batch_month))]
    waived_ids = {c["id"] for c, _ in waived}
    failures = [c for c in failures if c["id"] not in waived_ids]

    if not failures:
        result: dict[str, Any] = {
            "gate": "passed" if not waived else "passed_with_waiver",
            "checks": {c["id"]: c["observed"] for c in checks},
        }
        if waived:
            # Recorded on the run, so a waived month is visibly different from a clean
            # one in the ledger rather than indistinguishable from it.
            result["waivers"] = [
                {"id": w["id"], "check": c["id"], "observed": c["observed"]} for c, w in waived
            ]
        return result

    rolled_back = {
        "features.article_features": delete_partition("features.article_features", batch_month),
        "dwh.articles": delete_partition("dwh.articles", batch_month),
    }
    lines = [
        f"  {c['id']} {c['name']}: observed {c['observed']}, expected {c['expected']}"
        f"  <- {c['detail']}"
        for c in failures
    ]
    raise QualityGateError(
        f"QUALITY GATE FAILED for {batch_month:%Y-%m} — {len(failures)} check(s):\n"
        + "\n".join(lines)
        + f"\nThe batch was rejected and rolled back out of the warehouse: {rolled_back}. "
        "raw.articles keeps it exactly as delivered, for diagnosis."
    )


def partition_counts(batch_month: date) -> dict[str, int]:
    """Row counts per layer for one month — the idempotency evidence."""
    return {
        "raw.articles": int(
            scalar("SELECT count(*) FROM raw.articles WHERE batch_month = %s", (batch_month,)) or 0
        ),
        "dwh.articles": int(
            scalar("SELECT count(*) FROM dwh.articles WHERE batch_month = %s", (batch_month,)) or 0
        ),
        "features.article_features": int(
            scalar(
                "SELECT count(*) FROM features.article_features WHERE batch_month = %s",
                (batch_month,),
            )
            or 0
        ),
    }
