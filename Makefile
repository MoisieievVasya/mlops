# newsflow - KSE MLOps course project
# Lab 1 needs only `setup` and `lint`; later labs add targets above this line.

.DEFAULT_GOAL := help
.PHONY: help setup lint format hooks clean

help: ## Show the available targets
	@grep -E "^[a-zA-Z_-]+:.*?## .*$$" $(MAKEFILE_LIST) | awk -F":.*?## " "{printf \"  %-10s %s\\n\", \$$1, \$$2}"

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

clean: ## Remove caches and the virtualenv
	rm -rf .venv .ruff_cache .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
