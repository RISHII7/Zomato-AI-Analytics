# Runbook

End-to-end setup, from an empty AWS account to a working warehouse, orchestrated
pipeline and three AI applications. Roughly two hours, most of it the
`order_items` upload and `COPY`.

Design reasoning lives in [ARCHITECTURE.md](ARCHITECTURE.md); the AWS-side
detail in [aws/README.md](../aws/README.md). This is the sequence to execute.

| Phase | Steps | Time |
| ----- | ----- | ---- |
| **2 — Raw into Snowflake** | 1–6 | ~90 min |
| **3 — dbt** | 7–8 | ~15 min |
| **4 — Airflow** | 9 | ~10 min |
| **5 — AI lane** | 10 | ~5 min |

---

## Contents

**Phase 2 — raw data into Snowflake**

- [Before you start](#before-you-start)
- [Step 1 — S3 bucket and upload](#step-1--s3-bucket-and-upload)
- [Step 2 — Snowflake warehouse, database, schemas, role](#step-2--snowflake-warehouse-database-schemas-role)
- [Step 3 — The trust handshake](#step-3--the-trust-handshake)
- [Step 4 — Stage and file format](#step-4--stage-and-file-format)
- [Step 5 — RAW tables](#step-5--raw-tables)
- [Step 6 — Load](#step-6--load)
- [Verification](#verification)

**Phase 3 — dbt**

- [Step 7 — Configure the dbt profile](#step-7--configure-the-dbt-profile)
- [Step 8 — Build the warehouse](#step-8--build-the-warehouse)

**Phase 4 — Airflow**

- [Step 9 — Start the stack](#step-9--start-the-stack)

**Phase 5 — AI lane**

- [Step 10 — Run the three apps](#step-10--run-the-three-apps)

**Reference**

- [Troubleshooting](#troubleshooting)
- [Teardown](#teardown)

---

## Before you start

| You need | Notes |
| -------- | ----- |
| An AWS account | Free tier is enough; ~2.4 GB in S3 Standard |
| A Snowflake account | Trial gives $400 in credits, ample for this |
| AWS CLI, configured | The console times out uploading the 1.33 GB file |
| The raw CSVs | `orders.csv` and `order_items.csv` are not in git — see [DATA_DICTIONARY.md](DATA_DICTIONARY.md) |

Two values recur throughout. Decide them now:

- `<BUCKET>` — globally unique, e.g. `zomato-dl-yourname`
- `<ACCOUNT_ID>` — your 12-digit AWS account ID

Run every SQL step in a Snowsight worksheet as `ACCOUNTADMIN`. Steps 2–6 are
the five scripts in [`snowflake/`](../snowflake/), in order.

---

## Step 1 — S3 bucket and upload

Create the bucket in **the same region as your Snowflake account**.
Cross-region reads are slower and incur transfer cost.

```bash
aws s3 mb s3://<BUCKET> --region ap-south-1
aws s3api put-public-access-block --bucket <BUCKET> \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

Upload one file per prefix:

```bash
aws s3 cp data/restaurant.csv   s3://<BUCKET>/raw/restaurants/
aws s3 cp data/users.csv        s3://<BUCKET>/raw/users/
aws s3 cp data/food.csv         s3://<BUCKET>/raw/food/
aws s3 cp data/menu.csv         s3://<BUCKET>/raw/menu/
aws s3 cp data/reviews.csv      s3://<BUCKET>/raw/reviews/
aws s3 cp data/orders.csv       s3://<BUCKET>/raw/orders/        # 1.33 GB
aws s3 cp data/order_items.csv  s3://<BUCKET>/raw/order_items/   #  955 MB
```

> **The prefix is `restaurants/`, plural, while the file is `restaurant.csv`.**
> Prefixes match the RAW table names, not the filenames. Getting this wrong
> produces an empty `COPY` — zero rows, no error — which is the single most
> confusing outcome in this runbook.

Confirm all seven:

```bash
aws s3 ls s3://<BUCKET>/raw/ --recursive --human-readable
```

---

## Step 2 — Snowflake warehouse, database, schemas, role

Run [`snowflake/01_setup.sql`](../snowflake/01_setup.sql).

Creates `ZOMATO_WH` (XSMALL, `AUTO_SUSPEND = 60`), the `ZOMATO` database, six
schemas, and `DBT_ROLE`.

The schemas map onto the medallion architecture:

| Schema | Holds |
| ------ | ----- |
| `RAW` | Landing zone. Everything STRING or loosely typed, straight from CSV. |
| `BRONZE` | Iceberg tables, for the Spark path |
| `STAGING` | Cleaned and conformed — dbt |
| `MARTS` | Gold: facts, dimensions — dbt |
| `SNAPSHOTS` | SCD2 history — dbt |
| `AI` | LLM-enriched tables |

Two details in this script are deliberate:

- **`AUTO_SUSPEND = 60`.** The warehouse idles down after a minute. On a trial
  account the fastest way to lose credits is a warehouse left running
  overnight after a session.
- **Everything uses `IF NOT EXISTS`.** These objects hold state. `CREATE OR
  REPLACE ROLE DBT_ROLE` would succeed and silently drop every grant attached
  to it, and you would find out later, from a permissions error somewhere
  unrelated.

Expect `setup complete`.

---

## Step 3 — The trust handshake

The circular part. Snowflake will not reveal its IAM principal until an
integration exists; the integration will not accept a role ARN that does not.
Four steps break the cycle.

### A. Create the role with the bootstrap trust policy

IAM → Roles → Create role → Custom trust policy. Paste
[`aws/iam/snowflake-role-trust-policy-initial.json`](../aws/iam/snowflake-role-trust-policy-initial.json),
substituting `<ACCOUNT_ID>`.

Attach an inline policy from
[`aws/iam/s3-read-policy.json`](../aws/iam/s3-read-policy.json), substituting
`<BUCKET>`. Name the role `snowflake-zomato-role`.

### B. Create the storage integration

Edit [`snowflake/02_storage_integration.sql`](../snowflake/02_storage_integration.sql)
with the role ARN and bucket, then run it.

### C. Read back what Snowflake generated

```sql
DESC INTEGRATION ZOMATO_S3_INT;
```

Two rows matter:

| Property | Goes into |
| -------- | --------- |
| `STORAGE_AWS_IAM_USER_ARN` | the `"AWS"` principal of the trust policy |
| `STORAGE_AWS_EXTERNAL_ID` | the `sts:ExternalId` condition |

### D. Pin the trust policy

Replace the role's trust policy with
[`aws/iam/snowflake-role-trust-policy-final.json`](../aws/iam/snowflake-role-trust-policy-final.json),
substituting both values from step C.

> **Step D is not optional.** Until it is done, the role trusts `:root` and any
> principal in the account can assume it. And the `sts:ExternalId` condition is
> what prevents a confused-deputy attack — Snowflake shares one IAM principal
> across accounts on a deployment, so without the external ID another customer
> could point their integration at your role ARN and have Snowflake read your
> bucket for them. [aws/README.md](../aws/README.md) explains this in full.

IAM propagation takes 10–20 seconds. If step 4 denies immediately, wait and
retry before changing anything.

---

## Step 4 — Stage and file format

Run [`snowflake/03_stage_and_formats.sql`](../snowflake/03_stage_and_formats.sql)
after substituting `<BUCKET>`.

Creates `CSV_FMT` and the external stage `ZOMATO_RAW_STAGE` over
`s3://<BUCKET>/raw/`.

The file-format options are each there for a specific property of this data:

| Option | Why |
| ------ | --- |
| `SKIP_HEADER = 1` | Every uploaded CSV keeps its header row |
| `FIELD_OPTIONALLY_ENCLOSED_BY = '"'` | `restaurant.address` and `reviews.comment` contain commas inside quotes |
| `EMPTY_FIELD_AS_NULL = TRUE` | Cancelled orders have genuinely empty rating/delivery fields |
| `ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE` | **One** row in `food.csv` (of 371,561) is short a trailing field |
| `TRIM_SPACE = FALSE` | Preserves source values exactly; trimming belongs in STAGING |

That last one is worth dwelling on. Without it, a single malformed row out of
371,561 aborts the entire `food` load. With it, the row NULL-fills and a
`not_null` test in STAGING catches it in daylight — rather than a 3 a.m. page
for one bad row in a file that is 99.9997% fine.

The stage is `CREATE OR REPLACE` because it holds no state: it is a pointer at
a prefix. Replacing it costs nothing.

Verify:

```sql
LIST @ZOMATO.RAW.ZOMATO_RAW_STAGE;
```

Seven folders. If not, go to [Troubleshooting](#troubleshooting) — do not
continue.

---

## Step 5 — RAW tables

Run [`snowflake/04_raw_tables.sql`](../snowflake/04_raw_tables.sql).

**Column order matters more than column names.** `COPY INTO` without a
transform loads by position, so these definitions must match the CSVs
field-for-field.

Two things follow from that:

- The four **dimension** files (`restaurant`, `users`, `food`, `menu`) carry a
  leading unnamed index column from a pandas `to_csv()` without
  `index=False`. Each table therefore starts with a throwaway `_idx` column
  and STAGING drops it. The three **fact** files do not have it. Getting this
  wrong shifts every column by one and loads silently.
- `users` columns are renamed positionally at landing (`Marital Status` →
  `marital_status`). Because the COPY is by position, the rename is free, and
  it means nothing downstream needs quoted identifiers forever.

Dimensions land as all-`STRING`. That is intentional: `restaurant.rating`
holds `'--'` in 87,014 rows and `cost` holds `'₹ 200'`. Typing them at RAW
would fail the load. RAW's job is to land the data faithfully; STAGING's job
is to make it correct.

Facts land properly typed, because they are generated and clean.

---

## Step 6 — Load

Run [`snowflake/05_copy_into.sql`](../snowflake/05_copy_into.sql). This is the
long step — 15–40 minutes on XSMALL, nearly all of it `order_items`.

Error handling differs per table, and the split is the point:

```sql
-- Scraped, messy -> tolerate and skip bad rows
COPY INTO RAW.restaurants ... ON_ERROR = 'CONTINUE';

-- Generated, clean -> stay strict so counts are exact
COPY INTO RAW.orders      ... ON_ERROR = 'ABORT_STATEMENT';
```

A single global setting would be wrong in one direction or the other.
`CONTINUE` on the facts would let rows vanish silently from a table whose row
count is supposed to be exactly 10,000,000. `ABORT_STATEMENT` on the
dimensions would let one bad row in `food.csv` block the entire pipeline.

To go faster, resize before this step and back after:

```sql
ALTER WAREHOUSE ZOMATO_WH SET WAREHOUSE_SIZE = 'MEDIUM';
-- run the COPY statements
ALTER WAREHOUSE ZOMATO_WH SET WAREHOUSE_SIZE = 'XSMALL';
```

MEDIUM costs 4× per second but is roughly 4× faster on a load this size, so
total credits are about the same and the wall-clock is much shorter. Only
worth it if you are watching it run.

---

## Verification

The script ends with a count. Expected:

| Table | Rows |
| ----- | ---- |
| `orders` | 10,000,000 |
| `order_items` | 22,998,179 |
| `menu` | 1,179,936 |
| `food` | 371,561 |
| `reviews` | 300,000 |
| `restaurants` | ~148,541 |
| `users` | 100,000 |

The fact tables must be **exact** — they loaded with `ABORT_STATEMENT`, so any
shortfall means something is wrong. The dimensions may be a few rows short;
they loaded with `CONTINUE`. To see what was skipped:

```sql
SELECT * FROM TABLE(VALIDATE(RAW.restaurants, JOB_ID => '_last'));
```

Then check the load actually landed in the right columns — a positional load
that is off by one still reports success:

```sql
SELECT _idx, id, name, city, rating FROM RAW.restaurants LIMIT 5;
-- _idx should be 0,1,2...; id should be a 6-digit number; rating '--' or '4.4'

SELECT order_id, order_timestamp, sales_amount, order_status FROM RAW.orders LIMIT 5;
-- order_id dense from 1; order_status one of Delivered/Cancelled/Refunded
```

If `name` holds a city, the `_idx` column is misaligned — the CSV had no index
column, or the table has a spurious `_idx`.

---

## Step 7 — Configure the dbt profile

dbt reads credentials from `~/.dbt/profiles.yml`, outside the repo. Create it
with a profile named `zomato`:

```yaml
zomato:
  target: dev
  outputs:
    dev:
      type: snowflake
      account: <ACCOUNT>          # ab12345.ap-south-1 — not the full URL
      user: <USER>
      password: <PASSWORD>        # or private_key_path for key-pair auth
      role: DBT_ROLE              # never ACCOUNTADMIN
      warehouse: ZOMATO_WH
      database: ZOMATO
      schema: staging
      threads: 4
```

Install and verify:

```bash
pip install "dbt-snowflake==1.12.0"
cd zomato
dbt debug        # checks the connection
dbt parse        # checks the project compiles
```

`dbt debug` should report `All checks passed!`.

> **Two `dbt` binaries can coexist on a machine.** `dbt-fusion` (the Rust
> rewrite) takes `dbt init --project-name x` and rejects a positional argument
> with a clap-style *"unexpected argument"* error that looks nothing like
> dbt-core's. This project targets **dbt-core**. Check with `dbt --version` —
> it should report `Core: 1.12.0` and `snowflake: 1.12.0`.

### Expect one warning

```text
[WARNING]: Configuration paths exist in your dbt_project.yml file which
do not apply to any resources.
There are 1 unused configuration paths:
- models.snapshot.zomato
```

Known and inert — see [MODELS.md](MODELS.md#smaller-items). `dbt parse` still
exits 0.

## Step 8 — Build the warehouse

```bash
cd zomato

dbt build --exclude tag:ai      # everything except the AI mart
```

`--exclude tag:ai` matters: `mart_review_insights` reads `ZOMATO.AI.REVIEW_ENRICHED`,
which does not exist until the enrichment task has run. Building it now
produces an error or a stale result.

Expect 16 models and 16 tests. The first run is the expensive one — the fact
tables build in full before switching to incremental.

To go faster, resize before and after:

```sql
ALTER WAREHOUSE ZOMATO_WH SET WAREHOUSE_SIZE = 'MEDIUM';
-- run dbt build
ALTER WAREHOUSE ZOMATO_WH SET WAREHOUSE_SIZE = 'XSMALL';
```

Verify:

```sql
SELECT COUNT(*) FROM ZOMATO.MARTS.FCT_ORDERS;        -- 10,000,000
SELECT COUNT(*) FROM ZOMATO.MARTS.FACT_ORDER_ITEMS;  -- 22,998,179
SELECT * FROM ZOMATO.MARTS.MART_DAILY_CITY_REVENUNE LIMIT 5;
```

---

## Step 9 — Start the stack

```bash
cd airflow
cp .env.example .env        # fill in SNOWFLAKE_* and GEMINI_API_KEY
docker compose build
docker compose up -d
```

UI at <http://localhost:8080>, login `admin` / `admin`.

Five containers: `postgres`, `airflow-init`, `apiserver`, `scheduler`,
`dag-processor`. Check them:

```bash
docker compose ps
```

Enable the `zomato_batch` DAG in the UI and trigger a run. Task order:

```text
reload_raw → dbt_build_core → enrich_reviews → dbt_build_ai
```

`reload_raw` is safe to repeat — Snowflake tracks load metadata per file for 64
days and skips files it has already ingested, so an unchanged bucket is a no-op
rather than a duplicate load.

> **Port 8080 already allocated?** Another compose project is holding it. Either
> stop that stack (`docker compose -p <name> down`) or remap this one's host
> port. Only the host side may change — `AIRFLOW__CORE__EXECUTION_API_SERVER_URL`
> points at `http://apiserver:8080/execution/`, which is container-to-container
> and must stay 8080.

---

## Step 10 — Run the three apps

```bash
cd ai
cp .env.example .env        # SNOWFLAKE_* and GEMINI_API_KEY
pip install google-genai snowflake-connector-python python-dotenv \
            streamlit pandas numpy pyarrow
```

Get a key from <https://aistudio.google.com/apikey>.

### Enrichment

```bash
python enrich_reviews.py
```

Creates `ZOMATO.AI.REVIEW_ENRICHED` and classifies `SAMPLE_N` reviews. Idempotent
— the select excludes `review_id`s already enriched. Then build the AI mart:

```bash
cd ../zomato && dbt build --select tag:ai
```

### RAG chat

```bash
streamlit run rag_chat.py
```

First load reads 100 reviews from `STG_REVIEWS`, embeds them, and caches the
vectors to `review_embeddings.parquet`. Subsequent runs read the cache.

### Text-to-SQL

```bash
streamlit run text_to_sql.py
```

> **Both apps must be launched with `streamlit run`.** `python app.py` executes
> the file bare — no server, no session state — and emits a wall of
> `missing ScriptRunContext` warnings. Add `--server.address localhost` for
> local work: Streamlit binds `0.0.0.0` by default and prints an external URL,
> which on an untrusted network exposes an app that queries your warehouse.

See [AI_LANE.md](AI_LANE.md) for what each capability does and where its limits
are.

---

## Troubleshooting

### `LIST @stage` returns Access Denied

Trust policy. In order of likelihood:

1. **Step D was never done** — the role still trusts `:root`.
   ```bash
   aws iam get-role --role-name snowflake-zomato-role \
     --query 'Role.AssumeRolePolicyDocument'
   ```
2. **The external ID no longer matches.** `CREATE OR REPLACE STORAGE
   INTEGRATION` mints a *new* one. If you re-ran step B after step D, the
   policy is stale. Re-run `DESC INTEGRATION` and update it.
3. **IAM has not propagated.** Wait 30 seconds.

### `LIST @stage` returns nothing, no error

Trust is fine — the path is wrong. Check the stage URL ends in `/raw/`, and
that the files are under prefixes rather than at the bucket root.

### Folders list, but `COPY` denies

`ListBucket` is granted and `GetObject` is not. The permission policy needs
both statements — the object one with `<BUCKET>/*`, the bucket one with the
bare ARN. See [aws/README.md](../aws/README.md#permission-policy--read-only-two-statements).

### `COPY` succeeds, loads 0 rows

Almost always the prefix mismatch: `restaurants/` vs `restaurant/`.

```sql
LIST @ZOMATO_RAW_STAGE/restaurants/;
```

### Column-count mismatch on `food`

`ERROR_ON_COLUMN_COUNT_MISMATCH` did not take effect — the stage is using a
different file format. Re-run step 4; `CREATE OR REPLACE STAGE` is safe.

### Everything worked yesterday, denies today

The integration was re-created. See #2 above.

### Timestamps load as NULL

`order_timestamp` is `YYYY-MM-DD HH:MM:SS`, which Snowflake handles natively.
If they are NULL, the column is misaligned — check `_idx`.

---

## Teardown

Snowflake:

```sql
USE ROLE ACCOUNTADMIN;
DROP DATABASE IF EXISTS ZOMATO;
DROP WAREHOUSE IF EXISTS ZOMATO_WH;
DROP INTEGRATION IF EXISTS ZOMATO_S3_INT;
DROP ROLE IF EXISTS DBT_ROLE;
```

AWS:

```bash
aws s3 rm s3://<BUCKET> --recursive
aws s3 rb s3://<BUCKET>
aws iam delete-role-policy --role-name snowflake-zomato-role --policy-name s3-read
aws iam delete-role --role-name snowflake-zomato-role
```

Storage costs pennies a month; a warehouse left running does not. If you are
pausing rather than finishing, `AUTO_SUSPEND = 60` already handles it — the
warehouse costs nothing while idle.
