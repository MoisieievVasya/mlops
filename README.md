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

---

## Quick start

Requirements: [`uv`](https://docs.astral.sh/uv/), Python 3.12+, Docker with Compose v2, and ~6 GB of
free disk for the images.

```bash
make setup && make up
```

`make up` creates `.env` from `.env.example` if you have none, builds the images and waits for the
whole stack to be healthy. It prints the three URLs when it is done.

| Service | URL | What it is |
| --- | --- | --- |
| Airflow | http://localhost:8080 | the `ingest_month` and `train_and_register` DAGs |
| MLflow | http://localhost:5001 | runs, metrics, artifacts, the model registry |
| Object store | http://localhost:9101 | the DVC remote, MLflow artifacts, the Feast export |

Then, in order:

```bash
make data
```

```bash
make backfill FROM=2012-01 TO=2022-09
```

```bash
make train
```

```bash
make reproduce VERSION=champion
```

`make help` lists every target. Ports come from `.env`; change them there if something on your
machine already uses 8080 or 5001.

---

## What Lab 2 added

Lab 1 left a design document and an empty repository. Lab 2 makes layers 1–5 of the design's data
flow real, and makes each of them something a reviewer can check rather than read about.

| Property | How to check it yourself |
| --- | --- |
| The raw data is versioned, with a remote | `make data` on a clean clone; the checksum is below |
| The batch is the data interval, never the clock | trigger any past month and read the row dates |
| Running the same interval twice does not duplicate rows | `make rerun MONTH=2022-03` |
| One bad batch fails the run on purpose | `make bad-batch MONTH=2022-03` |
| Every feature has a declared serving-time source | `conf/features.yaml`, enforced by a test |
| The split is time-based and leakage-free | `docs/design.md` §5.4 |
| Every run carries its data version and its commit | any run in MLflow |
| A registered model can be rebuilt from its own tags | `make reproduce VERSION=champion` |

---

## 1 · The raw data, versioned

The dataset is the **News Category Dataset v3** (Rishabh Misra, Kaggle, CC BY 4.0): 209,527
HuffPost articles, 2012-01-28 → 2022-09-23, 83.3 MB of JSON Lines. It is **not in Git**. Git holds
`data/raw/news_category_v3.json.dvc`, which names its hash; the bytes live in the DVC remote, an
S3-compatible bucket (`s3://newsflow-dvc`) served by the object store in `docker-compose.yml`.

**The exact command that retrieves it on a clean clone**, and what should arrive:

```bash
make data
```

```
md5      bcd4791916daeac3fa5dc76786dd8bd5
expected bcd4791916daeac3fa5dc76786dd8bd5
rows     209527
ok
```

`make data` pulls from the DVC remote first (`dvc pull`) and falls back to the public mirror if you
have cloned the repository but have no credentials for our bucket. Either way it verifies the
checksum and refuses to continue on a mismatch, so "I got the data" and "I got *this* data" are the
same statement. At 83 MB the archive is under the 200 MB limit and is used whole — no sampling.

The remote's configuration is committed (`.dvc/config`: bucket and endpoint); its credentials are
not (`.dvc/config.local` and `.env`, both gitignored). The md5 above is the **`data_version`** that
appears on every warehouse row, every MLflow run and every registered model version.

---

## 2 · The pipeline

```
ingest_month:  extract → validate → transform → load → build_features → quality_check
```

One run owns one calendar month. **The batch is the run's data interval**, read from
`data_interval_start` / `data_interval_end` and matched against each article's own `date` field.
No task derives its window from the clock — the one `datetime.now()` in the DAG stamps the run
ledger with *when this attempt ran*, which is a record about the execution and never an input to
what it selects.

**On the schedule.** Airflow 3 changed the default so that a plain cron schedule produces runs whose
`data_interval_start` and `data_interval_end` are the same instant — which would leave the tasks with
no window to filter on. This project therefore declares the interval explicitly:

```python
schedule = CronDataIntervalTimetable("@monthly", timezone="UTC")
```

so the run triggered at 2015-04-01 owns `[2015-03-01, 2015-04-01)`. The DAG raises immediately if
the two bounds are ever equal, rather than silently ingesting nothing.

### Rerunning the same interval does not duplicate rows

The `load` task replaces the month's partition inside one transaction:

```sql
DELETE FROM dwh.articles WHERE batch_month = %s;
INSERT INTO dwh.articles (...) VALUES %s;
```

Delete-then-insert rather than an upsert, because an upsert is only idempotent when the second run
produces a superset of the first run's rows. If a batch shrinks, an upsert leaves the surplus behind
and the month quietly holds a mixture of two runs.

**Shown, not claimed.** `make rerun MONTH=2022-03` runs the same interval twice and prints both:

```
--- first run: running ingest_month for 2022-03 ---
    run state: success
    raw | dwh | features  =  174 | 174 | 174
--- second run, identical interval: running ingest_month for 2022-03 ---
    run state: success
    raw | dwh | features  =  174 | 174 | 174

first  : 174 | 174 | 174
second : 174 | 174 | 174
IDENTICAL - the load is idempotent.
```

The run ledger `dwh.batch_runs` keeps both executions — it is append-only, because Airflow reuses a
backfill's run id when it reprocesses a date and an upsert would overwrite the evidence:

```
             dag_run_id              | status | rows_extracted | rows_loaded | rows_features
-------------------------------------+--------+----------------+-------------+---------------
 backfill__2022-04-01T00:00:00+00:00 | passed |            174 |         174 |           174
 backfill__2022-04-01T00:00:00+00:00 | passed |            174 |         174 |           174
```

### The quality gate fails for real

Ten checks, declared in [`conf/quality_contract.yaml`](conf/quality_contract.yaml) with the upstream
break each one exists to catch. Q1–Q3 run in `validate` (the input contract); Q4–Q10 run in
`quality_check` after the load. **A failure rolls the month back out of `dwh.articles` and
`features.article_features`** and records the rejection in the ledger — the batch is rejected, not
flagged. `raw.articles` keeps it exactly as delivered, so the failure can be diagnosed afterwards.

To break a batch on purpose, pick one of the four faults and run it. The fault corrupts the data
between the archive and the raw layer — it does not flip a flag the gate reads:

```bash
make bad-batch MONTH=2022-03 FAULT=unmapped_label
```

```
run state: failed

QUALITY GATE FAILED for 2022-03 — 1 check(s):
  Q4 every_raw_label_mapped: observed 1, expected 0 rows with label_category = UNMAPPED
  <- a raw label was observed that conf/taxonomy.yaml does not map
The batch was rejected and rolled back out of the warehouse:
{'features.article_features': 174, 'dwh.articles': 174}.

== the warehouse after the rejection ==
           layer           | rows
---------------------------+------
 raw.articles              |  174
 dwh.articles              |    0
 features.article_features |    0
```

| Fault | What it does to the data | Which check catches it |
| --- | --- | --- |
| `unmapped_label` | rewrites one row's category to `CRYPTO`, absent from the taxonomy | Q4 |
| `null_headline` | blanks headline and dek on 5% of the batch | Q7 |
| `duplicate_rows` | delivers the first ten rows twice | Q5 |
| `truncate_batch` | delivers ten rows instead of the month | Q8 |

Screenshots: [a failed `quality_check`](docs/screenshots/airflow-quality-gate-failed.png) ·
[a successful run graph](docs/screenshots/airflow-ingest-month-graph.png) ·
[the same run's task list](docs/screenshots/airflow-ingest-month-success.png) ·
[the backfill grid](docs/screenshots/airflow-backfill-grid.png).

### What the gate caught that we did not plant

The first full backfill rejected 14 consecutive months, 2014-05 → 2015-06, on Q6: the dek is
missing on 40–56% of articles in every one of them, against under 5% everywhere else. That is a real
episode in the archive, not a delivery failure. The threshold was **not** raised — in production that
share means the CMS has stopped sending a field, which is an alert, not a tolerance. The decision was
recorded as a **waiver**: one check, one named period, a reason, an approver, and what would change
the decision. Inside the window the ledger says `passed_with_waiver`; one month either side, Q6 is
unchanged; deleting the waiver re-fails those months. The reasoning is in `docs/design.md` §5.2.

---

## 3 · The feature table

`features.article_features` — one row per article, carrying **`feature_version`**, which is
`fv1.<sha256 of conf/features.yaml + conf/taxonomy.yaml>`. Editing the feature contract changes the
version, so rows built before and after an edit are distinguishable from the row itself.

**Every feature names where its value comes from at serving time**, in
[`conf/features.yaml`](conf/features.yaml), and a test fails CI if one does not. That file is read by
the pipeline, so it cannot drift from the code.

`author_desk_prior` — the distribution of an author's *previous* sections — is the feature that would
leak if it were built naively, and it is built with a point-in-time join:

```sql
FROM dwh.articles h
JOIN batch b ON h.author_hash = b.author_hash
            AND h.event_ts <  b.event_ts      -- strictly before, never <=
```

Optional, with Feast:

```bash
uv sync --extra feast && make feast-demo
```

The demo picks a byline, builds an entity dataframe whose timestamp falls **between** two of its
feature rows, and shows that the **earlier** row comes back — the state that was knowable at that
moment, not the state at the end of history.

---

## 4 · Baselines and the model

Evaluated on a time-based split (design §5.4), all four on the same hold-out period, touched once:

```
2012-01 ──────────── 2016-12 │ 2017-01 ─ 2017-12 │ 2018-01 ─────────── 2022-09
   TRAIN 162,381            │ VALIDATION         │ HELD OUT 17,257
                             │ 12 rolling-origin │ touched once
                             │ folds, 29,889     │
```

<!-- RESULTS:START -->
| Tier | Model | Hold-out macro-F1 | Design 5.3 expected | Note |
| --- | --- | --- | --- | --- |
| 0 | always the majority class | **0.0009** | ≈ 0.02 | predicts `HEALTH & WELLNESS`, the training window's largest class |
| 0 | hand-written keyword lexicon | **0.2614** | 0.25–0.35 | matched 66.8% of hold-out rows; the rest fall back to the majority |
| **1** | **TF-IDF → linear SVM (SGD), calibrated** | **0.4605** | 0.62–0.68 | registered as `@champion` |
| 1 | the same, without `author_desk_prior` | 0.4246 | — | R6 leakage check: the feature is worth +0.0359 |

| Secondary metric, tier 1 | Value | Gate (design 5.4) |
| --- | --- | --- |
| Rolling-origin validation, mean over 12 folds | 0.5848 | — |
| Rolling-origin validation, spread across folds | ± 0.0290 | — |
| Worst validation fold | 0.5368 | — |
| Expected calibration error, hold-out | 0.0414 | ≤ 0.05 |
| Worst serving category, recall | 0.0000 | ≥ 0.45 |
| Mean top-1 confidence | 0.7263 | — |
| Auto-accept share at τ = 0.6 | 68.8% | target ≥ 60% (design 3.1) |

`newsflow-category-router` version **2**, alias **`@champion`**:

| | |
| --- | --- |
| `data_version` | `bcd4791916daeac3fa5dc76786dd8bd5` |
| `git_commit` | `4c168e2bc9adb79a0f72af69dff0b7b2f05fe190` |
| `feature_version` | `fv1.54623f56` |
| source run | `d3b86b0722a4478ea77a08e84f9a2328` |
| recorded hold-out macro-F1 | 0.4605 |
<!-- RESULTS:END -->

**The model is not eligible for promotion under the design's own gate**, and that is the gate
working. `HOME & LIVING` scores 0.0000 recall across 125 hold-out articles and four more categories
sit under the 0.45 floor of design 5.4 — a macro-F1 of 0.46 bought by abandoning small classes,
which is risk R5 written down in Lab 1 and caught in Lab 2. The `@champion` alias exists because
Lab 2 requires a registered aliased version and Lab 4 needs one to load; it is not an editorial
promotion. The full reading, including why validation (0.5848) and hold-out (0.4605) differ, is in
[§7.2 of the design document](docs/design.md#72-experiment-results).


---

## 5 · Tracking, the registry and lineage

The tracking server is **not** a local directory. MLflow 3.16.1 runs from `docker-compose.yml`
with a **PostgreSQL backend store** and an **object-store artifact root** (`s3://newsflow-mlflow/`),
so the reviewer runs the same server with the same settings.

**Every run carries three tags**, read from the repository rather than passed by hand:

| Tag | Where it comes from |
| --- | --- |
| `data_version` | the DVC md5 in `data/raw/news_category_v3.json.dvc` |
| `git_commit` | `git rev-parse HEAD`, suffixed `-dirty` when the tree has uncommitted changes |
| `feature_version` | the hash of the feature contract that built the rows it read |

The tier-1 run is registered as **`newsflow-category-router`**, the version carries the same tags,
and it has the alias **`@champion`** — which is what Lab 4 will load by.

Screenshots: [the four runs](docs/screenshots/mlflow-runs.png) ·
[the registered version with its tags and alias](docs/screenshots/mlflow-registered-version.png).

### One command reproduces it

```bash
make reproduce VERSION=champion
```

It resolves the alias, reads `data_version` off the version, checks the archive on disk against it
and pulls that version from the remote if they differ, warns if the working tree is a different
commit, retrains the same pipeline on the same split, and compares the hold-out macro-F1 with the
value recorded on the version.

**Tolerance: ±0.005 absolute macro-F1.** The split, the seed and the data are pinned, so the only
expected source of difference is BLAS and thread-count variation in the linear solver. Anything
larger is a real difference and the command exits non-zero.

<!-- REPRODUCE:START -->
```
reproducing newsflow-category-router v2
  source run     <run id>
  recorded       macro-F1 0.4605
  data_version   bcd4791916daeac3fa5dc76786dd8bd5  (already on disk)
  git_commit     4c168e2bc9ad  (matches)
  feature_version fv1.54623f56  (matches)

retraining on the same split ...

============================================================
  recorded    0.4605
  reproduced  0.4605
  difference  0.0000   tolerance 0.005
============================================================
within tolerance — reproduced.
```

1 minute 50 seconds, and it logs itself as a run tagged `reproduction_of` so the reproduction sits
in the tracking server beside the thing it reproduced. It deliberately does **not** re-run the 12
rolling-origin folds: a reproduction repeats the fit that produced the artifact, not the search that
selected it.

Two versions are registered, and the pair is itself the evidence. `v1` was trained before this work
was committed, so its `git_commit` carries a `-dirty` suffix and names code that cannot be checked
out; `v2` was trained from the commit above and reproduces to the fourth decimal. The difference
between them is exactly what the tag is for.
<!-- REPRODUCE:END -->

---

## Maturity scorecard

The table grows by a few rows each lab. Every row points at something a reviewer can reproduce from
this repository.

| Property | Evidence a reviewer checks | Added in | Status |
| --- | --- | --- | --- |
| Repository reproducible from a clean clone | CI is green; `make setup` and `make lint` succeed on a fresh clone | Lab 1 | ✅ |
| Design document exists and is current | `docs/design.md` follows the ml-design-docs template, every section filled or marked as an assumption | Lab 1 | ✅ |
| Data versioned | `make data` retrieves the raw archive on a clean clone and verifies `bcd4791916daeac3fa5dc76786dd8bd5` | Lab 2 | ✅ |
| Pipeline idempotent | `make rerun MONTH=2022-03` — two runs of one interval, `174 \| 174 \| 174` both times, both rows kept in `dwh.batch_runs` | Lab 2 | ✅ |
| Quality gate present | `make bad-batch` — Q4 fails the run and rolls the month back; `conf/quality_contract.yaml` + the screenshot | Lab 2 | ✅ |
| Experiments tracked | every run in MLflow carries parameters, metrics, `data_version`, `git_commit` and `feature_version` | Lab 2 | ✅ |
| Model registered with lineage | `newsflow-category-router@champion`, tagged with both identifiers, rebuilt by `make reproduce VERSION=champion` | Lab 2 | ✅ |
| LLM component evaluated | an evaluation set with retrieval and answer metrics logged as runs | Lab 3 | ⬜ |
| Prompts versioned | prompt files whose hash appears on every LLM run | Lab 3 | ⬜ |
| Cost and latency recorded | tokens or compute, cost, p50 and p95 per run | Lab 3 | ⬜ |
| Services containerized | two images built from the repository, tagged with the commit | Lab 4 | ⬜ |
| Services deployed with health contracts | both services ready on Minikube, probes fail when the model is missing | Lab 4 | ⬜ |
| Monitoring scheduled | reports on a schedule, a dashboard with at least two windows | Lab 4 | ⬜ |
| Drift signal reaches a human | an injected shift crosses the threshold and the alert is received | Lab 4 | ⬜ |
| One command brings the system up | the documented command starts everything from a clean clone and answers a smoke request | Lab 4 | ⬜ |
| Agent traced and evaluated | persisted traces and a ten-task evaluation run twice | Lab 5 | ⬜ |

---

## Repository layout

```
.
├── conf/
│   ├── taxonomy.yaml          # 42 raw labels → 17 serving categories
│   ├── features.yaml          # the feature contract; its hash is feature_version
│   ├── quality_contract.yaml  # the ten gate checks, the faults, the one waiver
│   └── rules.yaml             # the tier-0 keyword lexicon
├── dags/
│   ├── ingest_month.py        # extract → validate → transform → load → features → gate
│   └── train_and_register.py  # train, then report against the promotion gate
├── src/newsflow/
│   ├── etl/                   # one module per task
│   ├── text.py                # the one transform both paths import
│   ├── dataset.py model.py baselines.py metrics.py
│   ├── train.py reproduce.py  # tracking, registration, reproduction
│   ├── lineage.py             # data_version + git_commit, read from the repo
│   └── warehouse.py           # the only module that writes SQL
├── feature_repo/              # Feast entity, source and feature view
├── sql/001_schema.sql         # raw → dwh → features, and the run ledger
├── scripts/                   # fetch_raw, prove_idempotency, break_a_batch, counts
├── tests/                     # contract tests over the config files
├── docker/ docker-compose.yml # the local stack of design 6.2
└── docs/design.md             # the design document and the three diagrams
```

## Licence and data attribution

Code: MIT. The dataset is the **News Category Dataset** by Rishabh Misra (Kaggle, CC BY 4.0) — see
§5.2 and §6.5 of the design document for how it is versioned, what is held out, and how the one
personal field in it is handled.
