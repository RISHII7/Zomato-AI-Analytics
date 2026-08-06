# Data dictionary

Seven source tables, ~33.6 million rows, 2.4 GB on disk. Everything below was
measured against the actual files, not inferred from column names — the null
patterns and sentinel values in particular are the things that will break a
join or silently skew an average if you assume them away.

**Observation window:** `2024-01-01` → `2026-06-30` (912 distinct dates) for
both `orders` and `reviews`.

---

## Contents

- [At a glance](#at-a-glance)
- [What is committed to git](#what-is-committed-to-git)
- [Facts](#facts) — [orders](#orders) · [order_items](#order_items) · [reviews](#reviews)
- [Dimensions](#dimensions) — [restaurant](#restaurant) · [users](#users) · [food](#food) · [menu](#menu)
- [Relationships](#relationships)
- [Known data-quality issues](#known-data-quality-issues)

---

## At a glance

| Table          | Rows       | Size    | Grain                        | Type      |
| -------------- | ---------- | ------- | ---------------------------- | --------- |
| `orders`       | 10,000,000 | 1.33 GB | one order                    | fact      |
| `order_items`  | 22,998,179 | 955 MB  | one line item within an order | fact      |
| `reviews`      | 300,000    | 25 MB   | one review of one order      | fact      |
| `menu`         | 1,179,936  | 66 MB   | one dish offered by one restaurant | bridge |
| `food`         | 371,561    | 17 MB   | one dish                     | dimension |
| `restaurant`   | 148,541    | 46 MB   | one restaurant               | dimension |
| `users`        | 100,000    | 11 MB   | one customer                 | dimension |

Two shapes of source data are mixed here, and they behave differently:

- **Generated facts** — `orders`, `order_items`, `reviews`. Clean, typed,
  internally consistent. Loaded with `ON_ERROR = 'ABORT_STATEMENT'` so the row
  counts are exact.
- **Scraped dimensions** — `restaurant`, `users`, `food`, `menu`. Real-world
  mess: sentinel strings, currency symbols inside numbers, a leading unnamed
  index column, one ragged row. Loaded with `ON_ERROR = 'CONTINUE'`.

That split is why `snowflake/05_copy_into.sql` uses different error handling
per table rather than one global setting.

---

## What is committed to git

| File                            | In git | Why |
| ------------------------------- | ------ | --- |
| `data/food.csv`                 | yes    | 17 MB |
| `data/menu.csv`                 | yes    | 66 MB |
| `data/restaurant.csv`           | yes    | 46 MB |
| `data/reviews.csv`              | yes    | 25 MB |
| `data/users.csv`                | yes    | 11 MB |
| `data/samples/orders_sample.csv`      | yes | 5,000 rows |
| `data/samples/order_items_sample.csv` | yes | 11,450 rows |
| `data/orders.csv`               | **no** | 1.33 GB — past GitHub's 100 MB hard limit |
| `data/order_items.csv`          | **no** | 955 MB — same |
| `data/menu.xlsx`                | **no** | 41 MB, byte-equivalent to `menu.csv` |

The two excluded fact tables live in S3 under `s3://<BUCKET>/raw/orders/` and
`s3://<BUCKET>/raw/order_items/`. Rebuild the committed extracts with:

```bash
python scripts/make_samples.py            # default 5,000 orders
python scripts/make_samples.py --check    # verify without writing
```

The sample is the first 5,000 orders plus **every** line item belonging to
them — not a random sample. A random sample would break the `orders` ↔
`order_items` join, which is the one thing the extract exists to let you test.

---

## Facts

### `orders`

10,000,000 rows. One row per order. No index column; loads by position.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `order_id` | NUMBER | Primary key. Dense, 1-based, no gaps. |
| `order_timestamp` | TIMESTAMP_NTZ | Local time, no zone. 199,494 distinct values in the first 200k rows — effectively second-resolution. |
| `order_date` | DATE | Redundant with `order_timestamp`; kept because every mart groups by it. |
| `user_id` | NUMBER | → `users.user_id`. |
| `r_id` | NUMBER | → `restaurant.id`. Note the name: `r_id` here, `id` there, `restaurant_id` in `reviews`. |
| `restaurant_city` | STRING | 812 distinct. **Not clean** — mixes bare cities (`Bikaner`) with locality-qualified ones (`Indiranagar,Bangalore`) and numbered zones (`Noida-1`). See [known issues](#city-is-not-a-city). |
| `cuisine` | STRING | 1,853 distinct. Comma-separated multi-value: `North Indian,Chinese`. Denormalise in STAGING. |
| `items_count` | NUMBER | Distinct line items. 1–6. |
| `sales_qty` | NUMBER | Total units. 1–13. Always ≥ `items_count`. |
| `subtotal` | NUMBER | Pre-discount, pre-tax. INR. |
| `discount` | NUMBER | Absolute INR, not a rate. 0 is the modal value. |
| `delivery_fee` | NUMBER | 6 distinct values (0, 15, 20, …). 0 is modal. |
| `gst` | NUMBER | Absolute INR tax. |
| `sales_amount` | NUMBER | The one to sum. See the [arithmetic caveat](#sales_amount-is-not-a-derivable-column). |
| `currency` | STRING | `INR` for every row. Constant — drop it in STAGING. |
| `payment_method` | STRING | 5 values: UPI (modal), Card, Wallet, Netbanking, COD. |
| `order_status` | STRING | 3 values: `Delivered`, `Cancelled`, `Refunded`. |
| `customer_rating` | NUMBER | 1.0–5.0. **~36% null, structurally.** See below. |
| `delivery_time_min` | NUMBER | Minutes. **~8.5% null, structurally.** See below. |

#### The nulls are structural, not missing data

Measured over 300,000 rows, `order_status` fully determines whether these two
columns are populated:

| `order_status` | `customer_rating` | `delivery_time_min` | Share |
| -------------- | ----------------- | ------------------- | ----- |
| `Delivered`    | present           | present             | 64.0% |
| `Delivered`    | **null**          | present             | 27.6% |
| `Cancelled`    | **null**          | **null**            | 6.0%  |
| `Refunded`     | **null**          | **null**            | 2.5%  |

There is no row where a cancelled or refunded order carries a rating or a
delivery time, and no delivered order is missing its delivery time. So:

- **`delivery_time_min IS NULL` ⟺ the order never arrived.** It is not missing
  data; it is the absence of an event. Averaging delivery time across all
  orders is correct only because the nulls are excluded by definition.
- **`customer_rating IS NULL` on a delivered order means the customer did not
  rate it** — 30% of delivered orders. This one *is* missing data, and it is
  not missing at random: unhappy customers rate more often. `AVG(customer_rating)`
  is a biased estimate of satisfaction. Report response rate alongside it.

A `not_null` test on either column would be wrong. Test the *implication*
instead: cancelled ⟹ null.

### `order_items`

22,998,179 rows. One row per dish per order. Sorted by `order_id`, which is
what lets `scripts/make_samples.py` stop early instead of streaming 23 M rows.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `order_item_id` | NUMBER | Primary key. |
| `order_id` | NUMBER | → `orders.order_id`. ~2.3 line items per order on average. |
| `r_id` | NUMBER | → `restaurant.id`. Denormalised from the parent order. |
| `f_id` | STRING | → `food.f_id`. String key with an `fd` prefix: `fd538029`. |
| `price` | NUMBER | Unit price at time of order. |
| `quantity` | NUMBER | Units of this dish. |
| `line_amount` | NUMBER | `price * quantity`. |

**A dish can repeat within one order.** Order 9 contains `fd419560` twice as
two separate rows. Deduplicating on `(order_id, f_id)` silently drops revenue.
The grain is the line, not the dish.

### `reviews`

300,000 rows — one review per ~33 orders. This is the input to the AI lane.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `review_id` | NUMBER | Primary key. |
| `order_id` | NUMBER | → `orders.order_id`. **Not unique** — 198,011 distinct across 200,000 rows. |
| `user_id` | NUMBER | → `users.user_id`. |
| `restaurant_id` | NUMBER | → `restaurant.id`. Third spelling of the same key. |
| `rating` | NUMBER | 1–5 integer. Distinct from `orders.customer_rating`. |
| `comment` | STRING | Free text. **Only 294 distinct strings across all 300,000 rows.** |
| `review_date` | DATE | `2024-01-01` → `2026-06-30`. |

Rating distribution is strongly positive — 65% are 4 or 5:

| 1 | 2 | 3 | 4 | 5 |
| --- | --- | --- | --- | --- |
| 9.9% | 10.1% | 15.1% | 24.9% | **40.0%** |

`comment` drawn from a pool of 294 templates matters for the AI lane. LLM
enrichment over 300,000 rows would spend tokens re-analysing the same 294
strings ~1,020 times each. Enrich the **distinct comments** and join the result
back — same output, ~0.1% of the cost. Likewise for embeddings: the vector
store needs 294 vectors, not 300,000.

---

## Dimensions

The four dimension files were scraped, and all four carry a **leading unnamed
index column** from a pandas `to_csv()` without `index=False`. `snowflake/04_raw_tables.sql`
lands it as `_idx` and drops it in STAGING. The fact files do not have it.

### `restaurant`

148,541 rows. The messiest table.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `_idx` | STRING | Pandas index. Discard. |
| `id` | NUMBER | Primary key. → `orders.r_id`, `order_items.r_id`, `reviews.restaurant_id`. |
| `name` | STRING | 112,819 distinct. **86 nulls.** Modal: Domino's Pizza, Pizza Hut, KFC. |
| `city` | STRING | 821 distinct, same locality-mixing problem as `orders.restaurant_city`. |
| `rating` | STRING | **`'--'` for 87,014 rows (58.6%)** — a string, not a null, in a numeric column. |
| `rating_count` | STRING | Banded text, not a number: `Too Few Ratings`, `20+ ratings`, `1K+ ratings`. |
| `cost` | STRING | **Currency symbol inside the value**: `₹ 200`. |
| `cuisine` | STRING | Comma-separated multi-value. |
| `lic_no` | STRING | FSSAI licence. Numeric-looking but has leading zeros — keep as STRING. |
| `link` | STRING | Source URL. |
| `address` | STRING | Free text, contains commas — this is why the file format keeps `FIELD_OPTIONALLY_ENCLOSED_BY = '"'`. |
| `menu` | STRING | Relative path (`Menu/567335.json`) to a file not in this dataset. Dead reference. |

Three parsing traps, all in this one table:

- **`rating = '--'`** aligns exactly with `rating_count = 'Too Few Ratings'` —
  87,014 rows in both. It means "not enough ratings to display", not "rated
  zero". Cast to NULL. `TRY_CAST` handles it; a plain `CAST` errors the load.
- **`cost = '₹ 200'`** needs the symbol and space stripped before casting. The
  column is also *per two people*, a Swiggy/Zomato listing convention — it is
  not the cost of an order.
- **`rating_count`** is ordinal, not cardinal. `20+ ratings` cannot become a
  number without inventing one. Map it to an ordered category.

### `users`

100,000 rows. Fully synthetic — see [SECURITY.md](../SECURITY.md).

| Column | Type | Notes |
| ------ | ---- | ----- |
| `_idx` | STRING | Pandas index. Discard. |
| `user_id` | NUMBER | Primary key, dense 1..100,000. |
| `name` | STRING | 71,251 distinct — collisions are expected, not a bug. |
| `email` | STRING | 85,662 distinct across 100,000 users. **`email` is not a unique key.** All `@example.com` / `@example.net` (RFC 2606 reserved). |
| `password` | STRING | Fake plaintext. Authenticates nothing. Masked in STAGING, never loaded onward. |
| `Age` | STRING | 16 distinct, concentrated 22–25. |
| `Gender` | STRING | 2 values. |
| `Marital Status` | STRING | Single, Married, Prefer not to say. |
| `Occupation` | STRING | Student (modal), Employee, Self Employeed *[sic]*, House wife. |
| `Monthly Income` | STRING | Banded: `No Income`, `Below Rs.10000`, `10001 to 25000`, `25001 to 50000`, `More than 50000`. |
| `Educational Qualifications` | STRING | Graduate, Post Graduate, Ph.D, School, Uneducated. |
| `Family size` | STRING | 1–6. |

**Six column names contain spaces**, and one contains a typo the source owns
(`Self Employeed`). Snowflake needs `"Marital Status"` quoted, which is why
`04_raw_tables.sql` renames them positionally at landing — the COPY is by
position, so the rename is free and everything downstream gets `marital_status`.

The demographic skew is severe: overwhelmingly students aged 22–25 with no
income. Any "who orders most" segmentation reflects that skew, not the market.

### `food`

371,561 rows. One row per dish.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `_idx` | STRING | Pandas index. Discard. |
| `f_id` | STRING | Primary key, `fd`-prefixed: `fd0`, `fd538029`. |
| `item` | STRING | Dish name. Free text. |
| `veg_or_non_veg` | STRING | `Veg` (72.9%), `Non-veg` (27.1%), and **one blank**. |

**One row is ragged** — a single record is short a trailing field. That one row
is the entire reason `snowflake/03_stage_and_formats.sql` sets
`ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE`: without it, one malformed row out of
371,561 aborts the whole COPY. With it, the row NULL-fills and a `not_null`
test in STAGING catches it in daylight instead of at 3 a.m.

### `menu`

1,179,936 rows. The restaurant ↔ dish bridge.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `_idx` | STRING | Pandas index. Discard. |
| `menu_id` | STRING | **Not unique.** `mn0` repeats across rows. Not a usable key. |
| `r_id` | STRING | → `restaurant.id`. |
| `f_id` | STRING | → `food.f_id`. |
| `cuisine` | STRING | Comma-separated. Duplicated from `restaurant.cuisine`. |
| `price` | STRING | Menu price, e.g. `40.0`. Differs from `order_items.price`, which is the price actually charged. |

`menu_id` looking like a key and not being one is the trap here. The real grain
is `(r_id, f_id)`. Test uniqueness on that pair, not on `menu_id`.

---

## Relationships

```
users ──< orders ──< order_items >── food
  │         │  │          │            │
  │         │  │          └── r_id ────┤
  │         │  └───────────────────────┤
  │         │                          │
  │         └──< reviews               │
  │                 │                  │
  └─────────────────┘                  │
                                       │
restaurant ──< menu >──────────────────┘
```

The same restaurant key appears under **three different names**:

| Table | Column |
| ----- | ------ |
| `restaurant` | `id` |
| `orders`, `order_items`, `menu` | `r_id` |
| `reviews` | `restaurant_id` |

STAGING renames all of them to `restaurant_id`. Until then, every join needs
the mapping in front of you.

---

## Known data-quality issues

### `sales_amount` is not a derivable column

`subtotal - discount + delivery_fee + gst` does not reliably reproduce
`sales_amount`. Row 2 of the file: subtotal 648, discount 216, delivery_fee 20,
gst 32 → 484, which matches. Others do not, because of rounding applied at
different points in the generator.

**Use `sales_amount` as the revenue measure.** Do not recompute it from parts
and do not assert equality — the assertion will fail on real rows and teach
everyone to ignore the test.

### City is not a city

`orders.restaurant_city` and `restaurant.city` mix three different granularities
in one column:

| Value | Actually a |
| ----- | ---------- |
| `Bikaner` | city |
| `Indiranagar,Bangalore` | locality within a city |
| `Noida-1` | numbered zone |

`COUNT(DISTINCT city)` returns 812–821 and means nothing. Split on the comma
and take the right-hand side as the city, the left as the locality; bare values
are the city with a null locality. Strip the `-N` zone suffix separately.

### Cancelled orders still carry line items and revenue

`order_status = 'Cancelled'` rows have a populated `sales_amount` and their
`order_items` rows exist. Nothing in the source zeroes them out.

**Every revenue measure must filter `order_status = 'Delivered'`.** Roughly
8.5% of orders are cancelled or refunded — a mart that omits the filter
overstates revenue by about that much, and looks entirely plausible while doing
it. This is the single easiest way to get a wrong number out of this dataset.

### `reviews.order_id` is not unique

198,011 distinct values across 200,000 sampled rows. Joining `orders` to
`reviews` on `order_id` fans the fact table out. Aggregate reviews to one row
per order first, or accept the duplication deliberately and document it.

### Restaurant keys in facts may not exist in the dimension

`orders.r_id` spans 92,982 distinct values in the first 200k rows against
148,541 restaurants, but referential integrity is not guaranteed across the
full 10 M. Use a `relationships` test to find out how many orphans there are,
and decide between an inner join (drops revenue) and a left join with an
unknown-member row (keeps it). Do not discover this by watching a total change.

### `menu.price` ≠ `order_items.price`

Menu price is the listed price; `order_items.price` is what was charged. They
diverge. For revenue, use the order line. For assortment and pricing analysis,
use the menu.
