"""Train the baselines and the model, track every run, register one with lineage.

    make train                  # the design's split: 12 rolling-origin folds
    make train TRAIN_ARGS="--folds 3"

Four runs land in the tracking server, all four carrying `data_version`,
`git_commit` and `feature_version` as tags:

    tier0-majority        always the largest class — the floor
    tier0-rule            the keyword lexicon — the "do we need ML" check
    tier1-tfidf-linear-svm the production model; this one is registered
    tier1-no-prior        the R6 leakage check: the same model without
                          author_desk_prior, to see how much that feature is worth

The hold-out period is touched once, at the end, by all four. The rolling-origin
folds run on the validation period only (design 5.4).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import mlflow
import pandas as pd
from mlflow.models import infer_signature
from mlflow.tracking import MlflowClient

from newsflow import dataset, lineage
from newsflow.baselines import MajorityBaseline, RuleBaseline
from newsflow.config import MLFLOW_EXPERIMENT, MLFLOW_TRACKING_URI, REGISTERED_MODEL
from newsflow.metrics import expected_calibration_error, macro_f1, per_class_recall
from newsflow.model import HYPERPARAMETERS, build_pipeline, calibrate
from newsflow.taxonomy import serving_categories, taxonomy_version

#: Design 5.4, revised in Lab 2 against the measured archive. Train < 2017-01;
#: validate across 2017 in 12 rolling-origin folds; hold out 2018-01 onwards and
#: touch it once. The cut moved because the archive's volume collapses tenfold at
#: 2018-06 (1,411 rows in May, 143 in June) and the Lab 1 cut would have left 1,398
#: hold-out rows across 17 classes. The structure — time-ordered, monthly folds, the
#: most recent period held out once — is unchanged; only the dates are.
TRAIN_END = date(2017, 1, 1)
VALID_END = date(2018, 1, 1)
N_FOLDS = 12

#: The share of the training window used to fit the probability calibration, taken
#: from its *end* so the calibration sees the most recent data available to it.
CALIBRATION_TAIL = 0.1


def _log_common(frame_window: dict[str, Any]) -> None:
    mlflow.set_tags(lineage.tags())
    mlflow.set_tag("taxonomy_version", taxonomy_version())
    mlflow.log_params(frame_window)


def _artifact(name: str, body: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name
        path.write_text(body, encoding="utf-8")
        mlflow.log_artifact(str(path))


def _window_params(train: pd.DataFrame, valid: pd.DataFrame, holdout: pd.DataFrame) -> dict:
    return {
        "train_rows": len(train),
        "valid_rows": len(valid),
        "holdout_rows": len(holdout),
        "train_end": str(TRAIN_END),
        "valid_end": str(VALID_END),
        "train_first_month": str(train["event_ts"].min().date()),
        "holdout_last_month": str(holdout["event_ts"].max().date()),
        "n_classes": len(serving_categories()),
    }


def run_majority(train, holdout, windows) -> float:
    with mlflow.start_run(run_name="tier0-majority"):
        _log_common(windows)
        mlflow.set_tag("tier", "0-trivial")
        model = MajorityBaseline().fit(train, train[dataset.LABEL_COLUMN])
        predicted = model.predict(holdout)
        score = macro_f1(holdout[dataset.LABEL_COLUMN], predicted)
        mlflow.log_param("majority_class", model.majority_)
        mlflow.log_metric("macro_f1_holdout", score)
        print(f"  tier0-majority       macro-F1 = {score:.4f}  (always {model.majority_})")
        return score


def run_rule(train, holdout, windows) -> float:
    with mlflow.start_run(run_name="tier0-rule"):
        _log_common(windows)
        mlflow.set_tag("tier", "0-rule")
        model = RuleBaseline().fit(train, train[dataset.LABEL_COLUMN])
        predicted = model.predict(holdout)
        score = macro_f1(holdout[dataset.LABEL_COLUMN], predicted)
        coverage = model.coverage(holdout)
        mlflow.log_metric("macro_f1_holdout", score)
        mlflow.log_metric("rule_coverage_holdout", coverage)
        recall = per_class_recall(holdout[dataset.LABEL_COLUMN], predicted, serving_categories())
        _artifact("per_class_recall_rule.csv", recall.to_csv(index=False))
        print(f"  tier0-rule           macro-F1 = {score:.4f}  (matched {coverage:.1%} of rows)")
        return score


def run_rolling_origin(frame, n_folds: int) -> pd.DataFrame:
    """Train on everything strictly before month m, score m. Mean and spread."""
    folds = dataset.rolling_origin_folds(frame, TRAIN_END, n_folds)
    rows = []
    for index, (fold_train, fold_test, label) in enumerate(folds, start=1):
        pipeline = build_pipeline()
        pipeline.fit(fold_train, fold_train[dataset.LABEL_COLUMN])
        score = macro_f1(fold_test[dataset.LABEL_COLUMN], pipeline.predict(fold_test))
        # Logged as the fold completes, not in one batch at the end: a 12-fold
        # validation otherwise looks hung for a quarter of an hour.
        mlflow.log_metric("macro_f1_fold", score, step=int(label.replace("-", "")))
        rows.append(
            {
                "fold": label,
                "train_rows": len(fold_train),
                "test_rows": len(fold_test),
                "macro_f1": score,
            }
        )
        print(f"    fold {index:>2}/{len(folds)}  {label}  macro-F1 = {score:.4f}")
    return pd.DataFrame(rows)


def fit_and_evaluate(train, valid, holdout) -> dict[str, Any]:
    """The model that ships, and its hold-out numbers.

    Fitted on train + validation, calibrated on the tail of that window, evaluated
    once on the hold-out period. Factored out of `run_tier1` because
    `newsflow.reproduce` runs exactly this and nothing else: a reproduction has to
    repeat the fit that produced the registered artifact, not the rolling-origin
    validation that chose it.
    """
    fitted_window = pd.concat([train, valid]).sort_values("event_ts")
    cut = int(len(fitted_window) * (1 - CALIBRATION_TAIL))
    core, calibration = fitted_window.iloc[:cut], fitted_window.iloc[cut:]

    pipeline = build_pipeline()
    pipeline.fit(core, core[dataset.LABEL_COLUMN])
    model = calibrate(pipeline, calibration, calibration[dataset.LABEL_COLUMN])

    predicted = model.predict(holdout)
    probabilities = model.predict_proba(holdout)
    truth = holdout[dataset.LABEL_COLUMN]
    confidence = probabilities.max(axis=1)
    recall = per_class_recall(truth, predicted, serving_categories())

    return {
        "model": model,
        "macro_f1": macro_f1(truth, predicted),
        "ece": expected_calibration_error(truth, probabilities, model.classes_),
        "recall": recall,
        "min_class_recall": float(recall["recall"].min()),
        "mean_top1_confidence": float(confidence.mean()),
        "auto_accept_share": float((confidence >= 0.6).mean()),
        "calibration_rows": len(calibration),
    }


def run_tier1(train, valid, holdout, windows, *, n_folds: int, register: bool) -> dict[str, Any]:
    with mlflow.start_run(run_name="tier1-tfidf-linear-svm") as active:
        _log_common(windows)
        mlflow.set_tag("tier", "1-baseline")
        mlflow.log_params({k: str(v) for k, v in HYPERPARAMETERS.items()} | {"n_folds": n_folds})

        print("  tier1: rolling-origin validation")
        fold_table = run_rolling_origin(pd.concat([train, valid]), n_folds)
        mlflow.log_metric("macro_f1_val_mean", float(fold_table["macro_f1"].mean()))
        mlflow.log_metric("macro_f1_val_std", float(fold_table["macro_f1"].std()))
        mlflow.log_metric("macro_f1_val_worst", float(fold_table["macro_f1"].min()))
        _artifact("rolling_origin_folds.csv", fold_table.to_csv(index=False))

        print("  tier1: final fit on train + validation")
        evaluated = fit_and_evaluate(train, valid, holdout)
        model, score, ece, recall = (
            evaluated["model"],
            evaluated["macro_f1"],
            evaluated["ece"],
            evaluated["recall"],
        )

        mlflow.log_metrics(
            {
                "macro_f1_holdout": score,
                "ece_holdout": ece,
                "min_class_recall_holdout": evaluated["min_class_recall"],
                "mean_top1_confidence": evaluated["mean_top1_confidence"],
                # Design 3.1: the product's number is the share of articles an editor
                # never has to touch. tau = 0.6 is the Lab 2 placeholder; Lab 4 tunes it.
                "auto_accept_share_at_tau_0.6": evaluated["auto_accept_share"],
            }
        )
        _artifact("per_class_recall_tier1.csv", recall.to_csv(index=False))
        _artifact(
            "holdout_summary.json",
            json.dumps(
                {
                    "macro_f1": score,
                    "ece": ece,
                    "rows": len(holdout),
                    "period": [
                        str(holdout["event_ts"].min().date()),
                        str(holdout["event_ts"].max().date()),
                    ],
                    "worst_three_categories": recall.head(3).to_dict("records"),
                },
                indent=2,
            ),
        )

        example = holdout[dataset.FEATURE_COLUMNS].head(3)
        signature = infer_signature(example, model.predict(example))
        logged = mlflow.sklearn.log_model(
            sk_model=model,
            name="model",
            signature=signature,
            input_example=example,
            registered_model_name=REGISTERED_MODEL if register else None,
            # cloudpickle rather than MLflow 3's skops default: the estimator is
            # `newsflow.model.PlattCalibrated`, a class of ours, and skops refuses to
            # serialise types outside its trusted list. Declaring it trusted would
            # say the same thing with more ceremony.
            serialization_format="cloudpickle",
        )
        print(f"  tier1-tfidf-linear-svm macro-F1 = {score:.4f}  ECE = {ece:.4f}")
        return {
            "run_id": active.info.run_id,
            "macro_f1": score,
            "ece": ece,
            "model_uri": logged.model_uri,
            "fold_table": fold_table,
            "recall": recall,
            "val_mean": float(fold_table["macro_f1"].mean()),
            "val_std": float(fold_table["macro_f1"].std()),
        }


def run_no_prior(train, valid, holdout, windows) -> float:
    """R6: how much of the score is `author_desk_prior` carrying?"""
    with mlflow.start_run(run_name="tier1-no-prior"):
        _log_common(windows)
        mlflow.set_tag("tier", "1-baseline")
        mlflow.set_tag("variant", "no_author_desk_prior")
        mlflow.log_params({k: str(v) for k, v in HYPERPARAMETERS.items()})
        pipeline = build_pipeline(use_prior=False)
        window = pd.concat([train, valid])
        pipeline.fit(window, window[dataset.LABEL_COLUMN])
        score = macro_f1(holdout[dataset.LABEL_COLUMN], pipeline.predict(holdout))
        mlflow.log_metric("macro_f1_holdout", score)
        print(f"  tier1-no-prior       macro-F1 = {score:.4f}")
        return score


def register_with_lineage(result: dict[str, Any], alias: str = "champion") -> int | None:
    """Tag the new version with the data and the code that produced it, then alias it.

    Without these two tags a registered model is a file nobody can explain: you can
    load it, and you cannot say which rows taught it what. With them, "version N is
    the champion" resolves to a DVC hash and a commit, and `make reproduce` turns
    that pair back into a training run.
    """
    client = MlflowClient()
    versions = client.search_model_versions(f"name = '{REGISTERED_MODEL}'")
    mine = [v for v in versions if v.run_id == result["run_id"]]
    if not mine:
        print("  (model was not registered — run with --register)")
        return None
    version = max(mine, key=lambda v: int(v.version))

    tags = lineage.tags()
    for key, value in tags.items():
        client.set_model_version_tag(REGISTERED_MODEL, version.version, key, value)
    client.set_model_version_tag(
        REGISTERED_MODEL, version.version, "macro_f1_holdout", f"{result['macro_f1']:.4f}"
    )
    client.set_model_version_tag(
        REGISTERED_MODEL, version.version, "registered_at", datetime.now().astimezone().isoformat()
    )
    client.set_registered_model_alias(REGISTERED_MODEL, alias, version.version)
    client.update_model_version(
        REGISTERED_MODEL,
        version.version,
        description=(
            f"TF-IDF (word 1-2 + char 3-5) -> LinearSVC, Platt-calibrated. "
            f"Hold-out macro-F1 {result['macro_f1']:.4f}, ECE {result['ece']:.4f}. "
            f"Data {tags['data_version'][:12]}, commit {tags['git_commit'][:12]}, "
            f"features {tags['feature_version']}."
        ),
    )
    print(f"  registered {REGISTERED_MODEL} v{version.version} @{alias}")
    print(f"    data_version    {tags['data_version']}")
    print(f"    git_commit      {tags['git_commit']}")
    print(f"    feature_version {tags['feature_version']}")
    return int(version.version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--folds", type=int, default=N_FOLDS, help="rolling-origin folds (design: 12)"
    )
    parser.add_argument(
        "--no-register", action="store_true", help="train and track, but do not register"
    )
    parser.add_argument("--alias", default="champion")
    args = parser.parse_args(argv)

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT)
    print(f"tracking server: {MLFLOW_TRACKING_URI}  experiment: {MLFLOW_EXPERIMENT}")

    frame = dataset.load()
    train, valid, holdout = dataset.time_split(frame, TRAIN_END, VALID_END)
    windows = _window_params(train, valid, holdout)
    print(
        f"rows: {len(frame):,}  train {len(train):,} | validation {len(valid):,} | "
        f"hold-out {len(holdout):,}\n"
    )

    majority = run_majority(train, holdout, windows)
    rule = run_rule(train, holdout, windows)
    tier1 = run_tier1(
        train, valid, holdout, windows, n_folds=args.folds, register=not args.no_register
    )
    no_prior = run_no_prior(train, valid, holdout, windows)

    delta = tier1["macro_f1"] - no_prior
    with mlflow.start_run(run_id=tier1["run_id"]):
        mlflow.log_metric("macro_f1_no_prior_holdout", no_prior)
        mlflow.log_metric("author_prior_contribution", delta)
        mlflow.log_metric("macro_f1_rule_holdout", rule)
        mlflow.log_metric("macro_f1_majority_holdout", majority)

    version = None
    if not args.no_register:
        version = register_with_lineage(tier1, alias=args.alias)

    print("\n" + "=" * 66)
    print(f"  {'majority (tier 0)':<26} {majority:.4f}")
    print(f"  {'rule lexicon (tier 0)':<26} {rule:.4f}")
    print(f"  {'TF-IDF -> LinearSVC (tier 1)':<26} {tier1['macro_f1']:.4f}   <- registered")
    print(f"  {'  without author prior':<26} {no_prior:.4f}   (R6 delta {delta:+.4f})")
    print(f"  {'validation mean / std':<26} {tier1['val_mean']:.4f} / {tier1['val_std']:.4f}")
    print("=" * 66)
    if rule > tier1["macro_f1"]:
        print("  The rule beats the model. Reported as-is — design 5.3 and the lab both")
        print("  prefer an honest baseline to a tuned model with a leaked split.")
    if abs(delta) >= 0.05:
        print(f"  R6 WARNING: the author prior moves macro-F1 by {delta:+.4f} (>= 0.05).")
        print("  Design R6 reads that as evidence of leakage, not of a good feature.")

    if version is not None:
        print(f"\n  reproduce it with:  make reproduce VERSION={version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
