# Infrastructure variants — GCP and Databricks

Companion to [`design.md`](design.md) sections **6.2 Infra**, **6.7 Cost** and **7.1 A6**. The design
document commits to one platform, because a design that refuses to choose cannot produce a cost
number. This file holds the alternative that was priced before that choice was made, so the choice
is auditable rather than asserted.

Workload held constant across both: **2,000 articles/day = 60,000 predictions/month**, ~209k
historical rows / ~90 MB, one monthly ingest + train run, one daily monitoring run (~60 job runs a
month, none longer than an hour), an online endpoint under a **p95 ≤ 300 ms / 99.5%** contract, and
the Lab 3 LLM assist on the ~40% abstained slice.

---

## 0 Provenance — read this before quoting any number

These figures are **not** from the providers' own calculators, which section 6.7 of the assignment
asks for. Two things got in the way and both are worth stating plainly:

1. `cloud.google.com` and `databricks.com` are **blocked by policy** in the environment this document
   was written in, so the vendor pages could not be opened directly.
2. The pages that *were* fetchable render their pricing tables with JavaScript; the fetched HTML
   contained the literal string `Loading...` where the rates should be.

So every rate below comes from **third-party pricing write-ups retrieved by search in October 2026**,
listed in section 5. Treat them as **order-of-magnitude correct and digit-by-digit unverified**.

| Confidence | Applies to |
| --- | --- |
| **Reported rate** — found in a secondary source, consistent across more than one | Databricks DBU rates, GKE control-plane fee and credit, Cloud Composer DCU rate, Cloud SQL vCPU/GB rates, Vertex AI Feature Store node rate |
| **Assumption** — my sizing of the workload onto those rates | DBU-hours consumed per job run, endpoint size class, node counts, storage volumes |
| **Unverified** — plausible but from a single source | Model Serving size-to-DBU mapping, Vector Search endpoint hourly rate |

