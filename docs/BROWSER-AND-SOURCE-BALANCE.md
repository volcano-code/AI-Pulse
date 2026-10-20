# Source-balanced editions and native browser acceptance

## Scope

- Source balance defaults on for new editions. Operators can disable it in Preferences.
- Hard eligibility checks (data mode, source enabled, publication window, capture cutoff, blocked keywords) run first. No old or future publication is revived to fill a source quota.
- Candidates are bounded at 1,000 records. Within that candidate set, a soft source cap is ceil(max_items / available_sources); a second pass fills slots from eligible sources when other sources are sparse.
- Exact-content and existing event memberships are deduplicated. This relies on deterministic event grouping, not semantic fact verification. Existing historical groupings are not rewritten here.
- Saved edition summaries count frozen BriefItem source labels, not today's editable source names. Historical editions are not regenerated.
- Research UI shows the persisted requested/effective retrieval mode and degradation reason. It does not label lexical search as vector RAG.

## Executed before submission

- Main baseline: 337 passed, 3 PostgreSQL-only skips (Python 3.11 VM).
- Modified backend: 354 passed, 3 PostgreSQL-only skips.
- TypeScript typecheck and Next production build: passed. Floot's inherited non-standard NODE_ENV initially broke prerendering; setting NODE_ENV=production resolved the environment problem.
- Fresh live-source acceptance: 75 articles/snapshots; 8 exact quote slices validated; repeated brief generation returned the same edition.
- Same live corpus and cutoff, only diversify_sources toggled: disabled = arXiv 7/Hugging Face 1; enabled = arXiv 4/Hugging Face 3/LangGraph 1. This is one real run, not a general quality benchmark.
- VM Chromium execution is blocked by missing libglib-2.0.so.0. It is NOT counted as a browser pass. The new GitHub browser-e2e job installs system dependencies and is the required browser gate.

## Native E2E contract

Chromium -> Next standalone production server -> real FastAPI -> dedicated database + a separate durable SQL worker. No response mocking, page.route interception, bridge browser, paid provider or SMTP. PR CI uses a fresh PostgreSQL/pgvector service; local default uses disposable SQLite.

The supervisor migrates the empty database before starting API/worker, strips inherited paid credentials, disables fetch hosts, runs only replay/extractive/file mode, refuses existing databases and tears down its own processes.

Journeys: exact Unicode quote dialog; SSE evidence search; research + receipt + reload; unknown-question abstention; preference change + worker execution + unchanged historical edition; local delivery limits/worker heartbeat; mobile viewport.

Local usage (install backend dependencies first):

    cd apps/web
    npm ci
    npx playwright install --with-deps chromium
    NODE_ENV=production npm run build
    PLAYWRIGHT_PRODUCTION=1 npm run test:e2e

Use PYTHON to select your virtualenv interpreter. PostgreSQL opt-in is AI_PULSE_E2E_DATABASE_URL; only postgresql+psycopg on loopback, database name ai_pulse_e2e, with no URL query options is accepted. A non-empty database is rejected, never dropped.

GitHub's browser-e2e job uses zero retries; it retains replay screenshots, HTML/JSON reports and failure traces. Green unit/build checks do not replace this job. See the PR acceptance comment for the final committed-SHA CI result.

## Still out of scope

Paid LLM/embedding canaries, semantic citation support, production deployment, SMTP delivery and a 14-day soak are not validated by this change. A fresh PostgreSQL CI service is not a production Docker deployment.

## Dependency warning found during acceptance

The first four-job CI run passed, including 7 native Chromium tests, but npm reported high-severity advisories for Next 16.3.6 and source-map-js 1.2.1. Updated only those package families to Next 16.3.8 and source-map-js 1.2.2, regenerated npm lockfile, and added a high/critical audit gate. The subsequent registry audit returned zero advisories; this is not a security proof. Final CI results belong in the PR acceptance comment.
