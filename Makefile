# newsflow - KSE MLOps course project
# Lab 1 needs only `setup` and `lint`; Lab 2 adds the stack, the data and the model.

.DEFAULT_GOAL := help
SHELL := /bin/bash

# Everything that talks to the stack reads .env, so a port change is a one-file edit.
ifneq (,$(wildcard .env))
include .env
export
endif

COMPOSE := docker compose
AIRFLOW := $(COMPOSE) exec -T airflow-scheduler airflow

.PHONY: help setup lint format hooks clean \
        up down nuke logs ps init-stack \
        data data-push data-pull \
        backfill ingest rerun bad-batch counts \
        train reproduce feast-demo report \
        test

help: ## Show the available targets
	@grep -E "^[a-zA-Z_-]+:.*?## .*$$" $(MAKEFILE_LIST) | awk -F":.*?## " "{printf \"  %-14s %s\\n\", \$$1, \$$2}"

# ----------------------------------------------------------------- environment ---

setup: ## Create the pinned environment from pyproject.toml + uv.lock
	uv sync --frozen

lint: ## Run the linter and the format check
	uv run ruff check .
	uv run ruff format --check .

format: ## Apply the linter fixes and the formatter
	uv run ruff check --fix .
	uv run ruff format .

hooks: ## Install the pre-commit hooks into .git/hooks
	uv run pre-commit install

test: ## Run the test suite (contract tests over the config and the transforms)
	uv run pytest -q

clean: ## Remove caches and the virtualenv
	rm -rf .venv .ruff_cache .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

# ----------------------------------------------------------------------- stack ---

up: .env ## Start the whole local stack and wait for it to be healthy
	$(COMPOSE) up -d --wait
	@echo
	@echo "  Airflow  http://localhost:$${AIRFLOW_PORT:-8080}"
	@echo "  MLflow   http://localhost:$${MLFLOW_PORT:-5001}"
	@echo "  MinIO    http://localhost:$${MINIO_CONSOLE_PORT:-9101}  ($${MINIO_ROOT_USER})"

down: ## Stop the stack, keeping the volumes
	$(COMPOSE) down

nuke: ## Stop the stack and delete every volume (warehouse, artifacts, DVC remote)
	$(COMPOSE) down -v

ps: ## Show what is running
	$(COMPOSE) ps

logs: ## Tail the logs of every service
	$(COMPOSE) logs -f --tail=100

.env:
	cp .env.example .env
	@echo "created .env from .env.example - review the credentials before anything public"

# ------------------------------------------------------------------------ data ---

data: ## Fetch the raw archive on a clean clone (DVC remote first, source second)
	./scripts/fetch_raw.sh

data-push: ## Push the tracked raw data to the DVC remote on MinIO
	uv run dvc push

data-pull: ## Pull the tracked raw data from the DVC remote on MinIO
	uv run dvc pull

# -------------------------------------------------------------------- pipeline ---

# One month. Usage:  make ingest MONTH=2022-03
MONTH ?= 2022-03
FROM  ?= 2021-01
TO    ?= 2022-09

ingest: ## Run ingest_month for one interval: make ingest MONTH=2022-03
	$(AIRFLOW) dags backfill --from-date $(MONTH)-01 --to-date $(MONTH)-01 \
	  --run-backwards=false --reprocess-behaviour=completed ingest_month

backfill: ## Replay a range of months: make backfill FROM=2021-01 TO=2022-09
	$(AIRFLOW) dags backfill --from-date $(FROM)-01 --to-date $(TO)-01 \
	  --reprocess-behaviour=completed ingest_month

rerun: ## Run the SAME interval a second time and print both row counts (idempotency)
	./scripts/prove_idempotency.sh $(MONTH)

bad-batch: ## Break one batch on purpose and watch the gate reject it
	./scripts/break_a_batch.sh $(MONTH) $${FAULT:-unmapped_label}

counts: ## Row counts per layer and the run ledger
	./scripts/counts.sh $(MONTH)

# -------------------------------------------------------------------- modelling ---

train: ## Train the baselines + the model, log to MLflow, register with lineage
	uv run python -m newsflow.train

# make reproduce VERSION=1   (or VERSION=champion)
VERSION ?= champion

reproduce: ## Rebuild a registered model version from its own lineage tags
	uv run python -m newsflow.reproduce --version $(VERSION)

feast-demo: ## Point-in-time retrieval: an entity timestamp between two feature rows
	NEWSFLOW_PG_HOST=localhost NEWSFLOW_PG_PORT=$${POSTGRES_HOST_PORT:-5433} \
	  uv run --extra feast python -m newsflow.feast_demo

report: ## Regenerate the results tables in the README from the tracking server
	uv run python scripts/report_results.py --write
