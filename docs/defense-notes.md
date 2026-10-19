# Defense notes — Appendix 2

Answers to the twenty theoretical questions, each tied to the section of
[`design.md`](design.md) it is answered from. Questions marked `*` in the assignment can only be
answered with the design document open; those answers cite section numbers rather than restating
general theory.

These are preparation notes, not a graded artifact. Drop this file from the submission branch if you
would rather the repository carried only what Lab 1 asks for.

---

### 1. What distinguishes a production ML system from a research system? Name three requirements the first has that the second does not, and say where each appears in your design. `*`

A research system's output is a *number in a report*; a production system's output is a *decision
another system acts on*. Three requirements follow that a notebook never has:

1. **A contract with a consumer.** Something downstream is blocked until we answer, so latency and
   availability become correctness, not performance. → 4.2 latency/throughput and
   availability/fallback rows (p95 300 ms, 99.5%, fallback to `LATEST` + queue at ≤2%), formalised
   in 6.8.
2. **Observability when the answer cannot be checked.** Research always has labels; production has
   them late, partially, and biased. → 4.2 observability row and the eight label-free alerts in 6.6
   (M5–M7, M10–M12).
3. **Reversibility.** A research result is never rolled back; a production model is. → 4.2
   reproducibility row (rebuild to ±0.005 macro-F1 from commit + DVC hash) and 6.1 layer 5 (alias
   promotion, 10-minute rollback).

A fourth, if pressed: **a quality gate on input** (Mark 2), because research data is given and
production data arrives.

### 2. Define reliability, scalability and adaptability in one sentence each, and give the non-functional requirement that covers each. `*`

| Term | One sentence | My NFR row (4.2) |
| --- | --- | --- |
| **Reliability** | The system keeps producing correct results in the presence of faults — hardware, software, or human. | **Availability and fallback**: 99.5%, and on failure the caller degrades to `LATEST` + editorial queue rather than blocking publication; the fallback budget of 2%/day is itself alarmed (M4). |
| **Scalability** | The system absorbs growth in load, data or model count without a redesign. | **Latency and throughput**: 2,000 articles/day sustained, 10 req/s burst, two replicas at an assumed ~50 req/s each — ~10× headroom, sized so one replica can die. |
| **Adaptability** | The system can be changed — new data, new model, new requirement — without being taken down or rebuilt. | **Adaptability**: a new month needs no code change; a new model is an alias move with a 10-minute rollback; a new *category* needs a versioned `taxonomy.yaml` change plus a retrain, deliberately not automatic. |

### 3. Which two of your non-functional requirements conflict, and which one wins? `*`

**Latency versus adaptability**, and latency wins.

Adaptability wants the best available model; the transformer candidate in 5.3 is expected to gain
0.04–0.08 macro-F1. Latency gives it 300 ms p95 at the API boundary. 6.3 reserves 200 ms of the
budget as headroom for exactly that candidate, and 5.4 step 1 puts **measured p95 ≤ 300 ms inside
the promotion gate** — so a better model that misses the budget does not ship. Latency wins because
it is a contract with another service (6.8) while the model gain is an internal improvement, and
because the business metric tolerates a weaker model (abstention absorbs it) but the consumer does
not tolerate a slow one.

The subtler conflict, if asked for a second: **reproducibility versus adaptability**, colliding at
Mark 3. Reproducibility demands frozen, point-in-time feature values; adaptability wants
`author_desk_prior` refreshed nightly so new bylines are not strangers. Neither wins outright —
reproducibility owns the training path, adaptability owns the serving path — and the price of
letting both win in their own lane is the declared asymmetry in 5.3, which is then what M10 watches.

### 4. Describe CRISP-DM. Which phases does this lab cover, and which do Labs 2 to 4?

Six phases in a cycle: **Business Understanding → Data Understanding → Data Preparation → Modeling →
Evaluation → Deployment**, with Evaluation able to send you back to Business Understanding and
Deployment closing the loop.

