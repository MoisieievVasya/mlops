"""The tier-1 model of design 5.3 — TF-IDF → linear SVM, with calibration.

Why this and not a transformer: it trains in minutes on a CPU, it is deterministic
under a pinned seed, every coefficient is inspectable per feature, and it meets the
300 ms p95 budget of 6.3 with room to spare. Design 5.3 calls it "a real production
model for this problem, not a stand-in", and the tier-2 candidate has to clear the
promotion gate *including latency* before it replaces it.

The pipeline takes a DataFrame whose columns are the columns `newsflow.dataset`
produces, which are the columns a serving request carries. The input schema logged to
MLflow comes from that frame, so a payload that does not match fails at the boundary.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression, SGDClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from newsflow.config import RANDOM_SEED
from newsflow.dataset import (
    CATEGORICAL_COLUMNS,
    NUMERIC_COLUMNS,
    PRIOR_COLUMNS,
    TEXT_COLUMN,
)

#: Logged as MLflow params, so the numbers in 7.2 can be traced to the settings.
HYPERPARAMETERS = {
    "word_ngram_range": (1, 2),
    "char_ngram_range": (3, 5),
    "min_df": 3,
    "sublinear_tf": True,
    # A linear SVM — hinge loss, L2 penalty — trained by stochastic gradient descent
    # rather than by liblinear's coordinate descent. Same model class and the same
    # kind of decision boundary; a different solver, and the reason is measured
    # rather than aesthetic. On one rolling-origin fold (162,335 rows, 466,804
    # features) liblinear did not finish in 13 minutes, which makes 12 folds
    # impossible on a laptop; SGD fits the same fold in 13 seconds at macro-F1
    # 0.6117. Design 5.3 names the solver for this reason.
    "loss": "hinge",
    "alpha": 1e-5,
    "class_weight": "balanced",
    # 15 epochs is a budget, not a convergence criterion, and sklearn says so with a
    # ConvergenceWarning on every fit. That is accepted here: 12 rolling-origin folds
    # have to fit inside a laptop's patience, the metric is flat past ~10 epochs, and
    # this is the tier-1 baseline whose job is to be beaten by tier 2 — not the place
    # to spend an hour chasing the fourth decimal.
    "max_iter": 15,
    "tol": 1e-3,
    "random_state": RANDOM_SEED,
}


def build_pipeline(*, use_prior: bool = True) -> Pipeline:
    """The estimator. `use_prior=False` is the R6 leakage check, not an option.

    Design R6 states the acceptance condition: removing `author_desk_prior` must move
    offline macro-F1 by less than ~0.05. A larger drop is evidence that the feature
    is carrying the label rather than carrying signal, and the point-in-time join is
    wrong somewhere.
    """
    transformers = [
        (
            "word",
            TfidfVectorizer(
                ngram_range=HYPERPARAMETERS["word_ngram_range"],
                min_df=HYPERPARAMETERS["min_df"],
                sublinear_tf=HYPERPARAMETERS["sublinear_tf"],
                strip_accents="unicode",
            ),
            TEXT_COLUMN,
        ),
        (
            "char",
            TfidfVectorizer(
                analyzer="char_wb",
                ngram_range=HYPERPARAMETERS["char_ngram_range"],
                min_df=HYPERPARAMETERS["min_df"],
                sublinear_tf=HYPERPARAMETERS["sublinear_tf"],
            ),
            TEXT_COLUMN,
        ),
        ("numeric", StandardScaler(), NUMERIC_COLUMNS),
        (
            "categorical",
            OneHotEncoder(handle_unknown="ignore", sparse_output=True),
            CATEGORICAL_COLUMNS,
        ),
    ]
    if use_prior:
        # Already a distribution in [0, 1]; scaling it would only blur the "no
        # history" row, which is an all-zero vector with a meaning of its own.
        transformers.append(("prior", "passthrough", PRIOR_COLUMNS))

    return Pipeline(
        [
            ("features", ColumnTransformer(transformers, sparse_threshold=1.0)),
            (
                "classifier",
                SGDClassifier(
                    loss=HYPERPARAMETERS["loss"],
                    alpha=HYPERPARAMETERS["alpha"],
                    class_weight=HYPERPARAMETERS["class_weight"],
                    max_iter=HYPERPARAMETERS["max_iter"],
                    tol=HYPERPARAMETERS["tol"],
                    random_state=HYPERPARAMETERS["random_state"],
                    n_jobs=-1,
                ),
            ),
        ]
    )


class PlattCalibrated(ClassifierMixin, BaseEstimator):
    """A fitted pipeline plus a logistic map from its margins to probabilities.

    Platt scaling, written out rather than delegated to `CalibratedClassifierCV`:
    with a frozen estimator that class still runs its own internal cross-validation,
    and its prediction-ordering step indexes by class *position* assuming integer
    labels, which raises on the string category labels used here. Doing it directly
    is six lines, removes a dependency on that behaviour, and makes the thing the
    design calls "Platt calibration on a held-out fold" literally visible: one
    multinomial logistic regression fitted on the margins of a held-out slice.
    """

    def __init__(self, pipeline: Pipeline):
        self.pipeline = pipeline

    def fit(self, X: pd.DataFrame, y: pd.Series) -> PlattCalibrated:  # noqa: N803
        margins = self.pipeline.decision_function(X)
        self.mapper_ = LogisticRegression(max_iter=1000)
        self.mapper_.fit(margins, y)
        self.classes_ = self.mapper_.classes_
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:  # noqa: N803
        return self.mapper_.predict_proba(self.pipeline.decision_function(X))

    def predict(self, X: pd.DataFrame) -> np.ndarray:  # noqa: N803
        return self.classes_[self.predict_proba(X).argmax(axis=1)]


def calibrate(
    fitted: Pipeline,
    calibration_frame: pd.DataFrame,
    calibration_labels: pd.Series,
) -> PlattCalibrated:
    """Platt scaling on a held-out slice — design 5.3, "calibration on a held-out fold".

    A held-out slice rather than internal cross-validation: the slice is the *latest*
    months of the training window, so the calibration is fitted on data closer in time
    to what will be scored, and it costs one extra fit rather than five.

    Hinge loss has no probabilities of its own, and the product needs them: the
    abstention threshold tau is a probability, so an uncalibrated margin would make
    the 40% abstention target meaningless.
    """
    return PlattCalibrated(fitted).fit(calibration_frame, calibration_labels)
