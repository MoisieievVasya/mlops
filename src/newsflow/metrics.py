"""The metrics of design 3.2 / 5.4, in one place so every tier is scored identically.

macro-F1 is the headline number *because* of the class imbalance: the head class is
~17% of the archive and several serving categories are under 2%, so the cheapest way
to raise an aggregate is to abandon a small category. macro-F1 plus a per-class recall
floor is what makes that trade visible instead of profitable (design R5).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import f1_score, recall_score


def macro_f1(y_true, y_pred) -> float:
    return float(f1_score(y_true, y_pred, average="macro", zero_division=0))


def per_class_recall(y_true, y_pred, labels: list[str]) -> pd.DataFrame:
    """The table the editorial lead signs a promotion off on (design 5.5)."""
    recalls = recall_score(y_true, y_pred, labels=labels, average=None, zero_division=0)
    support = pd.Series(y_true).value_counts().reindex(labels).fillna(0).astype(int)
    return pd.DataFrame(
        {"category": labels, "recall": np.round(recalls, 4), "support": support.to_numpy()}
    ).sort_values("recall")


def expected_calibration_error(
    y_true, probabilities: np.ndarray, classes, n_bins: int = 10
) -> float:
    """ECE on the top-1 confidence — gating in the promotion rule of 5.4.

    The product abstains below a confidence threshold, so a model whose confidences
    are wrong is a model whose abstention rate is wrong, whatever its accuracy.
    """
    confidence = probabilities.max(axis=1)
    predicted = np.asarray(classes)[probabilities.argmax(axis=1)]
    correct = (predicted == np.asarray(y_true)).astype(float)

    edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for low, high in zip(edges[:-1], edges[1:], strict=True):
        in_bin = (confidence > low) & (confidence <= high)
        if not in_bin.any():
            continue
        ece += in_bin.mean() * abs(correct[in_bin].mean() - confidence[in_bin].mean())
    return float(ece)
