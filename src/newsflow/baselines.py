"""The tier-0 baselines — the floor that makes any other number readable.

Design 5.3 puts two of them in the repository on purpose:

* **majority** — always predict the largest class. Expected macro-F1 ≈ 0.02 across
  17 categories. Its only job is to make "0.6 macro-F1" mean something.
* **rule** — a hand-written keyword lexicon, first match wins, read from
  conf/rules.yaml. This is the honest "do we need ML at all" check *and* the
  documented fallback if the model service is down for a long outage.

Both implement the sklearn estimator surface so that the same evaluation code scores
them and the model, and so that neither is accidentally evaluated on a different
split from the one the model saw. If the rule beats the model, the README says so:
a documented honest baseline is worth more than a tuned model whose split leaked.
"""

from __future__ import annotations

import functools
import re

import numpy as np
import pandas as pd
import yaml
from sklearn.base import BaseEstimator, ClassifierMixin

from newsflow.config import RULES_PATH
from newsflow.dataset import TEXT_COLUMN


@functools.lru_cache(maxsize=1)
def _lexicon() -> list[tuple[str, re.Pattern[str]]]:
    """(category, compiled alternation) in file order — first match wins."""
    with RULES_PATH.open(encoding="utf-8") as handle:
        spec = yaml.safe_load(handle)
    compiled = []
    for category, terms in spec["categories"].items():
        alternation = "|".join(re.escape(str(term).lower()) for term in terms)
        compiled.append((category, re.compile(rf"(?<!\w)({alternation})(?!\w)")))
    return compiled


class MajorityBaseline(ClassifierMixin, BaseEstimator):
    """Always the training set's most frequent serving category."""

    def fit(self, X: pd.DataFrame, y: pd.Series) -> MajorityBaseline:  # noqa: N803
        self.classes_ = np.array(sorted(pd.Series(y).unique()))
        self.majority_ = pd.Series(y).value_counts().idxmax()
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:  # noqa: N803
        return np.full(len(X), self.majority_, dtype=object)


class RuleBaseline(ClassifierMixin, BaseEstimator):
    """Keyword lexicon over the normalised headline + dek, first match wins.

    Reads the same `model_text` column the vectoriser reads, so the two tiers are
    scored on identical input and a difference between them is a difference in the
    method, not in the preprocessing.
    """

    def fit(self, X: pd.DataFrame, y: pd.Series) -> RuleBaseline:  # noqa: N803
        self.classes_ = np.array(sorted(pd.Series(y).unique()))
        # The fallback is the training majority, so an article matching no term is
        # handled the same way the trivial baseline handles every article.
        self.fallback_ = pd.Series(y).value_counts().idxmax()
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:  # noqa: N803
        lexicon = _lexicon()
        out = []
        for text in X[TEXT_COLUMN]:
            label = self.fallback_
            for category, pattern in lexicon:
                if pattern.search(text):
                    label = category
                    break
            out.append(label)
        return np.array(out, dtype=object)

    def coverage(self, X: pd.DataFrame) -> float:  # noqa: N803
        """Share of rows that matched a term rather than falling back.

        Reported alongside the rule's macro-F1: a rule that answers 20% of the
        traffic and guesses the rest is a different artefact from one that answers
        most of it, and the aggregate metric hides which of the two you have.
        """
        lexicon = _lexicon()
        hits = sum(any(p.search(t) for _, p in lexicon) for t in X[TEXT_COLUMN])
        return hits / len(X) if len(X) else 0.0
