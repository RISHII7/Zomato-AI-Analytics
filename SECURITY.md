# Security Policy

## Scope

This is a portfolio and learning project. It provisions real cloud resources —
an S3 bucket, an AWS IAM role, a Snowflake account and a Gemini API key — so
the security posture is not theoretical even though the data is synthetic.

## Reporting a vulnerability

Open a [security advisory](https://github.com/RISHII7/Zomato-AI-Analytics/security/advisories/new)
or email **rishikesh.palande07@gmail.com**. Please do not open a public issue
for anything exploitable.

Expect an acknowledgement within a few days. There is no bug bounty.

## What is committed here, deliberately

Every credential-shaped value in this repository is a placeholder:

| Placeholder                   | Real value lives in                        |
| ----------------------------- | ------------------------------------------ |
| `<ACCOUNT_ID>`                | your AWS account                           |
| `<BUCKET>`                    | your S3 bucket name                        |
| `<ROLE_ARN>`                  | AWS IAM                                    |
| `<STORAGE_AWS_IAM_USER_ARN>`  | `DESC INTEGRATION` output, per Snowflake account |
| `<STORAGE_AWS_EXTERNAL_ID>`   | `DESC INTEGRATION` output, per integration |

The `STORAGE_AWS_EXTERNAL_ID` in particular must never be committed. It is the
single value that prevents the [confused-deputy](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html)
attack against the cross-account trust: with your role ARN alone, another
Snowflake tenant could ask Snowflake's shared IAM principal to assume your
role. The external ID is what pins the trust to *your* integration.

## Design choices that are security choices

- **Storage integration, not access keys.** Snowflake never stores an AWS
  secret. It assumes a role via STS and receives short-lived credentials. There
  is no long-lived key to leak or rotate.
- **Read-only on the bucket.** `aws/iam/s3-read-policy.json` grants
  `GetObject`, `GetObjectVersion`, `ListBucket` and `GetBucketLocation` and
  nothing else. A compromised warehouse cannot delete the lake.
- **Bucket-scoped, prefix-scoped.** `STORAGE_ALLOWED_LOCATIONS` bounds the
  integration to one bucket; the stage narrows it further to `raw/`.
- **Least-privilege role for tooling.** dbt and Airflow authenticate as
  `DBT_ROLE`, never `ACCOUNTADMIN`. Only the one-time provisioning scripts in
  `snowflake/` require `ACCOUNTADMIN`, and they say so at the top.
- **Text-to-SQL is `SELECT`-only.** The natural-language query path runs
  generated SQL as `DBT_ROLE` behind a statement guard. An LLM writing SQL
  against your warehouse is an injection sink; treat it as untrusted input,
  because it is.

## If you leak a credential

1. **Rotate first.** Delete the key, drop the integration, revoke the token.
2. Then rewrite history (`git filter-repo`) and force-push.
3. Then check CloudTrail / Snowflake `LOGIN_HISTORY` and `QUERY_HISTORY` for
   use you did not initiate.

Rotation before history rewriting, always. A secret that reached GitHub — even
for a minute, even in a private repo — should be considered read.

## Data

The datasets contain no real people. Names, e-mail addresses, passwords and
order histories are machine-generated; restaurant listings are derived from
public sources. The `password` column in `data/users.csv` holds fake plaintext
strings that authenticate nothing — it exists so the pipeline has a realistic
column to mask, and it is masked in the staging layer rather than loaded
onward.