| CRISP-DM phase | Where |
| --- | --- |
| Business Understanding | **Lab 1** — sections 1, 2, 3, 4 |
| Data Understanding | **Lab 1** — section 5.2 and the checklist; completed in Lab 2 against real counts |
| Data Preparation | **Lab 2** — layers 1–3, the quality gate, the feature store |
| Modeling | **Lab 2** (baseline, candidate) and **Lab 3** (the LLM assist) |
| Evaluation | **Lab 2** (5.4 offline gate) and **Lab 3** (retrieval and answer metrics) |
| Deployment | **Lab 4** — containers, Minikube, the monitoring loop |

Worth saying out loud: **CRISP-DM has no monitoring phase.** It folds "monitoring and maintenance"
into a Deployment deliverable and then stops. That is the gap this course fills — layer 7 of my data
flow diagram and all of section 6.6 live in a part of the lifecycle CRISP-DM barely names, which is
why Lab 4 is a lab of its own rather than a step inside Deployment.

### 5. Optimisation metric versus satisficing metric. Which of your three success metrics is which? `*`

An **optimisation** metric is the one you maximise; a **satisficing** metric is a bar you must clear,
after which further improvement buys nothing. You can have only one optimisation metric per
decision; everything else is a constraint.

- **3.1 business:** auto-accept rate / editor touches is the **optimisation** metric. Wrong-section
  corrections ≤3.0 per 100 is **satisficing** — getting it to 1.0 is not worth one lost auto-accept.
- **3.2 model:** macro-F1 is the **optimisation** metric. Per-class recall ≥0.45 and ECE ≤0.05 are
  **satisficing**, and they are the two floors that stop macro-F1 being gamed.
- **3.3 system:** **all satisficing.** Nothing is bought by 150 ms p95 instead of 300, or by 99.9%
  instead of 99.5%.

The pair that actually defines the product is "maximise auto-accept rate *subject to* corrections
≤3.0/100", stated in 3.1.

### 6. Your business metric improves while your model metric stays flat. Two explanations, and which one your monitoring distinguishes. `*`

**(a) The threshold moved, not the model.** τ was lowered (or the confidence distribution drifted
upward), so more articles clear the auto-accept bar. Editor touches fall; macro-F1, which is
computed on the audit sample independently of τ, does not move.

**(b) The traffic got easier.** The incoming mix shifted toward categories the model handles well.
macro-F1 averages all 17 categories equally and stays flat, while the *realised* accept rate on
actual traffic rises.

**Monitoring distinguishes them**, and this is why the serving log carries `tau` on every row (6.6).
(a) shows as a τ change in the log with **M5 (predicted-category PSI) flat**; (b) shows as **M5
crossing 0.20 with τ constant**, usually with **M6** (mean confidence) rising. **M7** (auto-accept
rate) moves in both and so resolves nothing on its own — it is the symptom, M5 plus `tau` is the
diagnosis.

### 7. Your model metric improves offline and the business metric gets worse after deployment. What could have caused it, and what in 5.4 was supposed to catch it? `*`

Three candidates from this design, in order of likelihood:

1. **macro-F1 improved on the tail, traffic lives in the head.** The candidate gained on rare
   categories and lost a little on `POLITICS`, which is ~20% of volume. Macro-F1 weights all 17
   equally; editor touches are weighted by volume. The aggregate gain was real and irrelevant.
2. **The offline number came from the biased slice.** If evaluation used "all labelled data" rather
   than the 5% forced audit (5.2, R3), the gain was measured on hard articles only.
3. **Skew at Mark 3** — `author_desk_prior` computed offline by as-of join, served from a nightly
   table (R6). The offline model used information the serving path does not have.

**5.4 step 2, the 14-day shadow run, is what should have caught all three**, because it scores live
traffic and compares against the champion on the unbiased audit sample. Cause 1 in particular is
invisible to the offline gate in step 1 by construction — the gate is a macro metric — which is why
shadow is a separate step rather than a formality, and why step 4 keeps a 7-day watch window with
automatic rollback.

### 8. Why is a random train-test split wrong for a system receiving data over time? What split does your document use, and what leakage does it prevent? `*`

Two failures, both fatal here.

