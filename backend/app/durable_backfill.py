"""Per-batch durable embedding writes; unknown dispatches are never retried."""
from __future__ import annotations
import time
from sqlalchemy import or_, select
from .embeddings import write_embedding
from .models import Article, EvidenceChunk, Snapshot, Workspace
from .provider_calls import CallBlocked, CallLedger, fingerprint
from .textutil import digest


def pending_chunks(provider, model, dimensions, mode):
    return (select(EvidenceChunk).join(Snapshot, EvidenceChunk.snapshot_id == Snapshot.id)
        .join(Article, Snapshot.article_id == Article.id).where(Article.data_mode == mode,
            or_(EvidenceChunk.embedding_json.is_(None), EvidenceChunk.embedding_provider.is_(None),
                EvidenceChunk.embedding_model.is_(None), EvidenceChunk.embedding_dim.is_(None),
                EvidenceChunk.embedding_provider != provider, EvidenceChunk.embedding_model != model,
                EvidenceChunk.embedding_dim != dimensions)).order_by(EvidenceChunk.id))


def durable_backfill(factory, gateway, *, data_mode: str, batch_size: int = 16,
                     max_chunks: int = 100, allow_paid_api: bool = False,
                     confirm_provider: str | None = None, retry_authorization=None) -> dict:
    p = gateway.provider
    provider, model, dim = p.provider, p.model, p.dimensions
    if data_mode not in {"live", "replay"} or not 1 <= batch_size <= 128 or not 1 <= max_chunks <= 5000:
        raise ValueError("Invalid backfill scope/budget")
    if not provider or not model or not 1 <= dim <= 16000:
        raise ValueError("Invalid embedding identity")
    if provider != "fixture" and (data_mode != "live" or not allow_paid_api or confirm_provider != provider):
        raise ValueError("Paid embedding requires explicit live-only scope and authorization")
    if retry_authorization is not None and max_chunks > batch_size:
        raise ValueError("Authorized retry must be limited to the original single batch")
    stmt = pending_chunks(provider, model, dim, data_mode)
    with factory() as db:
        if provider != "fixture" and db.bind.dialect.name != "postgresql":
            raise ValueError("Paid backfill requires PostgreSQL")
        workspace = db.get(Workspace, 1)
        if workspace is None or workspace.data_mode != data_mode:
            raise ValueError("Workspace data mode mismatch")
        rows = list(db.scalars(stmt.limit(max_chunks)))
    ledger = CallLedger(factory)
    result = {"status": "applied", "provider": provider, "model": model, "dimensions": dim,
              "selected": len(rows), "embedded": 0, "batches_completed": 0,
              "calls": [], "cost_usd": None, "complete": False}
    endpoint = fingerprint(getattr(p, "base_url", "fixture").rstrip("/"))
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset:offset + batch_size]
        # Include stable snapshot/offset/hash identity, not batch size or a run ID.
        # Rechunking the same evidence must not evade an unknown dispatch guard.
        manifests = [[r.snapshot_id, r.start_offset, r.end_offset, r.text_hash] for r in batch]
        resources = [fingerprint([provider, model, dim, endpoint, item]) for item in manifests]
        call = None
        started = time.monotonic()
        try:
            if any(digest(r.text) != r.text_hash or not r.text.strip() for r in batch):
                raise ValueError("Immutable chunk integrity check failed")
            call = ledger.reserve(operation_key="backfill:" + fingerprint(resources),
                request_hash=fingerprint([manifests, dim, endpoint]), kind="embedding_backfill",
                provider=provider, model=model, data_mode=data_mode, input_count=len(batch), resource_keys=resources, retry_authorization=retry_authorization)
            call.require_fresh()
            response = gateway.embed_documents([r.text for r in batch], receipt_observer=call.observe)
            if (response.provider, response.model, response.dimensions) != (provider, model, dim):
                raise ValueError("Provider identity changed during backfill")
            with factory.begin() as db:
                current = list(db.scalars(select(EvidenceChunk).where(
                    EvidenceChunk.id.in_([r.id for r in batch])).with_for_update()))
                by_id = {r.id: r for r in current}
                for old, vector in zip(batch, response.vectors, strict=True):
                    row = by_id.get(old.id)
                    if row is None or row.text_hash != old.text_hash or row.text != old.text:
                        raise ValueError("Chunk changed while provider request was in flight")
                    write_embedding(db, row, vector, response.model,
                                    provider=response.provider, revision=response.revision)
                call.succeed(db, elapsed_ms=round((time.monotonic()-started)*1000), summary={"embedded": len(batch)})
            result["embedded"] += len(batch)
            result["batches_completed"] += 1
            result["calls"].append(call.read())
        except CallBlocked as exc:
            result.update(status="needs_attention", blocked_call_id=exc.call_id, blocked_state=exc.state)
            break
        except Exception:
            if call is not None:
                call.unknown(elapsed_ms=round((time.monotonic()-started)*1000))
                result["calls"].append(call.read())
            result.update(status="needs_attention", error_code="batch_not_committed")
            break
    with factory() as db:
        result["complete"] = result["status"] == "applied" and db.scalar(stmt.limit(1)) is None
    counts = [c["input_tokens"] for c in result["calls"]]
    result["input_tokens"] = sum(counts) if all(n is not None for n in counts) else None
    result["request_ids"] = [c["request_id"] for c in result["calls"] if c["request_id"]]
    return result
