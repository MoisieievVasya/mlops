# newsflow

**Topic routing for an online news aggregator.** KSE MLOps course project, Labs 1–5.

## The problem, in five sentences

An online news aggregator receives roughly 2,000 articles a day from publisher feeds, and every
article must be placed into exactly one of 17 section categories before it can appear on a section
page or in a personalised feed. Today a human editor assigns that category to every single article,
which makes the editorial desk the bottleneck between an article arriving and an article being
visible. `newsflow` is a service that reads the headline and the dek of an incoming article and
returns a category together with a calibrated confidence, so that articles above a confidence
threshold are placed automatically and only the uncertain ones reach a human. The problem does not
end at a trained model, because the topics themselves move — a category mix from 2013 does not
describe 2022 — so the system has to notice when it is drifting, retrain on recent months, and prove
that the new model is better before it replaces the old one. The desired outcome is that editors
touch at most 40 articles in 100 instead of 100 in 100, while the share of articles placed in the
wrong section does not get worse than it is today.

The full design is in **[docs/design.md](docs/design.md)**.

## Quick start

```bash
make setup
```

```bash
make lint
```

| Target | What it does |
| --- | --- |
| `make setup` | Creates the pinned environment from `pyproject.toml` + `uv.lock` |
| `make lint` | Runs `ruff check` and `ruff format --check` — the same linter CI and pre-commit run |
| `make hooks` | Installs the pre-commit hooks into `.git/hooks` |
| `make format` | Applies the fixes `make lint` would complain about |

Requirements: [`uv`](https://docs.astral.sh/uv/) and Python 3.12 or later. Nothing else is needed for
Lab 1 — there is no model code yet, only the design document, the diagrams and the repository scaffold.

## Maturity scorecard

The table grows by a few rows each lab. Every row points at something a reviewer can reproduce from
this repository.

| Property | Evidence a reviewer checks | Added in | Status |
| --- | --- | --- | --- |
| Repository reproducible from a clean clone | CI is green; `make setup` and `make lint` succeed on a fresh clone | Lab 1 | ✅ |
| Design document exists and is current | `docs/design.md` follows the ml-design-docs template, every section filled or marked as an assumption | Lab 1 | ✅ |
| Data versioned | the documented pull command retrieves the raw data on a clean clone | Lab 2 | ⬜ |
| Pipeline idempotent | the same interval run twice gives identical row counts, both pasted in the README | Lab 2 | ⬜ |
| Quality gate present | one bad batch fails the run on purpose, shown | Lab 2 | ⬜ |
| Experiments tracked | every training run in the tracking server has parameters, metrics, data version and commit | Lab 2 | ⬜ |
| Model registered with lineage | a registered version tagged with data version and commit, reproducible by one command | Lab 2 | ⬜ |
| LLM component evaluated | an evaluation set with retrieval and answer metrics logged as runs | Lab 3 | ⬜ |
| Prompts versioned | prompt files whose hash appears on every LLM run | Lab 3 | ⬜ |
| Cost and latency recorded | tokens or compute, cost, p50 and p95 per run | Lab 3 | ⬜ |
| Services containerized | two images built from the repository, tagged with the commit | Lab 4 | ⬜ |
| Services deployed with health contracts | both services ready on Minikube, probes fail when the model is missing | Lab 4 | ⬜ |
| Monitoring scheduled | reports on a schedule, a dashboard with at least two windows | Lab 4 | ⬜ |
| Drift signal reaches a human | an injected shift crosses the threshold and the alert is received | Lab 4 | ⬜ |
| One command brings the system up | the documented command starts everything from a clean clone and answers a smoke request | Lab 4 | ⬜ |
| Agent traced and evaluated | persisted traces and a ten-task evaluation run twice | Lab 5 | ⬜ |

## Repository layout

```
.
├── docs/
│   └── design.md              # the design document and the three diagrams
├── src/newsflow/              # Lab 2 fills this
├── .github/workflows/lint.yml # CI: make setup + make lint on every push
├── .pre-commit-config.yaml    # the same ruff, from the same lock file
├── Makefile                   # setup, lint, format, hooks
├── pyproject.toml             # project and ruff configuration
├── uv.lock                    # the pinned environment
└── .python-version            # 3.12
```

## Licence and data attribution

Code: MIT. The dataset used from Lab 2 onwards is the **News Category Dataset** by Rishabh Misra
(Kaggle, CC BY 4.0) — see section 5.2 of the design document for how it is versioned and what is
held out.