1. **Leakage through bursts.** News arrives in clusters — one event produces many near-duplicate
   headlines on the same day. A random split puts near-duplicates of a test article into training,
   so the score measures memorisation of a news cycle rather than generalisation. (Deduplication by
   headline hash within a 7-day window, layer 2, reduces this but cannot remove paraphrase.)
2. **The wrong question.** The model will be asked about *future* articles. A random split answers
   "can it classify articles drawn from a distribution it has already seen", which is not what we
   need to know about a system whose topic mix provably moves across the decade (R1).

**My split (5.4):** train 2012-01 → 2020-12; validate by **rolling origin** across 12 monthly folds
over 2021, reporting mean *and* variance across folds; hold out 2022-01 → 2022-09 and touch it once.
Beyond the two failures above it also prevents **future-to-past leakage through
`author_desk_prior`**, which would otherwise aggregate labels from after the row's own date.

### 9. Labels arrive two weeks after the prediction. What can you monitor in those two weeks, what can you not, and which requirements row states this? `*`

**Can** — everything label-free, which is deliberately most of 6.6: M5 predicted-category PSI, M6
mean top-1 confidence, M10 input text / embedding drift and OOV rate, M11 missing-dek share, M12
model age, and all operational alerts M1–M4 (latency, errors, partition staleness, fallback budget).
M7 auto-accept rate too.

**Cannot** — anything requiring `y_true`: accuracy, macro-F1, per-class recall, ECE, and the
confusion matrix. Critically you also cannot *attribute*: a confidence drop (M6) is equally
consistent with the world changing and with the model degrading, and only labels separate them.

**The row is 4.2, "Observability without ground truth"**, which states the assumption that only ~45%
of articles are ever labelled and then requires every alert to be reachable from the label-free set
alone. In my design the delay is worse than two weeks for most traffic — 52% of articles are never
labelled at all (5.2) — which is why the 5% forced audit exists as the only unbiased estimator.

### 10. The pipeline succeeded for three days and the table is stale because the job re-read the same partition. What alert would have caught it, and does 6.6 contain it? `*`

**Yes — M3, "Partition staleness", daily at 09:00, severity page.**

The point is *how* it is defined. The job exited 0 three times, so an exit-code check sees three
green runs. A row-count check also passes, because re-reading the same partition yields an identical
count — that is precisely the failure mode. M3 therefore compares the **content hash of today's raw
partition against yesterday's** and fires when they are equal or when no new rows landed. A content
hash is the only one of the three signals that distinguishes "ingested today's data" from "ingested
yesterday's data again".

It pages rather than tickets because every downstream number — drift, accuracy, the retraining
trigger — silently becomes a statement about stale data the moment this is true.

### 11. A consumer wants results that are both complete and instant. Why can they not have both, and what does your freshness requirement promise instead? `*`

Answered in 6.8, "the contract's hardest clause". *Complete* means a correct category for every
article; *instant* means now. For a genuinely ambiguous article — a climate bill that is both
`ENVIRONMENT` and `POLITICS` (5.1) — a correct answer requires a human judgment that takes minutes,
and an instant answer is a guess. You can buy completeness with time or speed with abstention; the
only way to have both is to pretend a guess is an answer, which is the expensive failure mode in
section 2.

**What is promised instead:** an instant *response* for 100% of articles, of which ~40% carry
`abstain = true` and route to the editorial queue. Completeness is delivered by the **pair**
(service + queue), never by the model alone.

And the freshness requirement is about **the model's age, not the answer's**: the deployed model is
trained on data no older than 35 days (p95). An individual prediction is produced synchronously and
is never stale — only the model behind it can be. Conflating the two is how "freshness" gets
promised to a consumer who actually asked for completeness.

### 12. What does the model lifecycle look like from first notebook to retired model, and which repository artifact represents each stage?

