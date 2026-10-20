# Live evidence pipeline acceptance

Run from `backend`:

```sh
python -m scripts.live_pipeline --output-dir /tmp/pulse-live-unique --min-documents 20
```

The output directory must **not** already exist. The command migrates a fresh SQLite database, bootstraps the same source URLs used by the application, fetches each source once with `ProcessFetcher`, calls the production ingestion and event code, generates an extractive brief, and reopens saved rows for independent checks. It also verifies repeated generation at the same cutoff is idempotent.

No production database is accessed. Credentials and `.env` are not inherited into the run configuration. No LLM, embedding, SMTP, scheduler or publisher is called. HTTP/DNS time is bounded by the production fetch subprocess. Source failures stay visible and yield a nonzero exit even when other sources produce a brief.

Artifacts: `report.json` and `brief.md`. The disposable `pipeline.db` contains original saved text/quote offsets and is deliberately excluded from GitHub Actions artifacts. The Markdown artifact includes titles, source URLs, dates and evidence positions, not full source bodies or model-generated analysis.

## What is checked

- Same endpoints as `bootstrap.DEFAULT_SOURCES`, including arXiv Atom API (the earlier canary used a different RSS endpoint).
- Source errors and zero parsed items are not reported as successful collection.
- Original publication dates remain unchanged; old/future data cannot be made current by changing capture time.
- Article/snapshot ownership, content hashes, event-version membership, exact quote slices, date window and brief idempotence.
- Package release titles such as `langgraph==1.2.16` cannot merge with another explicit package/version identity. The guard only changes new matching decisions; it does **not** rewrite old memberships or historical versions. Existing incorrect groups require a separately reviewed repair.

## Interpretation

`passed` means this one disposable run passed the listed mechanical checks. It is not a semantic fact check, a real LLM/embedding acceptance, native browser E2E, production PostgreSQL deployment, Chinese model summary, or long-term uptime proof.

The CLI uses live data. Unit-test fixtures use a separate replay workspace and report `execution=offline_contract`; those results must never be presented as real-source quality metrics.

The Live Evidence Pipeline workflow runs manually, plus on the implementation branch for this acceptance. Standard CI unit tests remain deterministic and offline. External-source outages may fail the live job without invalidating the offline contracts.
