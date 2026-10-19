# Infrastructure variants — AWS, GCP, Databricks

Companion to [`design.md`](design.md) sections **6.2 Infra**, **6.7 Cost** and **7.1 A6**. The design
document commits to one cloud column, because a design that refuses to choose cannot produce a cost
number. This file holds the two alternatives that were priced before that choice was made, so the
choice is auditable rather than asserted.

Workload held constant across all three: **2,000 articles/day = 60,000 predictions/month**, ~209k
historical rows / ~90 MB, one monthly ingest + train run, one daily monitoring run (~60 job runs a
month, none longer than an hour), an online endpoint under a **p95 ≤ 300 ms / 99.5%** contract, and
the Lab 3 LLM assist on the ~40% abstained slice.

---

## 0 Provenance — read this before quoting any number

These figures are **not** from the providers' own calculators, which section 6.7 of the assignment
asks for. Two things got in the way and both are worth stating plainly:

1. `databricks.com` and `cloud.google.com` are **blocked by policy** in this environment's browser,
   so the vendor pages could not be opened directly.
2. The pages that *were* fetchable render their pricing tables with JavaScript; the fetched HTML
   contained the literal string `Loading...` where the rates should be.

So every rate below comes from **third-party pricing write-ups retrieved by search in October 2026**,
listed in section 5. Treat them as **order-of-magnitude correct and digit-by-digit unverified**.

| Confidence | Applies to |
| --- | --- |
| **Reported rate** — found in a secondary source, consistent across more than one | Databricks DBU rates, GKE control-plane fee and credit, Composer DCU rate, Cloud SQL vCPU/GB rates, Vertex Feature Store node rates |
| **Assumption** — my sizing of the workload onto those rates | DBU-hours consumed per job run, endpoint size class, node counts, storage volumes |
| **Unverified** — plausible but from a single source | Model Serving size-to-DBU mapping, Vector Search endpoint hourly rate |

