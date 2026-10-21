"""Durable local dispatch guard. Not provider-side exactly-once billing.

Commit intent before I/O. Unconfirmed calls never expire into retryable work.
Only hashes, bounded metadata and validated receipts are persisted here.
"""
from __future__ import annotations
import hashlib
import json
import re
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from .models import ProviderCall, ProviderCallResource
from .timeutil import iso, utcnow


class CallBlocked(RuntimeError):
    def __init__(self, call_id: str, state: str):
        self.call_id, self.state = call_id, state
        super().__init__("Existing provider call requires inspection; automatic retry is forbidden")


class CallConflict(ValueError):
    pass


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def safe_count(value):
    return value if type(value) is int and 0 <= value <= 2**31 - 1 else None


def safe_request_id(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value) else None


def receipt(row: ProviderCall) -> dict:
    return {"call_id": row.id, "kind": row.kind, "provider": row.provider,
            "model": row.model, "data_mode": row.data_mode, "status": row.status,
            "request_id": row.request_id, "http_status": row.http_status,
            "input_tokens": row.input_tokens, "output_tokens": row.output_tokens,
            "input_count": row.input_count, "response_received": row.response_received,
            "elapsed_ms": row.elapsed_ms, "error_code": row.error_code,
            "created_at": row.created_at, "finished_at": row.finished_at,
            "automatic_retry_allowed": False, "cost_usd": None,
            "billing": "not_billable" if row.provider == "fixture" else "unverified",
            "summary": row.summary}


class CallLedger:
    def __init__(self, factory):
        self.factory = factory

    def reserve(self, *, operation_key: str, request_hash: str, kind: str,
                provider: str, model: str, data_mode: str, input_count: int = 1,
                resource_keys: list[str] | None = None, commit_guard=None):
        if (not isinstance(operation_key, str) or not 1 <= len(operation_key) <= 500
                or not re.fullmatch(r"[a-f0-9]{64}", request_hash)
                or kind not in {"llm_canary", "embedding_backfill", "brief_draft", "research_turn"}
                or data_mode not in {"live", "replay", "canary"}
                or not provider or len(provider) > 60 or not model or len(model) > 150
                or type(input_count) is not int or not 1 <= input_count <= 128):
            raise ValueError("Invalid provider call identity")
        resources = resource_keys or []
        if len(resources) != len(set(resources)) or any(not re.fullmatch(r"[a-f0-9]{64}", k) for k in resources):
            raise ValueError("Invalid resource reservation")
        operation_hash = fingerprint(operation_key)
        identity = fingerprint([kind, provider, model, data_mode, input_count, request_hash, resources])
        try:
            with self.factory.begin() as db:
                if commit_guard:
                    commit_guard(db)
                row = ProviderCall(operation_hash=operation_hash, request_hash=identity, kind=kind,
                                   provider=provider, model=model, data_mode=data_mode,
                                   input_count=input_count, status="in_flight")
                db.add(row); db.flush()
                for key in resources:
                    db.add(ProviderCallResource(resource_key=key, call_id=row.id))
                call_id = row.id
            return CallHandle(self.factory, call_id, reused=False)
        except IntegrityError:
            # Reopen after rollback: uniqueness, not a process-local lock, chooses
            # the only caller allowed to dispatch. Different batch sizes collide too.
            with self.factory() as db:
                row = db.scalar(select(ProviderCall).where(ProviderCall.operation_hash == operation_hash))
                if row:
                    if row.request_hash != identity:
                        raise CallConflict("Operation ID was reused with different input or configuration") from None
                    return CallHandle(self.factory, row.id, reused=True)
                conflict = db.scalar(select(ProviderCallResource).where(
                    ProviderCallResource.resource_key.in_(resources)).limit(1))
                if conflict:
                    owner = db.get(ProviderCall, conflict.call_id)
                    raise CallBlocked(owner.id, owner.status) from None
            raise

    def inspect(self, limit: int = 50) -> list[dict]:
        if not 1 <= limit <= 200:
            raise ValueError("Invalid audit limit")
        with self.factory() as db:
            return [receipt(row) for row in db.scalars(select(ProviderCall)
                    .order_by(ProviderCall.created_at.desc(), ProviderCall.id).limit(limit))]


class CallHandle:
    def __init__(self, factory, call_id: str, reused: bool):
        self.factory, self.id, self.reused = factory, call_id, reused

    def read(self) -> dict:
        with self.factory() as db:
            return receipt(db.get(ProviderCall, self.id))

    def require_fresh(self):
        if self.reused:
            raise CallBlocked(self.id, self.read()["status"])

    def observe(self, *, request_id=None, http_status=None,
                input_tokens=None, output_tokens=None):
        values = {}
        if (ident := safe_request_id(request_id)) is not None: values["request_id"] = ident
        if type(http_status) is int and 100 <= http_status <= 599:
            values.update(http_status=http_status, response_received=True)
        if (n := safe_count(input_tokens)) is not None: values["input_tokens"] = n
        if (n := safe_count(output_tokens)) is not None: values["output_tokens"] = n
        if not values: return
        with self.factory.begin() as db:
            changed = db.execute(update(ProviderCall).where(ProviderCall.id == self.id,
                ProviderCall.status == "in_flight").values(**values))
            if changed.rowcount != 1:
                raise CallBlocked(self.id, "not_in_flight")

    def succeed(self, db, *, elapsed_ms: int, summary: dict):
        # Called inside the SAME transaction that commits the embedding vectors.
        allowed = {"embedded", "quote_exact", "draft_requires_review", "turn_accepted"}
        if set(summary) - allowed or any(type(v) not in (bool, int) for v in summary.values()):
            raise ValueError("Only bounded acceptance counters may enter an audit summary")
        changed = db.execute(update(ProviderCall).where(ProviderCall.id == self.id,
            ProviderCall.status == "in_flight").values(status="succeeded", elapsed_ms=safe_count(elapsed_ms),
            summary=summary, finished_at=iso(utcnow())))
        if changed.rowcount != 1:
            raise CallBlocked(self.id, "not_in_flight")

    def unknown(self, *, elapsed_ms: int):
        # Do not overwrite a success if the caller lost a commit acknowledgement.
        with self.factory.begin() as db:
            db.execute(update(ProviderCall).where(ProviderCall.id == self.id,
                ProviderCall.status == "in_flight").values(status="unknown",
                error_code="execution_or_commit_unconfirmed", elapsed_ms=safe_count(elapsed_ms),
                finished_at=iso(utcnow())))
