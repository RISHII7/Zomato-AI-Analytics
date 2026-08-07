# Model reference

Every dbt model, test and source in the project. Figures come from
`target/manifest.json`, not from counting by hand.

```text
models 17  |  tests 16  |  sources 8
```

- [Sources](#sources)
- [Staging — Silver](#staging--silver)
- [Marts — Gold](#marts--gold)
- [Tests](#tests)
- [Conventions](#conventions)
- [Known defects](#known-defects)

---

## Sources

Eight, across two schemas.

| Source | Table | Origin |
| ------ | ----- | ------ |
| `raw` | `restaurants`, `users`, `food`, `menu`, `orders`, `order_items`, `reviews` | `COPY INTO` from S3 |
| `ai` | `review_enriched` | written by `ai/enrich_reviews.py` |

The `ai` source is declared rather than referenced directly so the LLM step
stays in the lineage graph instead of appearing from nowhere.

---

## Staging — Silver

All views. Thin work — casting, renaming, splitting — so there is nothing worth
materialising.

| Model | Reads | Does |
| ----- | ----- | ---- |
| `stg_restaurants` | `raw.restaurants` | `'--'` → NULL, `'₹ 200'` → 200, city split, ordinal `rating_count` |
| `stg_users` | `raw.users` | **drops `password`**, lowercases email, types age/family size |
| `stg_food` | `raw.food` | `initcap` on `veg_or_non_veg` |
| `stg_menu` | `raw.menu` | types price, filters non-positive |
| `stg_orders` | `raw.orders` | city split, **adds `is_delivered`** |
| `stg_order_items` | `raw.order_items` | types the money columns |
| `stg_reviews` | `raw.reviews`, `stg_restaurants` | joins for city, drops null comments |

Two choices worth naming:

**`stg_users` drops `password` rather than masking it.** A masked column still
carries the shape of the secret and still has to be excluded from every
downstream select by hand. Nothing needs it, so nothing downstream can leak it.

**`stg_orders` exposes `is_delivered` rather than filtering.** Cancelled and
refunded orders keep a populated `sales_amount` — about 8.5% of the table.
Filtering here would hide the problem and make cancellation rate unanswerable.
The flag pushes the decision into the marts where it is visible.

---

## Marts — Gold

### Dimensions

| Model | Materialisation | Reads | Notes |
| ----- | --------------- | ----- | ----- |
| `dim_customer` | `table` | `stg_users` | adds `age_segment` |
| `dim_restaurants` | `table` | `stg_restaurants` | |
| `dim_food` | `table` | `stg_food` | |
| `dim_date` | `table` | *generated* | spine, 2024-01-01 → 2026-12-31 |

`dim_date` is a generated spine, not derived from observed order dates.
Deriving it is the common shortcut and it is wrong in a specific way: days with
no orders would be missing, so a time series grouped through the dimension
silently closes the gap rather than showing a zero — the chart looks like
business as usual on a day the platform was down.

`is_weekend` uses `DAYOFWEEKISO`, not `DAYOFWEEK`. The latter is affected by the
`WEEK_START` session parameter, so the same model would produce different
weekends depending on who ran it.

### Facts

| Model | Materialisation | Strategy | Reads |
| ----- | --------------- | -------- | ----- |
| `fct_orders` | `incremental` | `merge` on `order_id` | `stg_orders` |
| `fact_order_items` | `incremental` | `merge` on `order_item_id` | `stg_order_items`, `stg_orders` |

Incremental rather than `table` is the difference between a viable pipeline and
an unusable one: a full refresh of `fact_order_items` joins 23 M line items to
10 M orders. `merge` on a unique key makes reruns idempotent.

### Aggregates

| Model | Grain | Reads |
| ----- | ----- | ----- |
| `mart_daily_city_revenune` | `order_date` × `city` | `fct_orders` |
| `mart_restaurant_performance` | restaurant | `fct_orders`, `dim_restaurants` |
| `mart_delivery_sla` | `city` × `order_hour` | `fct_orders` |
| `mart_review_insights` | `city` × `topic` × `sentiment_label` | `ai.review_enriched`, `stg_reviews` |

`mart_review_insights` is the only model carrying `tags=['ai']`. The Airflow DAG
builds everything else first (`--exclude tag:ai`), runs enrichment, then builds
this one (`--select tag:ai`) — because it reads a table that does not exist
until enrichment has written to it.

All money measures are guarded:

```sql
sum(iff(is_delivered, sales_amount, 0))   -- correct
sum(sales_amount)                          -- overstates by ~8.5%
```

`mart_delivery_sla` reports p50 and p90 rather than a mean, because delivery
time is right-skewed and the interesting failures are in the tail.

---

## Tests

16 total.

| Kind | Count |
| ---- | ----- |
| `not_null` | 8 |
| `unique` | 6 |
| `relationships` | 1 |
| `accepted_values` | 1 |

### What is deliberately not tested

This dataset punishes the obvious assertions. Three that look right and would
fail on *correct* data:

| Tempting test | Why it is wrong |
| ------------- | --------------- |
| `not_null` on `stg_orders.delivery_time_min` | NULL means the order never arrived — absence of an event, not missing data. NULL on 8.5% of rows **by design**. |
| `unique` on `stg_menu.menu_id` | `menu_id` looks like a key and is not; `mn0` repeats. The grain is `(r_id, f_id)`. |
| `sales_amount` = sum of its parts | The generator rounds at different points; the identity fails on real rows. |

A test that fails for a known-acceptable reason is worse than no test — it
trains everyone to skim past red, and then the one failure that mattered goes
through with the rest.

### Referential integrity — measured

`fct_orders.customer_id` carries a `relationships` test to `dim_customer`. It
passes. Verified by scanning **all 10,000,000 orders** against both dimension
sources:

| Foreign key | Distinct in dimension | Orphans |
| ----------- | --------------------- | ------- |
| `orders.user_id` → `users.user_id` | 100,000 | **0** |
| `orders.r_id` → `restaurant.id` | 148,541 | **0** |

So an inner join to either dimension drops nothing and no unknown-member row is
needed. `restaurant_id` deserves the same test and does not have one yet.

### `accepted_values` is load-bearing

`order_status` ∈ `{Delivered, Cancelled, Refunded}` is not cosmetic.
`is_delivered` derives from `order_status`, and every revenue measure is guarded
by `is_delivered`. A fourth status appearing upstream would fall outside the
guard and be silently counted as not-delivered, suppressing real revenue. This
test catches that on the first run after it happens.

---

## Conventions

| | |
| --- | --- |
| **Staging** | `view`, schema `staging` |
| **Marts** | `table`, schema `marts` |
| **Facts** | `incremental`, `merge`, `on_schema_change: append_new_columns` |
| **Naming** | `stg_`, `dim_`, `fct_`/`fact_`, `mart_` |
| **Sources** | always `source()`, never a hard-coded `ZOMATO.RAW.x` |
| **Casting** | `TRY_*` where RAW landed a STRING, plain casts where RAW typed it |

`macros/generate_schema_name.sql` overrides dbt's default so a custom schema
name is used verbatim. Without it, `+schema: staging` builds `staging_staging`
and creates it on the fly, while `snowflake/01_setup.sql` has already
provisioned `STAGING` with grants.

---

## Known defects

Documented rather than hidden. None is currently breaking a build.

### `rating_count` loses the K multiplier

`stg_restaurants` parses with `regexp_substr(rating_count, '[0-9]+')`, which
reads the digits before the `K` and drops the magnitude:

| source value | rows | model gives | should be |
| ------------ | ---- | ----------- | --------- |
| `1K+ ratings` | 2,739 | **1** | 1000 |
| `5K+ ratings` | 98 | **5** | 5000 |
| `10K+ ratings` | 15 | **10** | 10000 |

2,852 rows wrong — and **the ordering inverts**: `1K+ ratings` scores 1 while
`500+ ratings` scores 500, so the busiest restaurants rank below the quietest.
`rating_count` is ordinal text, so the durable fix is a `CASE` mapping to an
ordered category rather than a better regex.

### The incremental watermark drops rows permanently

Both facts filter with a strict `>` against the high-water mark.
`order_timestamp` is second-resolution and **not unique** — measured over
1,000,000 orders:

```text
distinct timestamps            987,579
orders sharing a timestamp      24,712  (2.47%)
most orders on one second            3
```

When a batch boundary lands on a shared second, the orders not in the first
batch are excluded by `>` and never appear in any later run — the watermark has
moved past them. The run succeeds and the count looks plausible.

`>=` is the fix, and it is safe *because* the strategy is `merge` on a unique
key: overlapping rows re-merge onto themselves rather than duplicating.

**No test catches this.** Uniqueness detects duplicated rows; nothing detects
missing ones. Closing it needs a reconciliation count against staging.

### `avg(customer_rating)` is biased upward

`mart_restaurant_performance` averages `customer_rating` with no response rate
beside it. That column is NULL on 30% of *delivered* orders because those
customers did not rate — and that absence is not random, since unhappy
customers rate more often. The arithmetic is right; the number means less than
it appears to.

### Smaller items

- **`models.snapshot.zomato`** is nested under `models:` in `dbt_project.yml`, so
  dbt reads `snapshot` as a project name and discards the block. Reported as an
  unused configuration path. Inert — there are no snapshots yet — but a snapshot
  added later would land in the default schema rather than `SNAPSHOTS`.
- **Naming disagreement** — `fct_orders` against `fact_order_items`.
- **Filename typo** — `mart_daily_city_revenune` ships to Snowflake as
  `MART_DAILY_CITY_REVENUNE`.
- **`stg_menu` silently drops 1,272 rows** (0.11% of 1,179,936) via
  `WHERE try_to_decimal(price,10,2) > 0`, with no test behind the filter.
- **`stg_reviews` emits `restaurant_id` as STRING** while the other three models
  emit NUMBER.
- **`mart_review_insights` joins STRING to NUMBER** — `REVIEW_ENRICHED.REVIEW_ID`
  is declared STRING by `enrich_reviews.py`. Snowflake coerces today.
- **`cancel_rate` counts only `'Cancelled'`**, excluding the separate 2.5% of
  `'Refunded'` orders.
