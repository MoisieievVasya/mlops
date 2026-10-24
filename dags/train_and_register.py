"""`train_and_register` — the training DAG of design 6.1, layer 4.

Two tasks, and the split between them is the point:

    train_and_register -> promotion_gate

`train_and_register` runs exactly the code `make train` runs, so a model trained by
the orchestrator and a model trained by hand are the same artifact produced the same
way. `promotion_gate` then reads the run's metrics back out of the tracking server and
checks them against the gate of design 5.4 — macro-F1 against the rule baseline, a
per-class recall floor, and the calibration bound.

**The gate does not move the alias.** Design 5.5 is explicit that promotion carries
the editorial lead's recorded approval on the per-class recall table, so a green
pipeline reports that a candidate is *eligible* and stops. A DAG that promoted by
itself would be a DAG that had quietly taken a product decision.

Scheduled `None`: Lab 2 triggers it by hand or from the CLI. Lab 4 wires the M8
retrain trigger of design 6.6 to it.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "/opt/project/src")

from airflow.sdk import Param, dag, get_current_context, task  # noqa: E402

DOC = __doc__

#: Design 5.4. A candidate that fails any of these is not eligible for promotion.
MIN_CLASS_RECALL = 0.45
MAX_ECE = 0.05


@dag(
    dag_id="train_and_register",
    description="Train the baselines and the model, track them, register with lineage",
    doc_md=DOC,
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 0},
    tags=["newsflow", "lab-2", "training"],
    params={
        "folds": Param(12, type="integer", title="Rolling-origin folds (design 5.4: 12)"),
        "register": Param(True, type="boolean", title="Register the tier-1 run in the registry"),
    },
)
def train_and_register() -> None:
    @task
    def train_task() -> dict:
        from newsflow import train

        params = get_current_context()["params"]
        argv = ["--folds", str(params["folds"])]
        if not params["register"]:
            argv.append("--no-register")
        if train.main(argv) != 0:
            raise RuntimeError("training failed")

        # The caller needs the run that produced the candidate, not the whole result.
        import mlflow

        from newsflow.config import MLFLOW_EXPERIMENT

        experiment = mlflow.get_experiment_by_name(MLFLOW_EXPERIMENT)
        runs = mlflow.search_runs(
            experiment_ids=[experiment.experiment_id],
            filter_string="attributes.run_name = 'tier1-tfidf-linear-svm'",
            order_by=["attributes.start_time DESC"],
            max_results=1,
        )
        return {"run_id": runs.iloc[0]["run_id"]}

    @task
    def promotion_gate(upstream: dict) -> dict:
        """Check the candidate against design 5.4. Report; never promote."""
        import mlflow

        run = mlflow.get_run(upstream["run_id"])
        metrics = run.data.metrics
        model_f1 = metrics["macro_f1_holdout"]
        rule_f1 = metrics.get("macro_f1_rule_holdout", 0.0)

        checks = [
            (
                "beats the rule baseline",
                model_f1 > rule_f1,
                f"{model_f1:.4f} vs {rule_f1:.4f}",
            ),
            (
                f"no serving category below {MIN_CLASS_RECALL} recall",
                metrics.get("min_class_recall_holdout", 0.0) >= MIN_CLASS_RECALL,
                f"worst class recall {metrics.get('min_class_recall_holdout', 0):.4f}",
            ),
            (
                f"calibration error at most {MAX_ECE}",
                metrics.get("ece_holdout", 1.0) <= MAX_ECE,
                f"ECE {metrics.get('ece_holdout', 1):.4f}",
            ),
        ]
        for name, passed, observed in checks:
            print(f"  [{'PASS' if passed else 'FAIL'}] {name}: {observed}")

        eligible = all(passed for _, passed, _ in checks)
        print(
            "\ncandidate is ELIGIBLE for promotion — awaiting the editorial lead's sign-off\n"
            "on the per-class recall table (design 5.5). The alias is not moved here."
            if eligible
            else "\ncandidate is NOT eligible. The champion keeps serving."
        )
        return {
            "eligible": eligible,
            "checks": {name: passed for name, passed, _ in checks},
            "run_id": upstream["run_id"],
        }

    promotion_gate(train_task())


train_and_register()