**Before submission, re-derive the headline totals on [calculator.aws](https://calculator.aws/),
[cloud.google.com/products/calculator](https://cloud.google.com/products/calculator) and the
Databricks calculator.** The *ranking* below is robust to a fair amount of error in the individual
rates; the absolute totals are not.

---

## 1 Component mapping

Rows are the components named in `design.md` 6.1. Two rows are the whole story — look at where
Databricks says *n/a*.

| Component (6.1) | **Databricks** (chosen) | AWS | GCP |
| --- | --- | --- | --- |
| Object storage / data lake | Unity Catalog volumes — still S3/GCS underneath, billed by the cloud | Amazon S3 | Cloud Storage |
| Data versioning | **Delta Lake table history + time travel** — replaces DVC rather than hosting it | DVC remote on S3 | DVC remote on GCS |
| Warehouse / serving DB | Delta tables + SQL warehouse. **No PostgreSQL**: the online feature path needs Databricks Online Tables or an external store | RDS for PostgreSQL | Cloud SQL for PostgreSQL |
| Orchestrator | **Databricks Workflows (Jobs)** | Amazon MWAA | Cloud Composer 3 |
| Experiment tracking + registry | **MLflow in Unity Catalog** — native; Databricks authors MLflow | SageMaker with MLflow | Vertex AI Experiments + Model Registry |
| Feature store | Feature Engineering in UC + Online Tables | SageMaker Feature Store | Vertex AI Feature Store |
| Container registry | **n/a** — no image to push; the runtime is managed | Amazon ECR | Artifact Registry |
| Kubernetes cluster | **n/a** — no cluster; Model Serving is managed | Amazon EKS | GKE Standard |
| Model and data monitoring | Lakehouse Monitoring | SageMaker Model Monitor | Vertex AI Model Monitoring |
| Metrics and dashboards | System tables + DBSQL dashboards | CloudWatch | Cloud Monitoring |
| Vector store (Lab 3) | Mosaic AI Vector Search | OpenSearch Serverless | Vertex AI Vector Search |
| LLM inference (Lab 3) | Foundation Model APIs | Amazon Bedrock | Vertex AI generative AI |

---

## 2 Cost — AWS variant

A component-stack alternative. Repeated here so the three totals sit side by side.
`us-east-1`, on-demand list, no Savings Plans, no free tier.

| Line | Size assumed | $/mo |
| --- | --- | --- |
| S3 | 50 GB + ~2M requests | 3 |
| RDS PostgreSQL | `db.t4g.medium`, 100 GB gp3, single-AZ | 65 |
| **MWAA** | `mw1.small`, 1 environment, 24/7 | **360** |
| SageMaker with MLflow | `ml.t3.medium` tracking server, 24/7 | 45 |
| SageMaker Feature Store | 60k writes + 1.8M reads, 5 GB offline | 25 |
| **EKS** | control plane $73 + 2 × `t3.large` + 100 GB gp3 | **200** |
| ECR | 10 GB | 1 |
| Model Monitor | ~60 jobs on `ml.m5.xlarge` | 10 |
| CloudWatch | ~25 metrics + 5 GB logs | 20 |
| Vector store | pgvector inside the RDS instance | 0 |
| Bedrock | 24k calls × (1.5k in / 0.3k out), Haiku-class | 70 |
| Networking | NAT gateway + egress | 40 |
| | **Total** | **≈ $840** |

---

## 3 Cost — GCP variant

`us-central1`, on-demand list.

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
| Vector store | **pgvector on the Cloud SQL above**, same divergence as 6.2 | 0 | Vertex AI Vector Search keeps an always-on index node |
| Vertex AI generative AI | 24k calls, Flash-class | 50 | |
| Networking | Cloud NAT + egress | 40 | ~$0.044/hr NAT |
| | **Total** | **≈ $961** | |

**GCP is the most expensive of the three**, and not by a little. Two lines do it: Composer 3 at ~$400
for a small environment is *worse* than MWAA, and Vertex AI Feature Store charges ~$219 for an
always-on online serving node where SageMaker Feature Store bills per operation (~$25 at our volume).
The one genuine GCP win — **the $74.40/month credit that makes one GKE control plane free**, where
EKS charges $73 — saves less than either of those lines costs.

Swapping Vertex AI Feature Store for Feast on the Cloud SQL instance, the same divergence already
taken on the vector-store row, brings GCP to **≈ $742**.

---

## 4 Cost — Databricks (the chosen column)

Premium tier on AWS `us-east`. DBU rates are reported; DBU *consumption* is my assumption.

| Line | Size assumed | $/mo |
| --- | --- | --- |
| **Jobs Compute (Workflows)** | single-node job cluster ≈ 1 DBU/hr @ **$0.15/DBU** + ~$0.11/hr EC2; ~25 compute-hours/month across ingest, train and monitor runs | **~7** |
| **Model Serving (CPU)** | Small endpoint ≈ 4 DBU/hr @ **$0.070/DBU**, always-on | **204** |
| **Mosaic AI Vector Search** | Standard endpoint @ **$0.28/hr**; ~1 GB index, inside the 30 GB free tier | **204** |
| Foundation Model APIs | 24k calls × (1.5k in / 0.3k out) | 70 |
| Object storage | 50 GB on S3 under Unity Catalog | 3 |
| Delta Lake / UC metadata | storage only; no separate platform fee on Premium | 2 |
| Networking | NAT + egress | 20 |
| | **Total** | **≈ $510** |

Dropping the always-on Vector Search endpoint — serving the Lab 3 retrieval from a Delta table in
the same job that already runs, which is fine because the assist is editor-facing and asynchronous,
not on the 300 ms path — brings it to **≈ $306**.

**Why Model Serving must be always-on.** Databricks Model Serving supports scale-to-zero, and at
2,000 articles/day the mean gap between requests is ~43 seconds, so the endpoint would stay warm
through the working day and go cold overnight. A cold start measured in tens of seconds against a
**300 ms p95** budget (4.2, latency row) is not a trade worth making, so the variant is priced
always-on. This is the one Databricks line that does not benefit from pay-per-use.

---

## 5 What the comparison actually shows

| Variant | Total | Without the always-on vector/feature line |
| --- | --- | --- |
| **Databricks** | **≈ $510** | **≈ $306** |
| AWS (chosen) | ≈ $840 | — (already uses pgvector) |
| GCP | ≈ $961 | ≈ $742 |

**Databricks is the cheapest of the three, by roughly 40%.** That is a real result and it should be
said plainly rather than argued away.

**But the reason is specific, and it is the same fact as the objection in 7.1 A6.** Databricks wins
on the two rows where it says *n/a*:

- it has **no managed-Airflow line** — MWAA $360 and Composer $400 are each a 24/7 environment fee
  paid to run ~60 short jobs a month, while Databricks Workflows bills ~$7 for the same work;
- it has **no Kubernetes line** — EKS $200 and GKE $115 for a cluster that hosts one small API.

Together those are **$560 of the AWS total and $515 of the GCP total: always-on capacity that is
idle almost all of the time.** Databricks removes that cost by removing those components — and those
components are precisely the ones Labs 2 and 4 exist to make you build and demonstrate. The cost
advantage and the pedagogical objection are one fact seen from two sides.

**The fair comparison is not Databricks against the table above.** The AWS column is deliberately
written against the course reference stack, which names the managed services. Optimised honestly —
Airflow as pods on the EKS cluster that already exists (−$340), MLflow on the same cluster instead
of a SageMaker tracking server (−$45) — **AWS lands at ≈ $455**, which is within noise of the
Databricks $510 and more expensive than the $306 variant. So:

1. If the question is *"which platform is cheapest for this workload as specified"* — **Databricks**.
2. If the question is *"is the AWS design's cost dominated by a bad choice"* — **yes, managed Airflow
   is the worst line item in both cloud variants**, which 6.7 already says.
3. If the question is *"should this project run on Databricks"* — **no**, and the reason has nothing
   to do with price: there is no Databricks that runs on a laptop, and the first row of the maturity
   scorecard is reproducibility from a clean clone (7.1 A6).

### What would change the decision

Any one of these flips it toward Databricks, and none of them is true today:

- the corpus outgrows one machine (we are at ~90 MB; the threshold is ~tens of GB);
- a streaming path is added, so Spark Structured Streaming earns its keep — Appendix 3 scopes Spark
  to exactly that case, and `design.md` 5.2 commits to monthly batch;
- Delta Lake's ACID guarantees on the lake become a requirement, replacing DVC with something
  strictly better;
- the course stops requiring local reproducibility, which is the only blocking objection.

---

## 6 Sources

Vendor pricing pages could not be read directly (section 0). Rates below were taken from these
secondary write-ups, retrieved October 2026:

- [Databricks pricing: DBU rates for AWS, Azure and GCP — lakesentry.io](https://lakesentry.io/blog/databricks-pricing-explained/)
- [Databricks pricing 2026: DBU rates, SQL warehouse sizes and a worked bill — owox.com](https://www.owox.com/blog/articles/databricks-pricing)
- [Databricks 2026 pricing guide — revefi.com](https://www.revefi.com/blog/databricks-pricing-guide)
- [Databricks pricing guide (2026): understanding DBU costs and calculators — flexera.com](https://www.flexera.com/blog/finops/databricks-pricing-guide/)
- [Mosaic AI Vector Search cost management — docs.databricks.com](https://docs.databricks.com/aws/en/vector-search/vector-search-cost-management)
- [Cloud Composer cost optimization — nops.io](https://www.nops.io/blog/google-cloud-composer-cost-optimization-managed-airflow/)
- [Cloud Composer 3: truly serverless? — medium.com](https://medium.com/@shuvro_25220/cloud-composer-3-truly-serverless-5af001bc7930)
- [EKS vs AKS vs GKE: $73/mo vs free control plane — tech-insider.org](https://tech-insider.org/eks-vs-aks-vs-gke-2026/)
- [Vertex AI pricing: the complete 2026 guide — nops.io](https://www.nops.io/blog/vertex-ai-pricing/)
- [Cost of Vertex AI Feature Store — theneuralbase.com](https://theneuralbase.com/google-vertex-ai/learn/intermediate/cost-of-feature-store/)
- [Google Cloud SQL pricing 2026 — securityboulevard.com](https://securityboulevard.com/2026/05/google-cloud-sql-pricing-2026-instance-costs-storage-and-what-the-calculator-hides/)

Official calculators to re-derive against: [AWS](https://calculator.aws/),
[GCP](https://cloud.google.com/products/calculator), [Azure](https://azure.microsoft.com/pricing/calculator/).
