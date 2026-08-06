# AWS — the lake and the trust handshake

Two things live in AWS: an S3 bucket holding the raw CSVs, and an IAM role
Snowflake assumes to read them. No access keys are involved anywhere.

For the end-to-end sequence including the Snowflake side, see
[docs/RUNBOOK.md](../docs/RUNBOOK.md). This file covers the AWS half and
explains why the policies are shaped the way they are.

---

## Bucket layout

One bucket, one prefix per table. The prefix-per-table layout matters: the
external stage points at `raw/`, and each `COPY INTO` targets one folder
beneath it, so Snowflake expands `@ZOMATO_RAW_STAGE/orders/` into a file list
without scanning the other six tables.

```
s3://<BUCKET>/
└── raw/
    ├── restaurants/    restaurant.csv       46 MB
    ├── users/          users.csv            11 MB
    ├── food/           food.csv             17 MB
    ├── menu/           menu.csv             66 MB
    ├── orders/         orders.csv         1.33 GB
    ├── order_items/    order_items.csv     955 MB
    └── reviews/        reviews.csv          25 MB
```

Settings that are not defaults and are worth setting:

| Setting | Value | Why |
| ------- | ----- | --- |
| Block all public access | **on** | Nothing here is served publicly. Snowflake reads via an assumed role, not a public URL. |
| Default encryption | SSE-S3 | Free. SSE-KMS also works but adds a `kms:Decrypt` grant to the role policy and a per-request charge on 23 M-row loads. |
| Versioning | optional | The role has `s3:GetObjectVersion`, so it works either way. Useful if you re-upload a corrected file. |
| Region | same as Snowflake | Cross-region reads are slower and incur transfer cost. |

Upload the files with the CLI rather than the console — the console times out
on the 1.33 GB file:

```bash
aws s3 cp data/orders.csv       s3://<BUCKET>/raw/orders/
aws s3 cp data/order_items.csv  s3://<BUCKET>/raw/order_items/
aws s3 cp data/restaurant.csv   s3://<BUCKET>/raw/restaurants/
aws s3 cp data/users.csv        s3://<BUCKET>/raw/users/
aws s3 cp data/food.csv         s3://<BUCKET>/raw/food/
aws s3 cp data/menu.csv         s3://<BUCKET>/raw/menu/
aws s3 cp data/reviews.csv      s3://<BUCKET>/raw/reviews/
```

Note `restaurants/` is plural in S3 while the file is `restaurant.csv`. The
RAW table is `RAW.restaurants`, so the prefix matches the table, not the file.

---

## The role

One role, `snowflake-zomato-role`, with two policies attached — and they do
different jobs. Conflating them is the most common way this setup goes wrong.

| Policy | Answers | File |
| ------ | ------- | ---- |
| Permission policy | *what may this role do?* | [`iam/s3-read-policy.json`](iam/s3-read-policy.json) |
| Trust policy | *who may become this role?* | [`iam/snowflake-role-trust-policy-final.json`](iam/snowflake-role-trust-policy-final.json) |

### Permission policy — read-only, two statements

[`iam/s3-read-policy.json`](iam/s3-read-policy.json) grants four actions and
nothing else:

```
s3:GetObject           read an object
s3:GetObjectVersion    read a specific version
s3:ListBucket          enumerate keys, so COPY can expand a folder prefix
s3:GetBucketLocation   resolve the region before the first read
```

No `PutObject`, no `DeleteObject`. Snowflake pulls from the lake and never
writes to it, so a compromised warehouse — or a COPY typed in the wrong
direction — cannot damage the source data. The lake holds the only copy of
`orders.csv` and `order_items.csv` that is not in git.

**The two-statement split is required, not stylistic.** Object actions take
`arn:aws:s3:::<BUCKET>/*`; bucket actions take `arn:aws:s3:::<BUCKET>`, the
bare ARN with no wildcard. Collapse them and half the permissions break:

| If you use only… | What breaks |
| ---------------- | ----------- |
| `<BUCKET>/*` | `ListBucket` fails — `COPY` cannot expand `@stage/orders/` into a file list |
| `<BUCKET>` | `GetObject` fails — nothing loads at all |

Either way it surfaces as an opaque S3 access-denied *inside a COPY
statement*, which is a bad place to debug an IAM problem.

### Trust policy — why there are two files

The setup is circular:

