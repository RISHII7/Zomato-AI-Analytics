# Contributing

Thanks for taking a look. This document is the short version of how the repo is
run — branching, commit style, review expectations and the checks that have to
pass before anything lands on `main`.

---

## Table of contents

- [Ground rules](#ground-rules)
- [Local setup](#local-setup)
- [Branching model](#branching-model)
- [Commit messages](#commit-messages)
- [Pull requests](#pull-requests)
- [Releases and tags](#releases-and-tags)
- [Working with the datasets](#working-with-the-datasets)
- [SQL style](#sql-style)
- [Secrets](#secrets)

---

## Ground rules

1. **`main` is always deployable.** Every commit on `main` is a merge commit
   from a reviewed PR, or the initial commit. No direct pushes.
2. **No credentials in git, ever.** Not in a commit, not in a comment, not in a
   `-- example`. See [Secrets](#secrets).
3. **No raw data over 100 MB.** GitHub rejects it outright. See
   [Working with the datasets](#working-with-the-datasets).
4. **Every SQL file is re-runnable.** If running a script twice breaks the
   warehouse, the script is wrong.

---

## Local setup

```bash
git clone https://github.com/RISHII7/Zomato-AI-Analytics.git
cd Zomato-AI-Analytics

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

cp .env.example .env             # then fill in your own values
```

Verify the checked-in samples still join correctly:

```bash
python scripts/make_samples.py --check
```

---

## Branching model

Trunk-based, with short-lived branches off `main`.

```
main ──●──────●──────────●──────────●───────▶  protected, tagged, always green
        \    /  \       /  \       /
         ●──●    ●──●──●    ●──●──●            feature branches, squash-free
```

| Prefix      | Use for                                        | Example                            |
| ----------- | ---------------------------------------------- | ---------------------------------- |
| `feat/`     | new pipeline stage, model, dataset, capability | `feat/snowflake-foundation`        |
| `fix/`      | a bug in existing behaviour                    | `fix/reviews-null-rating-load`     |
| `docs/`     | documentation only                             | `docs/architecture-and-readme`     |
| `chore/`    | tooling, config, dependencies, repo plumbing   | `chore/repo-scaffolding`           |
| `refactor/` | behaviour-preserving restructuring             | `refactor/stg-orders-cte-layout`   |
| `perf/`     | performance work with a measured before/after  | `perf/order-items-clustering-key`  |
| `test/`     | tests and data-quality assertions only         | `test/dbt-uniqueness-assertions`   |

Keep branches under a few days old. If a branch outlives that, it is doing too
much — split it.

---

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/), because the
changelog and the version bumps are derived from them.

```
<type>(<scope>): <subject, imperative, lower case, no full stop>

<body — what changed and, more importantly, why. Wrap at 72 columns.>

<footer — BREAKING CHANGE:, Refs #12, Closes #34>
```

**Types:** `feat`, `fix`, `docs`, `chore`, `refactor`, `perf`, `test`, `build`, `ci`, `revert`

**Scopes used in this repo:** `aws`, `snowflake`, `dbt`, `airflow`, `ai`, `data`,
`app`, `docs`, `ci`

The subject line answers *what*. The body answers *why*, and is where the value
is — a diff already shows what changed. Prefer this:

```
feat(snowflake): widen CSV_FMT to tolerate ragged source rows

food.csv is missing a trailing field on ~40 rows, which aborted the COPY
with a column-count error. ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE NULL-fills
the gap instead, and the resulting NULLs are caught by a not_null test in
staging rather than silently failing the load at 3 a.m.
```

over this:

```
fixed csv format
```

Breaking changes get a `BREAKING CHANGE:` footer describing the migration, not
just the break.

---

## Pull requests

1. Branch from an up-to-date `main`.
2. Push and open a PR using the [template](.github/PULL_REQUEST_TEMPLATE.md).
   Fill in every section; delete none of them.
3. CI must be green.
4. At least one approving review.
5. **Merge with `--no-ff`** (the "Create a merge commit" button). We keep the
   branch topology — it is the cheapest available record of how a change was
   assembled. Do not squash; do not rebase-merge.
6. Delete the branch after merge.

A good PR is under ~400 lines of reviewable diff. Datasets and generated files
do not count toward that — mark them in the description so reviewers skip them.

---

## Releases and tags

Semantic versioning, annotated tags only (never lightweight — an annotated tag
carries the tagger, date and message, and `git describe` prefers it).

```bash
git checkout main && git pull
git tag -a v0.4.0 -m "v0.4.0 — Snowflake foundation"
git push origin v0.4.0
gh release create v0.4.0 --title "v0.4.0 — Snowflake foundation" --notes-file notes.md
```

While the project is pre-1.0, `0.MINOR.PATCH` moves on `MINOR` for each
completed phase of the architecture.

---

## Working with the datasets

The lake is S3. Git holds the code that moves data, plus small extracts.

| File                    | Size    | In git |
| ----------------------- | ------- | ------ |
| `data/orders.csv`       | 1.33 GB | no     |
| `data/order_items.csv`  | 955 MB  | no     |
| `data/menu.xlsx`        | 41 MB   | no — duplicate of `menu.csv` |
| everything else         | ≤ 66 MB | yes    |

The two excluded fact tables are represented by `data/samples/*.csv`, built by
`scripts/make_samples.py`. The sample is the first 5,000 orders plus **every**
line item belonging to them, so the pair still joins cleanly — a random sample
would not, and would be useless for testing the join.

If you change the sample size, rebuild both files in the same commit and say so
in the message. Never hand-edit a sample.

---

## SQL style

- Keywords upper case, identifiers upper case, everything else lower.
- One clause per line; leading commas in long select lists.
- CTEs over nested subqueries. Name them for what they contain — `paid_orders`,
  not `cte1`.
- Every script starts with `USE ROLE` / `USE DATABASE` / `USE SCHEMA` so it is
  unambiguous where it runs.
- `CREATE OR REPLACE` for stateless objects (formats, stages, views).
  `CREATE ... IF NOT EXISTS` for anything holding state (databases, warehouses,
  roles) — replacing those destroys grants.
- Comment the *why*. `-- skip the header row` is noise; `-- source CSVs keep
  their header, so SKIP_HEADER = 1` is not.

---

## Secrets

Nothing that authenticates you goes into git. That includes AWS account IDs in
example ARNs — use `<ACCOUNT_ID>`, as the committed IAM policies do.

Where things actually live:

| Secret                       | Home                                     |
| ---------------------------- | ---------------------------------------- |
| Snowflake key pair (`.p8`)   | local disk, `chmod 600`, gitignored      |
| dbt credentials              | `~/.dbt/profiles.yml`, outside the repo  |
| OpenAI API key               | `.env`, gitignored                       |
| AWS credentials              | `~/.aws/credentials` or an instance role |
| Airflow connections          | Airflow's own secrets backend            |

If you commit a credential: rotate it first, then rewrite history. In that
order. Rotation is what actually protects you — a pushed secret must be assumed
read the moment it lands.