| Stage | Artifact |
| --- | --- |
| Problem framed | `docs/design.md` §1–5.1 *(exists now)* |
| Data acquired and pinned | the `.dvc` pointer file in Git + the content on the MinIO remote (Mark 1) |
| Data validated | the quality-gate suite and its run record (Mark 2) |
| Features defined | the Feast feature definitions + `taxonomy.yaml`, both versioned |
| Experiment | an MLflow run carrying params, metrics, DVC hash and Git commit |
| Candidate selected | the rolling-origin fold table in §7.2 |
| Registered | an MLflow model version tagged with commit + data version |
| Promoted | the `@champion` alias, plus the editorial lead's recorded approval (5.5) |
| Serving | the running model service image, tagged with the commit |
| Observed | the serving log table + the Evidently reports (layer 7) |
| Retrained | a new run triggered by M8, repeating from "Experiment" |
| Retired | the previous alias target, kept registered so rollback stays a 10-minute operation (4.2) |

The through-line a reviewer should notice: **every stage has a Git-reachable artifact**, which is
what makes the first scorecard row ("reproducible from a clean clone") checkable rather than claimed.

### 13. Compare CSV, Parquet and a database table for the raw layer. What did you choose for raw, clean and features? `*`

| Format | Good for | Bad for |
| --- | --- | --- |
| **CSV** | human-readable, universal | no types, no schema, quoting and newline ambiguity, no compression, whole-file reads |
| **Parquet** | columnar, typed, compressed, predicate and column pushdown, partitioned | not human-readable, poor for single-row point lookups, immutable |
| **DB table** | indexed point lookups, transactions, concurrent writers, constraints | needs a running server, costs money idle, awkward to version byte-for-byte |

My choices:

- **Raw: JSON Lines exactly as received**, gzip-compressed, partitioned by month, versioned by DVC.
  The raw layer's job is **fidelity to the source**, not query speed — if I convert on the way in,
  I have destroyed the thing I need when a downstream bug turns out to be a parsing bug. CSV is
  rejected outright: the data contains commas, quotes and newlines inside headlines.
- **Clean / interim: Parquet**, partitioned by month. Typed, ~5–10× smaller, and training reads two
  text columns out of eight — exactly the columnar case.
- **Features: both, and that is the point.** Offline, **Parquet** on object storage for
  point-in-time joins over the full history. Online, a **PostgreSQL table** for single-key lookups
  of `author_desk_prior` inside a 25 ms budget (6.3) — a Parquet file physically cannot serve that.
  The split is Mark 3 on the data flow diagram.

### 14. What does Git LFS do that Git does not, and DVC that Git LFS does not? Which of your data goes where?

**Git LFS over Git:** Git stores every version of a binary whole, so the pack grows without bound
and clones get slow. LFS replaces the file in the tree with a small pointer and keeps the bytes on an
LFS server, fetched on checkout. It fixes repository size — and nothing else.

**DVC over Git LFS:** three things LFS has no concept of. (1) The remote is **any object store you
own** — MinIO, or any object store the platform can mount — not a quota on your Git host. (2) **Pipelines**: `dvc.yaml` declares
stage dependencies and outputs, so `dvc repro` rebuilds only what changed and the data has a
*lineage graph*, not just a version. (3) **Content-addressed deduplication across versions**, so
re-ingesting an unchanged month costs nothing.

In this project:

- **DVC →** raw monthly JSONL partitions, the Parquet interim and feature sets, model artifacts and
  the Lab 3 embedding index. All of it on the MinIO remote.
- **Git LFS →** *nothing.* There is no binary asset: the three diagrams are Mermaid **text** inside
  `design.md` precisely so they diff, review and version like code. If the diagrams had been
  exported PNGs, that is the one thing that would have belonged in LFS.
- **Git →** code, `taxonomy.yaml`, `params.yaml`, prompt files (Lab 3), `.dvc` pointers, `dvc.lock`,
  `uv.lock`, CI config, and these documents.

### 15. Your data source changes its schema without notice. Where is that detected, and what happens downstream? `*`

**Detected at Mark 2, the quality gate** — between layer 2 (preprocessing) and layer 3 (feature
store) on the data flow diagram. Three of its checks catch three different shapes of the problem: a
**renamed or dropped field** fails the schema and dtype check; a **new or renamed category value**
fails taxonomy coverage, because every observed raw label must be present in `taxonomy.yaml`; a
**changed delivery volume** fails the row count within 3σ of the trailing twelve months.

