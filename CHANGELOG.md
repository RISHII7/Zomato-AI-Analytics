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

### Planned

- dbt staging layer (Silver) — type casting, the `'--'` and `'₹ 200'` sentinels, city splitting
- dbt marts (Gold) — `fct_orders`, `fct_order_items`, `dim_restaurant`, `dim_user`
- AI lane — LLM enrichment, RAG over reviews, text-to-SQL
- Airflow DAGs — `upload_raw`, `dbt_build_core`, `enrich_reviews`, `dbt_build_all`
- Streamlit app

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

[Unreleased]: https://github.com/RISHII7/Zomato-AI-Analytics/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/RISHII7/Zomato-AI-Analytics/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/RISHII7/Zomato-AI-Analytics/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/RISHII7/Zomato-AI-Analytics/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/RISHII7/Zomato-AI-Analytics/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/RISHII7/Zomato-AI-Analytics/releases/tag/v0.1.0
