"""Generate the results tables from the tracking server, not from memory.

    uv run python scripts/report_results.py --write

Reads the four runs of the latest training, the registered version and its tags, and
emits the comparison table. With `--write` it replaces the block between the
`<!-- RESULTS:START -->` markers in the README, so the numbers in the documentation
cannot drift from the numbers in MLflow — the same reason `feature_version` is a hash
of the feature contract rather than a string somebody remembers to bump.
"""

from __future__ import annotations

import argparse
import re
import sys

import mlflow
from mlflow.tracking import MlflowClient

from newsflow.config import MLFLOW_EXPERIMENT, MLFLOW_TRACKING_URI, PROJECT_ROOT, REGISTERED_MODEL

#: Both files carry the same marked block, so one command keeps them in step.
TARGETS = (PROJECT_ROOT / "README.md", PROJECT_ROOT / "docs" / "design.md")


def latest_runs(client: MlflowClient) -> dict[str, object]:
    experiment = client.get_experiment_by_name(MLFLOW_EXPERIMENT)
    if experiment is None:
        raise SystemExit(f"no experiment named {MLFLOW_EXPERIMENT} — run `make train` first")
    runs = client.search_runs([experiment.experiment_id], order_by=["attributes.start_time DESC"])
    out: dict[str, object] = {}
    for run in runs:
        name = run.data.tags.get("mlflow.runName", "")
        if name and name not in out:
            out[name] = run
    return out


def _metric(run, key: str, default: float | None = None) -> float | None:
    if run is None:
        return default
    return run.data.metrics.get(key, default)


def build_table(runs: dict) -> str:
    tier1 = runs.get("tier1-tfidf-linear-svm")
    if tier1 is None:
        raise SystemExit("no tier1 run found — run `make train` first")

    majority = _metric(runs.get("tier0-majority"), "macro_f1_holdout")
    rule = _metric(runs.get("tier0-rule"), "macro_f1_holdout")
    coverage = _metric(runs.get("tier0-rule"), "rule_coverage_holdout")
    no_prior = _metric(runs.get("tier1-no-prior"), "macro_f1_holdout")
    model = _metric(tier1, "macro_f1_holdout")

    rows = [
        "| Tier | Model | Hold-out macro-F1 | Design 5.3 expected | Note |",
        "| --- | --- | --- | --- | --- |",
        f"| 0 | always the majority class | **{majority:.4f}** | ≈ 0.02 | "
        f"predicts `{runs['tier0-majority'].data.params.get('majority_class')}`, the training "
        "window's largest class |",
        f"| 0 | hand-written keyword lexicon | **{rule:.4f}** | 0.25–0.35 | "
        f"matched {coverage:.1%} of hold-out rows; the rest fall back to the majority |",
        f"| **1** | **TF-IDF → linear SVM (SGD), calibrated** | **{model:.4f}** | 0.62–0.68 | "
        "registered as `@champion` |",
        f"| 1 | the same, without `author_desk_prior` | {no_prior:.4f} | — | "
        f"R6 leakage check: the feature is worth {model - no_prior:+.4f} |",
    ]

    val_mean = _metric(tier1, "macro_f1_val_mean")
    val_std = _metric(tier1, "macro_f1_val_std")
    val_worst = _metric(tier1, "macro_f1_val_worst")
    ece = _metric(tier1, "ece_holdout")
    worst_class = _metric(tier1, "min_class_recall_holdout")
    confidence = _metric(tier1, "mean_top1_confidence")
    auto_accept = _metric(tier1, "auto_accept_share_at_tau_0.6")

    extra = [
        "",
        "| Secondary metric, tier 1 | Value | Gate (design 5.4) |",
        "| --- | --- | --- |",
        f"| Rolling-origin validation, mean over 12 folds | {val_mean:.4f} | — |",
        f"| Rolling-origin validation, spread across folds | ± {val_std:.4f} | — |",
        f"| Worst validation fold | {val_worst:.4f} | — |",
        f"| Expected calibration error, hold-out | {ece:.4f} | ≤ 0.05 |",
        f"| Worst serving category, recall | {worst_class:.4f} | ≥ 0.45 |",
        f"| Mean top-1 confidence | {confidence:.4f} | — |",
        f"| Auto-accept share at τ = 0.6 | {auto_accept:.1%} | target ≥ 60% (design 3.1) |",
    ]
    return "\n".join(rows + extra)


def build_lineage(client: MlflowClient, runs: dict) -> str:
    try:
        version = client.get_model_version_by_alias(REGISTERED_MODEL, "champion")
    except Exception:  # noqa: BLE001 - the registry may simply be empty
        return "_No registered `@champion` yet — run `make train`._"
    tags = dict(version.tags)
    tier1 = runs.get("tier1-tfidf-linear-svm")
    return "\n".join(
        [
            f"`{REGISTERED_MODEL}` version **{version.version}**, alias **`@champion`**:",
            "",
            "| | |",
            "| --- | --- |",
            f"| `data_version` | `{tags.get('data_version', '—')}` |",
            f"| `git_commit` | `{tags.get('git_commit', '—')}` |",
            f"| `feature_version` | `{tags.get('feature_version', '—')}` |",
            f"| source run | `{tier1.info.run_id if tier1 else version.run_id}` |",
            f"| recorded hold-out macro-F1 | {tags.get('macro_f1_holdout', '—')} |",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="patch the README in place")
    args = parser.parse_args(argv)

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    client = MlflowClient()
    runs = latest_runs(client)

    block = build_table(runs) + "\n\n" + build_lineage(client, runs)
    print(block)

    if args.write:
        for target in TARGETS:
            text = target.read_text(encoding="utf-8")
            patched = re.sub(
                r"(<!-- RESULTS:START -->\n).*?(\n<!-- RESULTS:END -->)",
                lambda m: m.group(1) + block + m.group(2),
                text,
                flags=re.DOTALL,
            )
            if patched == text:
                print(f"\n(no RESULTS markers in {target.name} — nothing written)", file=sys.stderr)
            else:
                target.write_text(patched, encoding="utf-8")
                print(f"wrote the block into {target.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
