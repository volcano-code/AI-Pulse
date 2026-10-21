# Provider call audit and bounded backfill recovery

## Coverage and non-goals

The operational embedding CLI and one-shot LLM canary now use a database-backed
call ledger. Ordinary brief generation, research turns and online query embeddings
still retain their existing safeguards, but are NOT yet migrated to this ledger.
This change does not claim provider-side exactly-once billing, real model quality,
a monetary spending limit, or live paid-provider acceptance. Tests use fixture
vectors or HTTP mocks; no API key is needed for CI.

## State and durability

1. Reserve a unique operation hash and, for backfill, unique evidence resource keys
   in a short transaction BEFORE provider I/O. Reservation failure means no dispatch.
2. Persist bounded request ID/HTTP status on response headers and valid token counts
   after response decoding, BEFORE local schema/evidence/vector validation.
3. Commit each successful embedding batch and its `succeeded` call status in the
   SAME transaction. Later batch failure cannot roll back completed batches.
4. On a caught timeout/validation/write failure, retain `unknown`. Hard process death
   leaves `in_flight`; neither status has a retry timer or expiry. Both are unsafe to
   re-dispatch. A confirmed success is never overwritten by a lost commit acknowledgement.

Unknown is conservative: it does not assert a provider charged or did not charge.
`cost_usd` remains null. Missing/negative/bool/oversized token counts remain null.
No source text, prompt, generated output, API key or raw upstream exception is stored
in the ledger. Operator IDs and endpoint identities are hashed. Receipts are still
operational metadata: keep the database private and restrict access.

A local database cannot atomically transact with a remote model service. A crash
between remote completion and local commit can require manual reconciliation.
This implementation chooses safety over automatic recovery; it does not unlock
unknown calls or invent a provider idempotency guarantee.

## Commands

First migrate the intended database; never point migration tests at production:

```sh
cd backend
alembic upgrade head
python -m scripts.embedding_backfill --max-chunks 20 --batch-size 4
python -m scripts.provider_audit --limit 50
```

Dry run does not create call records, send content, or write embeddings. Applying
still requires provider confirmation and (for a real provider) live data,
PostgreSQL/pgvector and explicit paid API authorization:

```sh
python -m scripts.embedding_backfill --apply --confirm-provider openai \
  --allow-paid-api --max-chunks 20 --batch-size 4
```

Use a server-side environment/secret manager for credentials, not argv or logs.
Re-running skips already persisted vectors. A pending item whose resource is already
reserved returns `needs_attention` and a call ID, without another provider request.
Changing batch size or restarting the process does not remove that guard. This is
bounded incremental scanning, not a frozen corpus-wide migration manifest: new
eligible chunks can be considered on a later invocation. `complete` describes the
pending set observed at the end of that invocation, not future ingestion.

The old session-scoped `backfill_embeddings` is now fixture-only because it cannot
commit a durable intent outside its caller's transaction. Paid callers must use the
new session-factory-based entrypoint; do not retain a surrounding write transaction.

For an explicitly authorized single LLM transport/evidence canary:

```sh
python -m scripts.llm_canary --confirm-live-call --operation-id first-provider-check
```

The input is labelled public synthetic canary text, NOT live news. A repeated
operation ID returns the existing successful receipt or `needs_attention`; it sends
zero additional calls. Reusing the ID with different input/model/configuration is
rejected. A deliberately new ID requests a new operation, so do not use new IDs to
work around an unknown outcome. No draft is published or emailed.

Audit inspection is read-only. No automatic unknown-to-retry transition is provided.
Inspect the supplier's request/account logs before any separately authorized manual
remediation. Provider response tokens are usage evidence, not proof of billing.

## Migration and acceptance

Migration 0007 adds `provider_calls` and `provider_call_resources`. Existing evidence
and historical editions are unchanged. Downgrade refuses to delete a non-empty call
ledger, so an operator cannot silently erase unknown-call protection. Back up and
reconcile retained history before any separately reviewed destructive migration.

Offline acceptance covers concurrency, conflicting operation IDs, overlapping
batches, timeout, process death, invalid evidence/vector output, current-batch
rollback, completed-batch preservation, restart, credential/error redaction and
no-side-effect dry runs. PostgreSQL CI additionally exercises real uniqueness and
pgvector writes. Final counts and CI URLs belong in the PR acceptance comment;
this document does not predeclare test success.