- Snowflake will not tell you which IAM principal it uses until a storage
  integration exists.
- `CREATE STORAGE INTEGRATION` will not accept a role ARN that does not exist.

So the role is created with a placeholder trust policy, and the real one is
written afterwards from values Snowflake returns.

```
  A. Create the role with the INITIAL trust policy  ─┐
                                                     │  bootstrap
  B. CREATE STORAGE INTEGRATION using the role ARN  ─┘
                    │
  C. DESC INTEGRATION ZOMATO_S3_INT
         ├── STORAGE_AWS_IAM_USER_ARN
         └── STORAGE_AWS_EXTERNAL_ID
                    │
  D. Replace with the FINAL trust policy  ← the one that actually protects you
```

[`snowflake-role-trust-policy-initial.json`](iam/snowflake-role-trust-policy-initial.json)
trusts the account's own root principal. That is not access control — the
account already controls its own roles — it is a syntactically valid
placeholder that lets step B proceed.

> **Step D is not optional.** A role left trusting `:root` with the S3 read
> policy attached can be assumed by any principal in the account. On a solo
> account that is a small problem; on a shared one it is not.

---

## Why the external ID is the whole point

[`snowflake-role-trust-policy-final.json`](iam/snowflake-role-trust-policy-final.json)
adds a condition that looks like a formality and is not:

```json
"Condition": {
  "StringEquals": { "sts:ExternalId": "<STORAGE_AWS_EXTERNAL_ID>" }
}
```

**Snowflake does not create a dedicated AWS principal per customer.** Accounts
on the same Snowflake deployment share an IAM user, so
`STORAGE_AWS_IAM_USER_ARN` is neither secret nor unique to you.

A trust policy naming only that principal would be satisfied by *any Snowflake
customer on the same deployment*. They create a storage integration pointing
at your role ARN; Snowflake, acting in good faith on their behalf, assumes it
and reads your bucket. Neither party does anything wrong — Snowflake is a
deputy confused about whose request it is serving. That is the
[confused-deputy problem](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html),
and the external ID is AWS's answer to it.

The external ID is generated per integration and known only to your account
and Snowflake. With the condition in place, the assume-role call must present
it, so only *your* integration succeeds.

It is not a bearer token to be guarded like a private key, but it is
account-specific and does not belong in a public repo — which is why both
values in the committed file are angle-bracketed placeholders.

### The failure mode that costs an afternoon

`CREATE OR REPLACE STORAGE INTEGRATION` **mints a new external ID.**

Re-running [`snowflake/02_storage_integration.sql`](../snowflake/02_storage_integration.sql)
after step D silently invalidates the trust policy. Every subsequent `COPY`
fails with an S3 access-denied that says nothing about trust, external IDs, or
the fact that you just rotated one.

If that happens:

```sql
DESC INTEGRATION ZOMATO_S3_INT;
```

and paste the new `STORAGE_AWS_EXTERNAL_ID` into the trust policy.

---

## Verifying it works

From Snowflake, before loading anything:

```sql
LIST @ZOMATO.RAW.ZOMATO_RAW_STAGE;
```

Seven folders should come back. What you get instead tells you which half is
wrong:

| Symptom | Cause |
| ------- | ----- |
| `Access Denied` | Trust policy — wrong principal, or the external ID does not match |
| Empty list, no error | Trust is fine; the prefix is wrong or the files are not uploaded |
| Folders listed but `COPY` denies | `ListBucket` granted, `GetObject` not — the two-statement split |
| Worked yesterday, denied today | The integration was re-created and the external ID rotated |

Then confirm from the AWS side that the role is only assumable as intended:

```bash
aws iam get-role --role-name snowflake-zomato-role \
  --query 'Role.AssumeRolePolicyDocument'
```

If the principal still reads `:root`, step D has not been done.

---

## What is deliberately absent

- **No access keys.** Snowflake stores no AWS secret. It assumes the role via
  STS and receives short-lived credentials, so there is no long-lived key to
  leak, rotate or find in a screenshot.
- **No write permissions.** Covered above.
- **No real identifiers.** `<BUCKET>`, `<ACCOUNT_ID>`,
  `<STORAGE_AWS_IAM_USER_ARN>` and `<STORAGE_AWS_EXTERNAL_ID>` stay as
  placeholders. Bucket names are globally unique and enumerable; a real one in
  a public repo is free reconnaissance.
