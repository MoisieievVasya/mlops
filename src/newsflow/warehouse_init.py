"""Apply the warehouse schema. Run by the airflow-init service on every `make up`."""

from __future__ import annotations

import sys

from newsflow.warehouse import apply_schema


def main() -> int:
    apply_schema()
    print("newsflow: warehouse schema applied (raw, dwh, features)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