**Downstream of that point: nothing happens, which is the design.** The batch is rejected and is not
written to the feature store, so training does not run, no model is registered, no alias moves, and
the serving model is untouched and keeps answering from the last good version. An alert fires and a
human decides whether this is a break to fix or a real change to absorb into `taxonomy.yaml`.

The property worth naming: **the gate sits upstream of the feature store**, so a corrupted month can
never become training data or online features. Had it sat after, the failure would be silent, and
the first symptom would have been M5 firing weeks later on a model trained on garbage.

### 16. Name the components of your deployment diagram and their managed services. Pick one and say what you lose and gain. `*`

| Component | Managed equivalent on Databricks |
| --- | --- |
| MinIO-compatible object store (`pgsty/silo`) | Unity Catalog volumes |
| DVC 3.67.1 | Delta Lake time travel — **replaces** DVC |
| PostgreSQL 16 | Delta tables + SQL warehouse; Online Tables for the online feature path |
| Apache Airflow 3.3.1, LocalExecutor | Databricks Workflows (Jobs) |
| MLflow 3.16.1 | MLflow in Unity Catalog — native |
| Feast 0.66.0 | Feature Engineering in Unity Catalog |
| `newsflow` model service (FastAPI) | Mosaic AI Model Serving — **no cluster** |
| vLLM 0.30.0 | Foundation Model APIs |
| Evidently | Lakehouse Monitoring |
| Prometheus + Grafana | System tables + DBSQL dashboards |
| Minikube registry / Docker Hub | **n/a** — the runtime is managed |

Two rows go to *n/a*, and a reviewer should ask about that: the platform removes the container
registry and the Kubernetes cluster, which is why 6.7 is the cheapest of the three variants and also
what 7.1 A6 holds against it.

**Taking Airflow → Databricks Workflows.**

*Gain:* **$7/month instead of the $360–400 every hyperscaler charges for a 24/7 managed-Airflow
environment** (6.7), because Jobs Compute is billed per run and this system runs sixty short jobs a
month. No scheduler, webserver or metadata database to operate or patch. The job, the notebook, the
tracking server and the registry share one identity model instead of four.

*Lose:* the DAG is no longer an Airflow DAG. Workflows is a different orchestrator with a different
task model, so the local `ingest_month` DAG and its cloud counterpart stop being the same artifact —
the fidelity loss named in 6.2 and argued in 7.1 A6. Scheduling expressiveness is narrower than
Airflow's sensors and branching. And the platform owns the runtime version, not `uv.lock`.

The trade is good on cost and bad on fidelity, which is exactly the shape of the whole platform
decision — and why 7.1 A6 calls it narrow.

### 17. Your cost estimate turns out to be ten times too low. Which assumption was wrong, and which requirement would you relax first? `*`

**The volume assumption — 2,000 articles/day — not the rates.** Rates are listed numbers; volume is
a guess about a product that does not exist yet, and it multiplies the only lines that scale.

At 20,000/day the bill does **not** become $5,100 — but the answer here is less comfortable than on
a component stack, and knowing why is the point. **Only ~$230 of this estimate is fixed**; the rest
scales. Foundation Model APIs go ~$70 → ~$700, Model Serving moves Small → Medium (≈ 4 → 16 DBU/hr)
~$204 → ~$816, jobs compute ~$7 → ~$20. Realistic total ≈ **$1,600–1,900**. A component stack, where
~$600 is fixed, absorbs the same growth into capacity it was already paying for — so this platform is
cheapest at today's volume and **scales more steeply**. The crossover is a reason to re-price at a 5×
volume change, not a reason to have chosen differently.

**First requirement relaxed: the Lab 3 LLM assist on *every* abstained article** (4.2, cost row).
Restrict it to the lowest-confidence decile — roughly a 75% cut of the largest variable line for a
loss that falls entirely on the articles an editor was going to read anyway. M13's $100 ceiling is
the trigger that forces the conversation rather than discovering it on an invoice. Second: serving
log retention 13 months → 3, keeping a monthly aggregate for the year-over-year comparison.

