# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While pre-1.0, `MINOR` moves for each completed phase of the architecture.

Every version below has an annotated tag and a
[GitHub release](https://github.com/RISHII7/Zomato-AI-Analytics/releases)
behind it.

---

## [Unreleased]

### Known defects, documented not hidden

See [docs/MODELS.md](docs/MODELS.md#known-defects) for detail.

- `stg_restaurants.rating_count` drops the K multiplier — `1K+ ratings` parses as `1`, inverting the ranking on 2,852 rows
- Both incremental facts use a strict `>` watermark against a non-unique second-resolution timestamp, permanently dropping rows on batch boundaries (2.47% of orders share a timestamp)
- `mart_restaurant_performance.avg(customer_rating)` is biased upward — 30% of delivered orders are unrated, and not at random
- `models.snapshot.zomato` is nested under `models:`, so dbt discards it
- Enrichment and RAG process rows rather than the 294 distinct comments

---

## [1.0.0] — 2026-08-07

The full documentation set. Every layer of the stack now has a document
explaining not just what it does but why it is shaped that way.

### Added

- `docs/ARCHITECTURE.md` — system design with lineage and orchestration
  diagrams, the five decisions worth defending, the security and cost models,
  and an explicit section on what the design deliberately does *not* do
- `docs/MODELS.md` — every model, test and source, with figures taken from
  `target/manifest.json` rather than counted by hand; includes the known
  defects
- `docs/AI_LANE.md` — the three capabilities in depth, the OpenAI→Gemini
  migration table, and quotas measured against the free tier rather than
  quoted from documentation
- `docs/RUNBOOK.md` extended from 6 steps to 10, covering dbt, Airflow and the
  AI apps
- `README.md` rewritten for v1.0.0

### What runs

33.6 M rows loaded · 17 dbt models · 16 tests · 1 Airflow DAG · 3 AI apps.

---

## [0.8.0] — 2026-08-07

Orchestration and the AI lane. The provider moved from OpenAI to Gemini before
any AI code was written against the wrong SDK.

### Added

- `airflow/` — Airflow 3 on Docker: LocalExecutor, Postgres, `api-server` and
  `dag-processor` as separate components, dbt in its own venv
- `airflow/dags/zomato_batch.py` — `reload_raw` → `dbt_build_core` →
  `enrich_reviews` → `dbt_build_ai`
- `ai/enrich_reviews.py` — sentiment, topic and key-issue extraction into
  `ZOMATO.AI.REVIEW_ENRICHED`
- `ai/rag_chat.py` — Streamlit RAG over reviews, embeddings cached to parquet
- `ai/text_to_sql.py` — natural language → Snowflake SQL behind a
  `SELECT`-only guard, executed as `DBT_ROLE`
- `zomato/models/marts/mart_review_insights.sql` — the only model tagged `ai`
- `requirements-airflow.txt`, `[tool.pyrefly]` config, `.vscode/settings.example.jsonc`

### Changed

- **Every OpenAI reference replaced with Gemini** — 11 places across 9 files.
  `google-genai`, not the deprecated `google-generativeai`.
  `gemini-2.5-flash` and `gemini-embedding-001`.

### Notes

- **dbt and Airflow cannot share a virtualenv.** Airflow pins protobuf below 5
  through OpenTelemetry; dbt-core 1.12 requires 6. No pair of versions
  satisfies both — which is why the Dockerfile isolates dbt, and why local
  development uses `.venv` and `.venv-airflow`.
- **Gemini's system prompt is configuration, not a message**, and
  `response.text` can be `None` when a response is blocked — where OpenAI
  raised. All three scripts guard for it.
- **Embeddings cap at 100 texts per call**, and the free tier allows 100 per
  minute. `rag_chat.py` chunks, and `NEW_REVIEWS` is 100.
- **The `SELECT`-only guard is a security control, not a correctness one.** It
  passed a query reporting a 100% cancellation rate against a true ~6%. See
  [docs/AI_LANE.md](docs/AI_LANE.md#the-guard-does-not-check-correctness).

---

## [0.7.0] — 2026-08-07

The warehouse. Silver and Gold, 17 models and 16 tests.

### Added

- **Staging (7 views)** — `stg_restaurants`, `stg_users`, `stg_food`,
  `stg_menu`, `stg_orders`, `stg_order_items`, `stg_reviews`
- **Dimensions (4 tables)** — `dim_customer`, `dim_restaurants`, `dim_food`,
  `dim_date`
- **Facts (2 incremental)** — `fct_orders`, `fact_order_items`, both `merge` on
  a unique key
- **Marts (3 tables)** — `mart_daily_city_revenune`,
  `mart_restaurant_performance`, `mart_delivery_sla`
- **16 data tests** — 8 `not_null`, 6 `unique`, 1 `relationships`,
  1 `accepted_values`
- `macros/generate_schema_name.sql`, `_sources.yml`, `_staging.yml`, `_marts.yml`

### Notes

- **`stg_users` drops `password` rather than masking it.** A masked column
  still carries the shape of the secret and still has to be excluded by hand
  downstream.
- **`stg_orders` exposes `is_delivered` rather than filtering.** Every money
  measure in the marts is guarded with
  `sum(iff(is_delivered, sales_amount, 0))`; the unguarded form overstates by
  ~8.5% and looks entirely correct.
- **`dim_date` is a generated spine**, not derived from observed order dates —
  days with no orders would otherwise vanish, and a time series would silently
  close the gap rather than show a zero.
- **Referential integrity verified across all 10,000,000 orders**: zero orphan
  `user_id`s, zero orphan `r_id`s. An inner join to either dimension drops
  nothing.
- **What is deliberately not tested matters as much as what is.** `not_null` on
  `delivery_time_min`, `unique` on `menu_id`, and the `sales_amount` identity
  would all fail on *correct* data.

---

## [0.6.1] — 2026-08-06

Tooling pin reconciliation. Patch release closing drift the Dependabot merges
introduced.

### Fixed

- Both CI lint jobs install from `requirements-dev.txt` instead of restating
  versions inline. Dependabot can only update that file, so the workflow had
  been left claiming ruff 0.15.10 while requirements said 0.16.1.
- `.pre-commit-config.yaml` revs realigned — ruff v0.16.1, gitleaks v8.30.1.
  CONTRIBUTING.md states pre-commit mirrors CI; a hook running a different
  linter version passes locally and fails on push, which is the failure mode
  pre-commit exists to prevent.
- `GITLEAKS_VERSION` pinned to 8.30.1. The v3 action otherwise installs
  whatever is latest at run time, and `.gitleaks.toml` depends on
  version-specific behaviour — only the singular `[allowlist]` table is
  honoured at global scope.

---

## [0.6.0] — 2026-08-06

dbt project scaffold. Opens Phase 3.

### Added

- `zomato/` — `dbt init` output, committed unmodified so that everything
  following reads as a diff against what the generator produces rather than
  being mixed in with its boilerplate

Built against dbt-core 1.12.0 with dbt-snowflake 1.12.0.

### Notes

Two `dbt` binaries can coexist on a machine. dbt-fusion (the Rust rewrite)
takes `dbt init --project-name x` and rejects a positional name with a
clap-style error that looks nothing like dbt-core's. This project targets
dbt-core.

---

## [0.5.0] — 2026-08-06

Documentation. The project explained rather than merely built.

### Added

- `README.md` — architecture, the AI lane's cost model, a "three ways to get a
  wrong number" section, security reasoning, and a status table linked to the
  tagged releases
- `docs/architecture.png` — end-to-end diagram, source CSVs through to the
  served app, with the AI lane drawn *alongside* the medallion layers rather
  than after them
- `CHANGELOG.md` — this file

---

## [0.4.0] — 2026-08-06

Snowflake foundation. Empty account to 33.6 M rows in five ordered scripts.

### Added

- `snowflake/01_setup.sql` — `ZOMATO_WH` (XSMALL, `AUTO_SUSPEND = 60`,
  `INITIALLY_SUSPENDED`), the `ZOMATO` database, six medallion schemas, and
  `DBT_ROLE` with `USAGE`/`OPERATE` plus `FUTURE` grants
- `snowflake/02_storage_integration.sql` — the Snowflake half of the trust
  handshake
- `snowflake/03_stage_and_formats.sql` — external stage over
  `s3://<BUCKET>/raw/` and a CSV format tuned to these files
- `snowflake/04_raw_tables.sql` — seven landing tables
- `snowflake/05_copy_into.sql` — seven `COPY` statements with per-table error
  tolerance
- `docs/RUNBOOK.md` — six steps, a symptom-to-cause troubleshooting table, and
  teardown

### Notes

- **Stateful objects use `IF NOT EXISTS`; stateless use `CREATE OR REPLACE`.**
  `CREATE OR REPLACE ROLE` succeeds and silently drops every grant on the role.
- **Error tolerance is per table.** `ON_ERROR = 'CONTINUE'` on scraped
  dimensions, `'ABORT_STATEMENT'` on generated facts. A single global setting
  would be wrong in one direction or the other.
- **`ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE`** because exactly one row of
  371,561 in `food.csv` is short a field, and it would otherwise abort the
  whole load.
- **`COPY` loads by position, and getting it wrong succeeds.** The four
  dimension files carry a leading pandas index column the three fact files do
  not.

---

## [0.3.0] — 2026-08-06

AWS lake and the cross-account trust that lets Snowflake read it without
holding a credential.

### Added

- `aws/iam/s3-read-policy.json` — `GetObject`, `GetObjectVersion`,
  `ListBucket`, `GetBucketLocation`. No write actions.
- `aws/iam/snowflake-role-trust-policy-initial.json` — bootstrap policy that
  breaks the circular dependency between role and integration
- `aws/iam/snowflake-role-trust-policy-final.json` — pinned to Snowflake's IAM
  principal with an `sts:ExternalId` condition
- `aws/README.md` — bucket layout, the four-step handshake, symptom-to-cause
  table

### Security

- **The external ID is the whole point.** Snowflake shares one IAM principal
  across accounts on a deployment, so the principal ARN is neither secret nor
  unique. Without the `sts:ExternalId` condition, any Snowflake customer on
  that deployment could point their integration at this role ARN and have
  Snowflake read the bucket for them — a confused-deputy attack in which
  nobody involved does anything wrong.
- **Read-only.** No `PutObject`, no `DeleteObject`. S3 holds the only copy of
  `orders.csv` and `order_items.csv` that is not in git.

### Known issues

- `CREATE OR REPLACE STORAGE INTEGRATION` mints a **new** external ID,
  silently invalidating the trust policy. Every subsequent `COPY` then fails
  with an access-denied that mentions nothing about trust.

---

## [0.2.0] — 2026-08-06

Source datasets and a data dictionary measured against the actual files.

### Added

- `data/users.csv` (100,000), `data/restaurant.csv` (148,541),
  `data/food.csv` (371,561), `data/menu.csv` (1,179,936),
  `data/reviews.csv` (300,000)
- `data/samples/` — the first 5,000 orders plus every line item belonging to
  them, referentially intact by construction
- `scripts/make_samples.py` — rebuilds the extracts; `--check` verifies the
  join invariant and runs in CI
- `docs/DATA_DICTIONARY.md` — all 61 columns across 7 tables
- `.gitleaks.toml` — path-scoped exemption for entropy false positives in
  scraped address columns

### Notes

- **Nulls in `orders` are structural.** `order_status` fully determines
  whether `customer_rating` and `delivery_time_min` are populated, with no
  exceptions across 300,000 measured rows. `delivery_time_min IS NULL` means
  the order never arrived — a `not_null` test would be wrong.
- **Cancelled orders keep their revenue.** ~8.5% of orders are cancelled or
  refunded and still carry a populated `sales_amount`. Any revenue measure
  without `WHERE order_status = 'Delivered'` overstates by about that much.
- **`reviews.comment` has 294 distinct strings across 300,000 rows.** Enrich
  the distinct set, not the table.
- `sales_amount` is not reproducible from its parts; rounding differs.
- `menu_id` looks like a key and is not; the grain is `(r_id, f_id)`.

### Not included

- `data/orders.csv` (1.33 GB) and `data/order_items.csv` (955 MB) exceed
  GitHub's 100 MB per-file limit and live in S3.
- `data/menu.xlsx` — a 41 MB binary duplicate of `menu.csv`.

---

## [0.1.0] — 2026-08-06

Repository foundation. The working agreement and the automated checks, before
any pipeline code.

### Added

- `CONTRIBUTING.md` — branching model, conventional-commit scopes mapped to
  the layers of this stack, `--no-ff` merge policy, SQL conventions
- `SECURITY.md` — threat model and the rotate-before-rewrite leak response
- `.github/PULL_REQUEST_TEMPLATE.md` — requires verification output and a cost
  estimate
- `.github/ISSUE_TEMPLATE/` — crash, data-quality and feature-request forms
- `.github/CODEOWNERS` — explicit ownership of `aws/`, `snowflake/`,
  `.github/workflows/`
- `.github/workflows/ci.yml` — six hermetic jobs holding no cloud credentials
- `.github/dependabot.yml` — monthly, linters grouped
- `.pre-commit-config.yaml` — mirrors CI locally, 50 MB file cap
- `pyproject.toml`, `.sqlfluff`, `requirements-dev.txt`, `.env.example`
- `LICENSE` — MIT for the code, with an explicit note that the datasets are
  synthetic

### Notes

- **sqlfluff is pinned to 4.2.2.** The 3.x Snowflake dialect cannot parse
  `CREATE STAGE ... STORAGE_INTEGRATION = ...` and reports it unparsable.
- **The sqlfluff rule set is an allowlist.** `references.keywords` is
  disabled because RAW mirrors the source CSVs, which ship columns named
  `name`, `password`, `comment` and `menu`.
- **CI holds no credentials.** Warehouse-dependent checks belong in the
  Airflow run.

---

[Unreleased]: https://github.com/RISHII7/Zomato-AI-Analytics/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/RISHII7/Zomato-AI-Analytics/compare/v0.8.0...v1.0.0
[0.8.0]: https://github.com/RISHII7/Zomato-AI-Analytics/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/RISHII7/Zomato-AI-Analytics/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/RISHII7/Zomato-AI-Analytics/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/RISHII7/Zomato-AI-Analytics/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/RISHII7/Zomato-AI-Analytics/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/RISHII7/Zomato-AI-Analytics/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/RISHII7/Zomato-AI-Analytics/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/RISHII7/Zomato-AI-Analytics/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/RISHII7/Zomato-AI-Analytics/releases/tag/v0.1.0
