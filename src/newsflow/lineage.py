"""The two tags without which a registered model is a file nobody can explain.

`data_version` — the DVC hash of the raw archive the run read.
`git_commit`   — the commit of the code that read it.

Both are read from the repository, not passed in by hand, so a run cannot be stamped
with a version it did not use. Every MLflow run and every registered model version
carries both (design 6.1, mark 1).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from newsflow.config import PROJECT_ROOT, RAW_ARCHIVE


def data_version(dvc_file: Path | None = None) -> str:
    """The DVC md5 of the tracked raw archive, read from its .dvc pointer file.

    This is the hash DVC itself computed and committed to Git; it is not recomputed
    here, so the tag names the version the remote holds rather than whatever happens
    to be on this disk. `newsflow.reproduce` checks the two against each other.
    """
    pointer = dvc_file or Path(f"{RAW_ARCHIVE}.dvc")
    if not pointer.exists():
        return "unversioned"
    with pointer.open(encoding="utf-8") as handle:
        meta = yaml.safe_load(handle)
    outs = meta.get("outs") or []
    if not outs:
        return "unversioned"
    return str(outs[0].get("md5") or outs[0].get("hash") or "unversioned")


def _git(*args: str) -> str:
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *args],  # noqa: S607
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def git_commit() -> str:
    """The commit the code came from, marked `-dirty` when the tree has changes.

    A dirty marker is deliberate: a run whose code is not committed is not
    reproducible, and the tag should say so rather than name a commit that does not
    describe the code that ran.
    """
    sha = _git("rev-parse", "HEAD") or "unknown"
    if sha != "unknown" and _git("status", "--porcelain"):
        sha = f"{sha}-dirty"
    return sha


def tags() -> dict[str, str]:
    """The lineage tag set, stamped on runs, on registered versions and on rows."""
    from newsflow.etl.features import feature_version  # local import: avoids a cycle

    return {
        "data_version": data_version(),
        "git_commit": git_commit(),
        "feature_version": feature_version(),
    }


def as_json() -> str:
    return json.dumps(tags(), sort_keys=True)
