"""Rebuild a registered model version from its own lineage tags.

    make reproduce VERSION=champion
    make reproduce VERSION=3

This is the command the lab asks a reviewer to run, and it is the whole point of the
two tags. Given nothing but an alias or a version number it:

1. resolves the version in the registry and reads `data_version`, `git_commit` and
   `feature_version` off it;
2. checks the raw archive on this disk against `data_version`, and if it differs,
   restores the `.dvc` pointer from the recorded commit and pulls that version from
   the remote — the data is fetched by its hash, not assumed;
3. warns if the working tree is a different commit from the one that trained it,
   naming the commit to check out;
4. retrains the same pipeline on the same split and scores the same hold-out period;
5. compares the hold-out macro-F1 with the value recorded on the version, and fails
   if the difference is outside the tolerance the README states.

It deliberately does **not** re-run the rolling-origin validation: a reproduction has
to repeat the fit that produced the artifact, not the search that selected it.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import mlflow
from mlflow.tracking import MlflowClient

from newsflow import dataset, lineage, train
from newsflow.config import (
    MLFLOW_EXPERIMENT,
    MLFLOW_TRACKING_URI,
    PROJECT_ROOT,
    RAW_ARCHIVE,
    REGISTERED_MODEL,
)

#: The band the README quotes. Two students on two laptops reproduce the same split
#: and the same seed, so the only sources of drift are BLAS/threading differences in
#: the linear solver. 0.005 absolute macro-F1 covers that and nothing larger.
TOLERANCE = 0.005


def resolve(version: str) -> object:
    client = MlflowClient()
    if version.isdigit():
        return client.get_model_version(REGISTERED_MODEL, version)
    return client.get_model_version_by_alias(REGISTERED_MODEL, version)


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        args, cwd=PROJECT_ROOT, capture_output=True, text=True, check=False, timeout=600
    )


def restore_data_version(expected: str) -> None:
    """Make the archive on this disk the archive the run read, or say why not."""
    actual = lineage.data_version()
    if actual == expected:
        print(f"  data_version   {actual}  (already on disk)")
        return

    print(f"  data_version   {actual} on disk, {expected} wanted — restoring")
    pointer = Path(f"{RAW_ARCHIVE}.dvc").relative_to(PROJECT_ROOT)
    commit = _run("git", "log", "--format=%H", "-S", expected, "--", str(pointer)).stdout.split()
    if commit:
        print(f"    the pointer naming it was committed in {commit[0][:12]}")
        _run("git", "checkout", commit[0], "--", str(pointer))
    pull = _run("uv", "run", "dvc", "pull", str(pointer))
    if pull.returncode != 0:
        raise SystemExit(
            f"could not pull {expected} from the DVC remote:\n{pull.stderr.strip()}\n"
            "Is the stack up (`make up`) and are the credentials in .env?"
        )
    restored = lineage.data_version()
    if restored != expected:
        raise SystemExit(f"restored {restored}, wanted {expected} — the remote does not hold it")
    print(f"    pulled {expected} from the remote")


def check_commit(expected: str) -> None:
    actual = lineage.git_commit()
    if actual == expected:
        print(f"  git_commit     {actual[:12]}  (matches)")
        return
    print(f"  git_commit     {actual[:12]} here, {expected[:12]} trained it")
    if expected.endswith("-dirty"):
        print("    the registered version was trained from an uncommitted tree, so the")
        print("    code cannot be restored exactly. The metric check below still applies.")
    else:
        print(f"    for an exact reproduction:  git checkout {expected[:12]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="champion", help="registry version number or alias")
    parser.add_argument("--tolerance", type=float, default=TOLERANCE)
    args = parser.parse_args(argv)

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT)

    model_version = resolve(args.version)
    tags = dict(model_version.tags)
    recorded = tags.get("macro_f1_holdout")
    print(f"reproducing {REGISTERED_MODEL} v{model_version.version}")
    print(f"  source run     {model_version.run_id}")
    print(f"  recorded       macro-F1 {recorded}")

    if "data_version" not in tags or "git_commit" not in tags:
        raise SystemExit(
            "this version carries no lineage tags, so there is nothing to reproduce from. "
            "A registered model without data_version and git_commit is a file nobody can explain."
        )

    restore_data_version(tags["data_version"])
    check_commit(tags["git_commit"])

    expected_features = tags.get("feature_version")
    current_features = lineage.tags()["feature_version"]
    if expected_features and expected_features != current_features:
        print(f"  feature_version {current_features} here, {expected_features} trained it")
        print("    conf/features.yaml or conf/taxonomy.yaml has changed; the rows this")
        print("    run reads are not the rows that version was trained on.")
    else:
        print(f"  feature_version {current_features}  (matches)")

    print("\nretraining on the same split ...")
    frame = dataset.load()
    train_df, valid_df, holdout = dataset.time_split(frame, train.TRAIN_END, train.VALID_END)

    with mlflow.start_run(run_name=f"reproduce-v{model_version.version}"):
        mlflow.set_tags(
            lineage.tags()
            | {
                "reproduction_of": f"{REGISTERED_MODEL}/{model_version.version}",
                "source_run_id": model_version.run_id,
            }
        )
        result = train.fit_and_evaluate(train_df, valid_df, holdout)
        mlflow.log_metric("macro_f1_holdout", result["macro_f1"])
        mlflow.log_metric("ece_holdout", result["ece"])

    print("\n" + "=" * 60)
    print(f"  recorded    {float(recorded):.4f}" if recorded else "  recorded    (not tagged)")
    print(f"  reproduced  {result['macro_f1']:.4f}")
    if recorded is None:
        print("  no recorded metric to compare against")
        return 0
    delta = abs(result["macro_f1"] - float(recorded))
    print(f"  difference  {delta:.4f}   tolerance {args.tolerance}")
    print("=" * 60)
    if delta > args.tolerance:
        print("OUTSIDE TOLERANCE — this version did not reproduce.")
        return 1
    print("within tolerance — reproduced.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