**Not** relaxed: the latency budget or the availability target, because those are the contract in
6.8 and breaking them turns a cost problem into a product failure.

### 18. What is training-serving skew? Where in your data flow diagram could it enter? `*`

**Skew is when a feature's value or distribution at serving time differs from what the model saw in
training**, so the offline metric stops predicting production behaviour. It is not drift: drift is
the world changing, skew is the two code paths disagreeing about the same moment.

Two entry points in my diagram, and the diagram marks the dangerous one:

1. **Mark 3 — the feature-store split.** The offline side computes `author_desk_prior` by a
   point-in-time as-of join; the online side serves it from a table refreshed nightly. Same feature
   name, two different computations, declared deliberately in 5.3. Any divergence — a missed
   refresh, a different default for unseen authors, a different hash salt — is skew, and it is
   invisible offline. This is why Mark 3 is on the diagram at all.
2. **Layer 2 — preprocessing**, if the serving path ever reimplemented normalisation instead of
   importing it. 5.3 states that the transform lives in one shared module imported by both paths;
   that is the only structural defence, since nothing else would catch a lowercase difference.

The design's third defence is upstream of both: **`link` was dropped** (5.2) because it has no
serving-time source at all — it is minted by the CMS after the section is chosen. Training on it
would have produced a near-perfect offline score and a useless model.

### 19. Why does a feature need a declared serving-time source? Give one whose serving-time source differs from its training-time source. `*`

Because a feature with an undeclared serving-time source is a feature that **might not exist when
you need it, or might mean something different** — and you discover which only after deployment.
Writing the column forces the question at design time, when the answer is a paragraph, instead of at
incident time, when it is a rollback. It is also the only reason the `link` leakage was caught in
Lab 1 rather than Lab 2: the column had no entry to write.

**The feature whose sources differ: `author_desk_prior`.**

- *Training time:* a point-in-time as-of aggregate over confirmed labels strictly **before each
  row's own date**, computed in the Feast offline store over Parquet.
- *Serving time:* a lookup in the Feast **online** PostgreSQL table, keyed by hashed author id and
  refreshed **nightly** — so at serving it is up to 24 hours stale and is computed over all history,
  not as-of a row.

The asymmetry is deliberate, not accidental: a true as-of join is impossible in a 25 ms budget, and a
whole-history aggregate is leakage in training. Both constraints are real, so the feature is defined
twice and the gap is monitored (M10) and tested (R6: removing the feature should move offline
macro-F1 by less than ~0.05; a bigger drop is evidence of leakage, not of a good feature).

### 20. When is a rule-based baseline the right production model? Does your 5.3 baseline qualify, and how would you know? `*`

A rule system is the **right production model**, not a placeholder, when at least one of these holds:
the decision is a written policy or regulation and the rule *is* the specification; explanation is a
hard requirement and "the model said so" is not acceptable to a regulator or a user; labelled data is
too scarce to train anything; or the rule already reaches the accuracy the product needs, in which
case a model is a liability with extra steps. Rules also beat models whenever the cost of being
confidently wrong exceeds the value of being usually right.

**Does my tier-0 keyword baseline qualify? No — and the reasoning should be empirical, not stylistic.**

The estimate in 5.3 is 0.25–0.35 macro-F1 against a target of 0.70, and the failure is structural
rather than a matter of lexicon size: `POLITICS` and `SOCIETY & IDENTITY` share nearly all their
vocabulary, and a first-match-wins lexicon has no way to represent "this word matters here but not
there". Adding terms makes the overlap worse, not better.

**How I would know**, rather than assert: tier-0 runs in Lab 2 alongside the TF-IDF baseline on the
same folds, and 7.2 records both. If the gap were under ~0.05 macro-F1 the rules would win on
operability — no retraining loop, no drift, no feature store, trivially explainable to an editor —
and most of this design would be unnecessary. That is a real possible outcome and the experiment is
set up so it can happen. It is also why the rule baseline is kept in 5.3 as the documented fallback
for a long model-service outage, where an explainable 0.30 beats a 503.
