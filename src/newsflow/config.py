"""Where things are, resolved once.

Everything that differs between the laptop and the Airflow container is an
environment variable with a laptop-shaped default, so that the same module works in
both places without a branch. Nothing here reads a secret from a file in the repo.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Repository root. Mounted at /opt/project inside the Airflow containers.
PROJECT_ROOT = Path(os.environ.get("NEWSFLOW_PROJECT_ROOT", Path(__file__).resolve().parents[2]))

CONF_DIR = PROJECT_ROOT / "conf"
SQL_DIR = PROJECT_ROOT / "sql"
DATA_DIR = PROJECT_ROOT / "data"

TAXONOMY_PATH = CONF_DIR / "taxonomy.yaml"
FEATURES_PATH = CONF_DIR / "features.yaml"
QUALITY_PATH = CONF_DIR / "quality_contract.yaml"
RULES_PATH = CONF_DIR / "rules.yaml"

#: The DVC-tracked archive. `data/raw/news_category_v3.json.dvc` is what Git holds.
RAW_ARCHIVE = DATA_DIR / "raw" / "news_category_v3.json"

#: Warehouse. postgres:5432 inside the compose network, localhost:5433 outside.
DSN = os.environ.get(
    "NEWSFLOW_DSN",
    "postgresql://newsflow:newsflow-local-dev@localhost:5433/newsflow",
)

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5001")
MLFLOW_EXPERIMENT = os.environ.get("NEWSFLOW_EXPERIMENT", "newsflow-category-router")
REGISTERED_MODEL = os.environ.get("NEWSFLOW_REGISTERED_MODEL", "newsflow-category-router")

#: Salt for the byline hash (design 6.5). A secret in every environment; the default
#: exists so that a clean clone runs, and .env.example says to change it.
AUTHOR_SALT = os.environ.get("NEWSFLOW_AUTHOR_SALT", "newsflow-local-salt")

#: One publisher in the archive (design R2). Becomes a real column with a second feed.
SOURCE_FEED_ID = "huffpost"

#: Pinned everywhere a split, a shuffle or an initialiser is involved.
RANDOM_SEED = 20261026