**Before submission, re-derive the headline totals on the
[Google Cloud pricing calculator](https://cloud.google.com/products/calculator) and the Databricks
calculator.** The *ranking* below is robust to a fair amount of error in the individual rates; the
absolute totals are not.

---

## 1 Component mapping

Rows are the components named in `design.md` 6.1. Two rows are the whole story — look at where
Databricks says *n/a*.

| Component (6.1) | **GCP** (chosen) | Databricks |
| --- | --- | --- |
| Object storage / data lake | Cloud Storage | Unity Catalog volumes — still a cloud bucket underneath, billed by the underlying provider |
| Data versioning | DVC remote on GCS | **Delta Lake table history + time travel** — replaces DVC rather than hosting it |
| Warehouse / serving DB | Cloud SQL for PostgreSQL | Delta tables + SQL warehouse. **No PostgreSQL**: the online feature path needs Online Tables |
| Orchestrator | Cloud Composer 3 | **Databricks Workflows (Jobs)** |
| Experiment tracking + registry | Vertex AI Experiments + Model Registry | **MLflow in Unity Catalog** — native; Databricks authors MLflow |
| Feature store | Vertex AI Feature Store | Feature Engineering in Unity Catalog + Online Tables |
| Container registry | Artifact Registry | **n/a** — no image to push; the runtime is managed |
| Kubernetes cluster | GKE Standard | **n/a** — no cluster; Model Serving is managed |
| Model and data monitoring | Vertex AI Model Monitoring | Lakehouse Monitoring |
| Metrics and dashboards | Cloud Monitoring | System tables + DBSQL dashboards |
| Vector store (Lab 3) | Vertex AI Vector Search | Mosaic AI Vector Search |
| LLM inference (Lab 3) | Vertex AI generative AI | Foundation Model APIs |

---

## 2 Cost — GCP (the chosen platform)

Same as `design.md` 6.7, repeated here so the totals sit side by side. `us-central1`, on-demand list.

| Line | Size assumed | $/mo | Rate used |
| --- | --- | --- | --- |
| Cloud Storage | 50 GB Standard + ops | 3 | ~$0.020/GB-mo |
| Cloud SQL for PostgreSQL | Enterprise, 2 vCPU / 4 GB, 100 GB SSD | 98 | $0.0413/vCPU-hr, $0.007/GB-hr, $0.170/GB-mo SSD |
| **Cloud Composer 3** | small environment, 24/7 | **400** | $0.06/DCU-hr × ~9 DCU/hr |
| Vertex AI Experiments + Model Registry | registry itself is free; metadata storage only | 5 | registry $0 |
| **Vertex AI Feature Store** | 1 optimized online serving node, 24/7 | **219** | $0.30/node-hr (incl. 200 GB) |
| Artifact Registry | 10 GB | 1 | ~$0.10/GB-mo |
| **GKE Standard** | control plane $73 **− $74.40 free-tier credit = $0** + 2 × `e2-standard-2` + 100 GB PD | **115** | $0.10/hr control plane, one cluster credited |
| Vertex AI Model Monitoring | ~60 jobs | 10 | |
| Cloud Monitoring | ~25 metrics + 5 GB logs | 20 | |
| Vector store | **pgvector on the Cloud SQL above** — divergence 1 of 6.2 | 0 | Vertex AI Vector Search keeps an always-on index node |
| Vertex AI generative AI | 24k calls, Flash-class | 50 | |
| Networking | Cloud NAT + egress | 40 | ~$0.044/hr NAT |
| | **Total, managed services as listed** | **≈ $961** | |
| | **With both divergences of 6.2 taken** | **≈ $742** | |
| | **Also self-hosting Airflow on the GKE cluster already running** | **≈ $410** | |

---

## 3 Cost — Databricks

Premium tier. DBU rates are reported; DBU *consumption* is my assumption.

| Line | Size assumed | $/mo |
| --- | --- | --- |
| **Jobs Compute (Workflows)** | single-node job cluster ≈ 1 DBU/hr @ **$0.15/DBU** + ~$0.11/hr instance; ~25 compute-hours/month across ingest, train and monitor runs | **~7** |
| **Model Serving (CPU)** | Small endpoint ≈ 4 DBU/hr @ **$0.070/DBU**, always-on | **204** |
| **Mosaic AI Vector Search** | Standard endpoint @ **$0.28/hr**; ~1 GB index, inside the 30 GB free tier | **204** |
| Foundation Model APIs | 24k calls × (1.5k in / 0.3k out) | 70 |
| Object storage | 50 GB under Unity Catalog volumes | 3 |
| Delta Lake / UC metadata | storage only; no separate platform fee on Premium | 2 |
| Networking | NAT + egress | 20 |
| | **Total, managed services as listed** | **≈ $510** |
| | **Serving Lab 3 retrieval from a Delta table instead of the vector endpoint** | **≈ $306** |

**Why Model Serving must be always-on.** Databricks Model Serving supports scale-to-zero, and at
2,000 articles/day the mean gap between requests is ~43 seconds, so the endpoint would stay warm
through the working day and go cold overnight. A cold start measured in tens of seconds against a
**300 ms p95** budget (`design.md` 4.2, latency row) is not a trade worth making, so the variant is
priced always-on. This is the one Databricks line that gets no benefit from pay-per-use.

---

## 4 What the comparison actually shows

| Comparison | GCP | Databricks | Cheaper |
| --- | --- | --- | --- |
| **As listed** — every managed service of 6.2 named and paid for | ≈ $961 | ≈ $510 | Databricks, by ~47% |
| **Each with its own documented divergences taken** | ≈ $742 | ≈ $306 | Databricks |
| **Both built attentively** — divergences plus self-hosted Airflow | ≈ $410 | ≈ $306 | Databricks, by ~25% |

**Databricks is cheaper under every comparison, and the design chooses GCP anyway.** That is a
deliberate decision rather than an oversight, and `design.md` 6.2 says so in those words: the
platform is chosen on fit, not on price. The reason is in **7.1 A6** — every component of 6.1 keeps
a one-to-one managed counterpart on GCP, so the local column and the cloud column stay near drop-in,
whereas on Databricks three rows have no counterpart (DVC replaced by Delta time travel, Feast by
Online Tables, PostgreSQL by Delta tables) and two rows go to *n/a* entirely.

**Where the gap comes from.** Databricks wins on exactly the two rows where it offers nothing:

- **no managed-Airflow line** — Cloud Composer 3 charges ~$400 for a 24/7 environment that runs ~60
  short jobs a month, where Databricks Workflows bills ~$7 for the same work;
- **no Kubernetes line** — GKE costs $115 for a cluster hosting one small API.

That is **$515 of the GCP total: always-on capacity idle almost all of the time.** Databricks removes
that cost by removing those components — and those components are precisely the ones Labs 2 and 4
exist to make us build. The cost advantage and the fidelity objection are one fact seen from two
sides.

**A scaling caveat that cuts the other way.** Only ~$230 of the Databricks estimate is fixed, against
~$750 of the GCP one. At 20,000 articles/day Databricks reaches ≈ $1,600–1,900 and GCP ≈ $2,100–2,600
— still cheaper, but the margin narrows, because a component stack absorbs growth into capacity it
already pays for. Worth re-pricing at a 5× volume change.

### What would flip the decision toward Databricks

- the corpus outgrows one machine (we are at ~90 MB; the threshold is tens of GB);
- a streaming path is added, so Spark Structured Streaming earns its keep — Appendix 3 of the
  assignment scopes Spark to exactly that case, and `design.md` 5.2 commits to monthly batch;
- Delta Lake's ACID guarantees on the lake become a requirement, replacing DVC with something
  strictly better;
- the budget becomes binding — at which point the honest first move is self-hosting Airflow on the
  GKE cluster (−$380), since that closes most of the gap without changing platform.

---

## 5 Sources

Vendor pricing pages could not be read directly (section 0). Rates were taken from these secondary
write-ups, retrieved October 2026:

- [Databricks pricing: DBU rates by cloud — lakesentry.io](https://lakesentry.io/blog/databricks-pricing-explained/)
- [Databricks pricing 2026: DBU rates, SQL warehouse sizes and a worked bill — owox.com](https://www.owox.com/blog/articles/databricks-pricing)
- [Databricks 2026 pricing guide — revefi.com](https://www.revefi.com/blog/databricks-pricing-guide)
- [Databricks pricing guide (2026): understanding DBU costs and calculators — flexera.com](https://www.flexera.com/blog/finops/databricks-pricing-guide/)
- [Mosaic AI Vector Search cost management — docs.databricks.com](https://docs.databricks.com/aws/en/vector-search/vector-search-cost-management)
- [Cloud Composer cost optimization — nops.io](https://www.nops.io/blog/google-cloud-composer-cost-optimization-managed-airflow/)
- [Cloud Composer 3: truly serverless? — medium.com](https://medium.com/@shuvro_25220/cloud-composer-3-truly-serverless-5af001bc7930)
- [Managed Kubernetes control-plane pricing compared — tech-insider.org](https://tech-insider.org/eks-vs-aks-vs-gke-2026/)
- [Vertex AI pricing: the complete 2026 guide — nops.io](https://www.nops.io/blog/vertex-ai-pricing/)
- [Cost of Vertex AI Feature Store — theneuralbase.com](https://theneuralbase.com/google-vertex-ai/learn/intermediate/cost-of-feature-store/)
- [Google Cloud SQL pricing 2026 — securityboulevard.com](https://securityboulevard.com/2026/05/google-cloud-sql-pricing-2026-instance-costs-storage-and-what-the-calculator-hides/)
