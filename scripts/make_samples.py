#!/usr/bin/env python3
"""Build the committed sample extracts for the two oversized fact tables.

`data/orders.csv` (1.33 GB) and `data/order_items.csv` (955 MB) are excluded
from git — both are far past GitHub's 100 MB per-file hard limit, and the raw
lake belongs in S3 anyway. What *is* committed is a small extract of each, so
that anyone cloning the repo can read the schema, eyeball real values and
smoke-test a dbt model without downloading a gigabyte first.

The sample is deliberately not random. It is the first N orders plus *every*
line item belonging to them, which keeps the pair referentially intact:

    orders_sample.order_id        is a superset of
    order_items_sample.order_id

A random sample would break that join and make the extract useless for testing
the exact thing it exists to test. `order_items.csv` is already sorted by
`order_id`, so a single forward pass is enough — nothing is held in memory.

Usage
-----
    python scripts/make_samples.py                 # default: 5,000 orders
    python scripts/make_samples.py --orders 20000
    python scripts/make_samples.py --check         # verify, write nothing

Re-running is idempotent: the same inputs always produce byte-identical output.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

# Repo root, resolved from this file so the script works from any cwd.
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SAMPLES = DATA / "samples"

DEFAULT_ORDERS = 5_000

# Widen the field-size cap: review comments and address fields are long, and
# csv defaults to 128 KB which some rows in the wider tables will trip.
csv.field_size_limit(10_000_000)


def _fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def sample_orders(limit: int) -> set[str]:
    """Copy the first `limit` order rows out; return the order_ids taken."""
    src = DATA / "orders.csv"
    dst = SAMPLES / "orders_sample.csv"
    if not src.exists():
        _fail(f"{src} not found — download the raw dataset first (see docs/DATA_DICTIONARY.md)")

    kept: set[str] = set()
    with (
        src.open(newline="", encoding="utf-8") as fh_in,
        dst.open("w", newline="", encoding="utf-8") as fh_out,
    ):
        reader = csv.reader(fh_in)
        writer = csv.writer(fh_out, lineterminator="\n")
        header = next(reader)
        writer.writerow(header)

        order_id_at = header.index("order_id")
        for row in reader:
            if len(kept) >= limit:
                break
            kept.add(row[order_id_at])
            writer.writerow(row)

    print(f"  orders_sample.csv       {len(kept):>7,} rows")
    return kept


def sample_order_items(order_ids: set[str]) -> int:
    """Copy every line item whose order_id is in `order_ids`.

    Relies on `order_items.csv` being sorted by `order_id`: once we have seen
    the sampled ids and then hit one that is not in the set, everything after
    it is out of range too, so we can stop instead of streaming 23 M rows.
    """
    src = DATA / "order_items.csv"
    dst = SAMPLES / "order_items_sample.csv"
    if not src.exists():
        _fail(f"{src} not found — download the raw dataset first (see docs/DATA_DICTIONARY.md)")

    written = 0
    with (
        src.open(newline="", encoding="utf-8") as fh_in,
        dst.open("w", newline="", encoding="utf-8") as fh_out,
    ):
        reader = csv.reader(fh_in)
        writer = csv.writer(fh_out, lineterminator="\n")
        header = next(reader)
        writer.writerow(header)

        order_id_at = header.index("order_id")
        seen_any = False
        for row in reader:
            if row[order_id_at] in order_ids:
                writer.writerow(row)
                written += 1
                seen_any = True
            elif seen_any:
                break  # sorted input — past the sampled range

    print(f"  order_items_sample.csv  {written:>7,} rows")
    return written


def check() -> None:
    """Assert the committed samples still join cleanly."""
    orders = SAMPLES / "orders_sample.csv"
    items = SAMPLES / "order_items_sample.csv"
    for path in (orders, items):
        if not path.exists():
            _fail(f"{path.name} is missing — run `python scripts/make_samples.py`")

    with orders.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        order_ids = {row["order_id"] for row in reader}

    with items.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        item_order_ids = {row["order_id"] for row in reader}

    orphans = item_order_ids - order_ids
    if orphans:
        _fail(
            f"{len(orphans)} order_item rows reference orders missing from the sample "
            f"(e.g. {sorted(orphans)[:5]})"
        )

    print(
        f"ok: {len(order_ids):,} orders, {len(item_order_ids):,} of them with line items, 0 orphans"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--orders",
        type=int,
        default=DEFAULT_ORDERS,
        help=f"number of orders to sample (default: {DEFAULT_ORDERS:,})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the existing samples instead of rebuilding them",
    )
    args = parser.parse_args()

    if args.check:
        check()
        return

    SAMPLES.mkdir(parents=True, exist_ok=True)
    print(f"building samples from the first {args.orders:,} orders")
    order_ids = sample_orders(args.orders)
    sample_order_items(order_ids)
    check()


if __name__ == "__main__":
    main()
