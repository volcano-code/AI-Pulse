# Runtime model audit and validated brief drafts

## What is covered

The existing metadata-only provider ledger now covers ordinary live brief drafts
and every live research tool-call turn, in addition to operational embedding
backfill and the one-shot LLM Canary. Online query embeddings are **not** migrated
by this change. No paid API or SMTP call is made by the tests.

### Brief generation

A committed dispatch intent precedes each model request. Identity includes the
snapshot, actual endpoint hash/model/output settings, prompt and draft schema.
The selected source must belong to the configured workspace/data mode. Missing
migration 0008 fails before dispatch. Worker fencing is checked at reservation
and commit; no database transaction is held during network I/O.

A valid draft and its success receipt commit together. The `brief_model_drafts`
table is a business-result cache, **not** audit metadata and **not** a published
brief. It contains validated title, summary and exact quote only, tied to the
immutable snapshot and provider call. Never expose this table in public artifacts.

Completed drafts survive a later item failure or final edition write failure.
A new attempt reuses the same validated snapshot/configuration result, reporting
zero new model calls and a nonzero `reused_model_results` count. An unknown or
in-flight call, or a success without its required cached artifact, blocks rather
than dispatching again. Changing preferences, cutoff or job ID does not bypass
the same-snapshot/configuration guard. Changing model semantics is a new request.
Every generated edition remains `needs_review`; quote location is not semantic
fact verification. Approval/export/delivery gates are unchanged.

### Research turns

Each turn uses a deterministic investigation-ID/turn-number operation key. The
request trace links its provider-call ID. Headers/usage are observed separately
from tool/claim validation; invalid tools, bad evidence and uncertain transport
leave a blocking `unknown` receipt. Tokens received for a failed request remain
visible; incomplete totals are `null`, not a misleading partial sum.

A successful turn means local protocol/tool/evidence handling accepted that turn;
it does not mean the whole investigation completed, nor that the final claims
are true. Research results are still saved in their existing final transaction.
This change does not add graph resumption or replay tool decisions after a crash.

The child process emits bounded metadata frames on its internal pipe before
response validation. On nonzero exit or timeout, the parent relays complete
receipt frames from captured stdout before recording failure. The child receives
keys only over stdin. No keys, full prompts, reasoning text or raw upstream errors
enter the ledger. If the parent itself dies before receiving metadata, the
committed intent remains blocking; missing usage is honestly unknown.

On single-API startup, interrupted investigations are stopped. Their deterministic
turn keys locate and mark in-flight receipts unknown, even when a crash prevented
the trace link from being saved. Repeating an idempotency key returns the stopped
record. New explicit investigations are separate requests, not a retry guarantee.

## Migration and operations

Stop the API/worker and back up the database before upgrading existing installs:

    cd backend
    alembic upgrade head
    python -m scripts.provider_audit --limit 50

Migration 0008 adds only `brief_model_drafts`; it does not rewrite historical
editions, snapshots, event memberships or provider-call receipts. Downgrade
refuses to erase nonempty validated drafts. Revision 0007 separately protects
nonempty audit history. Run downgrade tests only on disposable databases.

Do not delete reservations, replace operation IDs or mutate unknown statuses to
force a retry. There is no new unlock/retry endpoint here. Human reconciliation
and an authorized recovery workflow remain separate work. No exactly-once
provider billing or dollar-denominated spending cap is claimed.

## Validation contract

Run the full backend suite, then the dedicated PostgreSQL CI tests including
`test_runtime_models_postgres.py`. Ordinary local runs skip PostgreSQL tests;
those skips are not passes. Four GitHub gates remain required: backend,
PostgreSQL/pgvector+migration round trip, frontend audit/build and native browser
E2E. The PR acceptance comment records final committed-SHA results.

New tests exercise intent-before-I/O, one dispatch under concurrency, immutable
scope, successful draft reuse, final-edition commit failure, atomic draft/receipt
rollback, unknown blocking, invalid quote usage retention, per-turn research
receipts, repeated request IDs, model-configuration conflicts, startup recovery,
real child-process timeout with partial metadata, and migration refusal.

All scripted clients/HTTP MockTransport paths are labelled `fixture` in receipts.
These are fault-injection contracts, not real model quality or billing evidence.
