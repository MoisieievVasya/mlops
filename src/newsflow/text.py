"""The one text transform, imported by the training path and the serving path.

Design 5.3 names this module as "the only structural defence against skew": a feature
can only differ between training and serving if the two paths compute it with
different code, so there is one copy of the code and both paths import it.

Nothing here reads the database, the config or the clock. It is a pure function of
its arguments, which is what makes `headline_norm` reproducible from a raw row.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

#: The explicit token for a missing dek. 9.4% of the archive has none, and an absent
#: dek is a real state of the CMS draft, so it is encoded rather than imputed.
NO_DEK = "<no_dek>"

_WS = re.compile(r"\s+")
_URL = re.compile(r"https?://\S+")


def normalise(value: str | None) -> str:
    """NFKC-fold, strip URLs and collapse whitespace. Lowercase.

    The same four lines run over an archive headline and over a CMS draft title.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = _URL.sub(" ", text)
    text = text.replace(" ", " ")
    return _WS.sub(" ", text).strip().lower()


def normalise_dek(value: str | None) -> str:
    """A dek that is absent, empty or whitespace becomes the explicit NO_DEK token."""
    normalised = normalise(value)
    return normalised if normalised else NO_DEK


def model_text(headline_norm: str, dek_norm: str) -> str:
    """The single string the vectoriser sees. One definition, used by both paths."""
    return f"{headline_norm} {dek_norm}".strip()


def word_count(headline_norm: str, dek_norm: str) -> int:
    """Length in words of the model text, with the NO_DEK token not counted."""
    text = model_text(headline_norm, dek_norm).replace(NO_DEK, " ")
    return len(text.split())


def hash_author(authors: str | None, salt: str) -> str:
    """Salted sha256 of the byline, truncated to 16 hex chars.

    Design 6.5: a journalist's byline is personal data even though it is published,
    so it exists in clear in the raw layer only. Everything downstream — the feature
    table, the feature store, the serving log, any prompt — sees this value. An empty
    byline is its own bucket rather than a null, because "no byline" is a real and
    reasonably predictive state (17.9% of the archive).
    """
    value = (authors or "").strip().lower()
    if not value:
        return "anon"
    digest = hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()
    return digest[:16]


def article_id(link: str | None, headline: str | None, published_at: str) -> str:
    """A stable id for a source row.

    The archive has no id field. The URL is the source's own key and is used as one
    here — and only here, in the raw layer: `link` never reaches dwh.articles, so
    this is not a back door for the leakage field (conf/features.yaml:dropped).
    Rows without a usable link fall back to the (date, headline) pair.
    """
    key = (link or "").strip()
    if not key:
        key = f"{published_at}|{(headline or '').strip()}"
    return hashlib.sha1(key.encode()).hexdigest()  # noqa: S324 - an id, not a MAC
