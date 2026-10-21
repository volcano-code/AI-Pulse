# Query embedding audit and operator-controlled recovery

## Behavior

The research hybrid retrieval path now reserves a durable call before query-embedding I/O. Successful vectors and their success receipts commit together. The vector cache is keyed by the exact query, mode, purpose, model, dimensions and endpoint identity. It stores no raw query. Hashes are not encryption; cached vectors and the database remain protected application data.

Only vectors are reused, never evidence lists or permissions. Every retrieval rebuilds its authorized snapshot scope, including historical-brief and disabled-source filtering. Empty scopes, missing scoped corpus vectors, absent PostgreSQL, missing audit migrations and provider failures produce explicit lexical degradation, never an untracked provider fallback. Queries are limited to 400 characters. `EMBEDDING_QUERY_ALLOW_PAID=false` is the default; enabling it is separate from configuring a corpus provider or authorizing backfill. This is a call gate, not a monetary ceiling or provider usage guarantee.

Query receipts include cached/new status and the original call receipt; cached invocations report 0 new embedding calls/tokens. Original provider token observations remain in the attached receipt. The audit CLI and public evidence results do not expose vectors.

## Local operator reconciliation (no provider I/O)

Stop the API and worker before modifying unresolved attempts. Inspect the particular call:

```bash
cd backend
python -m scripts.provider_reconcile --call-id CALL_UUID
```

Keep the returned `expected_fingerprint`; it detects changes since inspection. Reconciliation is an operator assertion, NOT verification of the vendor invoice. Store the actual provider support/billing evidence privately; pass only its SHA-256 digest. Never put credentials or billing documents in CLI arguments, logs or public artifacts.

Preview a decision by supplying `--decision keep_blocked|authorize_retry --billing charged|not_charged|unknown --expected-fingerprint DIGEST --evidence-sha256 DIGEST --actor OPERATOR_ALIAS`. Without `--apply`, no rows change.

To record it add `--apply --confirm-call-id CALL_UUID --acknowledge-workers-stopped`. `authorize_retry` also requires `--acknowledge-possible-charge`, including when the operator believes the previous attempt was not charged. The original attempt stays unknown. An interrupted in-flight attempt is frozen as unknown, fencing its late local writes. A decision is immutable through this tool; identical submissions are idempotent, conflicting ones are refused. `keep_blocked` is final for that attempt under the current tool. There is no deletion/reset/unlock HTTP endpoint.

## Explicit one-attempt recovery

A recorded retry decision does NOT itself send anything and is never consumed by ordinary web/worker requests. Recovery additionally requires the decision ID and original request identity. One authorization can create only one replacement attempt; consumption, the replacement intent, and resource ownership transfer are one transaction. Old observations stay intact. Concurrent or repeated recovery commands cannot create a second replacement. Another unknown result requires another separately reviewed decision on that replacement.

For a query or a single brief draft, preview:

```bash
python -m scripts.provider_recover query --call-id CALL_UUID --authorization-id DECISION_UUID
python -m scripts.provider_recover brief --call-id CALL_UUID --authorization-id DECISION_UUID --snapshot-id SNAPSHOT_UUID
```

Execution additionally requires `--apply --confirm-paid-call` and the original configured provider/model/mode. The query command reads the original exact query from stdin (up to 400 characters), never argv. A brief retry only saves a validated needs-review draft; it does not publish a brief or send mail. No generated draft or query vector is printed.

The existing canary supports `--retry-authorization DECISION_UUID` alongside its same `--operation-id` and `--confirm-live-call`. Backfill supports `--retry-authorization DECISION_UUID` alongside its original explicit provider/live/paid confirmations. A backfill retry is restricted to one original, identical batch (`max_chunks <= batch_size`); changed grouping or selection is rejected rather than broadening approval. Ordinary continuation can later process other eligible work. These guards are local dispatch prevention, NOT provider-side exactly-once billing.

Research turns can be reconciled as `keep_blocked` only. No automatic research continuation or per-turn retry is introduced; a successful turn is not a completed investigation. This release does not implement multi-user authorization, a monetary budget, production rollout, vendor reconciliation APIs, or true billed-provider acceptance.

Migration 0009 adds protected query vectors and operator decisions; downgrade refuses to erase nonempty tables. All destructive migration tests run only on disposable databases. Final committed-SHA CI evidence belongs in the PR acceptance record, not inferred from source code.
