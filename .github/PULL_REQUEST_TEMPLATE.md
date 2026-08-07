<!--
  Title this PR as a conventional commit, e.g.
      feat(snowflake): add external stage and CSV file format
  It becomes the merge-commit subject and feeds the changelog.
-->

## What

<!-- One paragraph. What does this change do, in plain language? -->

## Why

<!--
  The part that matters. What problem forced this change? What did you try
  first? What did you rule out and why? Six months from now this section is
  the only place that answer exists.
-->

## Layer

<!-- Tick every layer this PR touches. -->

- [ ] Source / datasets (`data/`)
- [ ] Lake — S3, IAM (`aws/`)
- [ ] Bronze / Raw — Snowflake provisioning, stages, COPY (`snowflake/`)
- [ ] Silver — dbt staging models
- [ ] Gold — dbt marts, snapshots
- [ ] AI lane — LLM enrichment, RAG, text-to-SQL
- [ ] Orchestration — Airflow DAGs
- [ ] Serve — Streamlit, Snowsight
- [ ] Docs / tooling / CI

## How this was verified

<!--
  Be specific. "Tested locally" is not a verification. Paste the command and
  the outcome — row counts before and after, the dbt test summary, the
  warehouse credits consumed, the query plan that changed.
-->

```text

```

## Cost and performance impact

<!--
  This project runs on Snowflake trial credits. Any change to warehouse size,
  auto-suspend, clustering, materialisation strategy or a Gemini call volume
  needs a number here. "None" is a valid answer — say it explicitly.
-->

## Checklist

- [ ] Branch name follows the convention in [CONTRIBUTING.md](../CONTRIBUTING.md)
- [ ] Commits are conventional, and the bodies explain *why*
- [ ] No credentials, account IDs, bucket names or external IDs in the diff
- [ ] SQL is re-runnable — running it twice is safe
- [ ] Stateful objects use `IF NOT EXISTS`; stateless use `CREATE OR REPLACE`
- [ ] Any new column is documented in [docs/DATA_DICTIONARY.md](../docs/DATA_DICTIONARY.md)
- [ ] `python scripts/make_samples.py --check` passes if `data/` changed
- [ ] Architecture diagram updated if the topology changed
- [ ] CI is green

## Related

<!-- Closes #, Refs #, or "none". -->
