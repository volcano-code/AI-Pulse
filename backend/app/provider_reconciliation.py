"""Operator-attested reconciliation and explicit one-use retry authorizations.

No network I/O. Original calls and observations are never erased or relabelled
as successful. A decision is an operator assertion, not provider verification.
"""
from __future__ import annotations

import re
from sqlalchemy import select, update
from .models import ProviderCall, ProviderCallResource, ProviderReconciliation
from .provider_calls import CallBlocked, CallConflict, CallHandle, fingerprint, receipt
from .timeutil import iso, utcnow

RETRY_KINDS = {"llm_canary", "embedding_backfill", "brief_draft", "query_embedding"}


def inspect_call(factory, call_id):
    with factory() as db:
        row = db.get(ProviderCall, call_id)
        if row is None:
            raise LookupError("Provider call not found")
        return {"receipt": receipt(row), "expected_fingerprint": fingerprint([row.request_hash, receipt(row)]),
                "reconciliation": decision_payload(db.scalar(select(ProviderReconciliation).where(
                    ProviderReconciliation.call_id == call_id)))}


def decision_payload(row):
    if row is None:
        return None
    return {"id": row.id, "call_id": row.call_id, "decision": row.decision,
            "operator_billing": row.operator_billing, "evidence_sha256": row.evidence_sha256,
            "actor_hash": row.actor_hash, "created_at": row.created_at,
            "replacement_call_id": row.replacement_call_id,
            "provider_verified": False, "automatic_retry_allowed": False}


def reconcile(factory, *, call_id, expected_fingerprint, decision, operator_billing,
              evidence_sha256, actor, apply=False, confirm_call_id=None,
              workers_stopped=False, acknowledge_possible_charge=False):
    if decision not in {"keep_blocked", "authorize_retry"}:
        raise ValueError("Invalid reconciliation decision")
    if operator_billing not in {"charged", "not_charged", "unknown"}:
        raise ValueError("Invalid billing assertion")
    if (not isinstance(actor, str) or not 1 <= len(actor.strip()) <= 128 or
            any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v)
                for v in (expected_fingerprint, evidence_sha256))):
        raise ValueError("Actor and SHA-256 evidence/fingerprint are required")
    identity = fingerprint([call_id, expected_fingerprint, decision, operator_billing,
                            evidence_sha256, fingerprint(actor.strip())])
    if apply and (confirm_call_id != call_id or not workers_stopped or
                  (decision == "authorize_retry" and not acknowledge_possible_charge)):
        raise ValueError("Explicit call confirmation, stopped workers and retry-risk acknowledgment required")
    with factory.begin() as db:
        if apply:
            # A write acquires a DB lock on BOTH SQLite and PostgreSQL. A plain
            # SELECT FOR UPDATE would not serialize the SQLite contract tests.
            db.execute(update(ProviderCall).where(ProviderCall.id == call_id).values(status=ProviderCall.status))
        row = db.get(ProviderCall, call_id)
        if row is None:
            raise LookupError("Provider call not found")
        previous = db.scalar(select(ProviderReconciliation).where(ProviderReconciliation.call_id == call_id))
        if previous:
            if previous.decision_hash != identity:
                raise CallConflict("An immutable decision already exists for this attempt")
            return {"status": "recorded" if apply else "dry_run", "reused": True,
                    "decision": decision_payload(previous), "provider_calls": 0}
        if fingerprint([row.request_hash, receipt(row)]) != expected_fingerprint:
            raise CallConflict("Call changed since preview; inspect it again")
        if row.status not in {"in_flight", "unknown"}:
            raise ValueError("Only unresolved attempts can be reconciled")
        if decision == "authorize_retry" and row.kind not in RETRY_KINDS:
            raise ValueError("Research turns cannot be resumed or retried by this tool")
        if not apply:
            return {"status": "dry_run", "call_id": row.id, "decision": decision,
                    "operator_billing": operator_billing, "provider_verified": False, "provider_calls": 0}
        if row.status == "in_flight":
            # The operator attests all old executors are stopped. Even a late
            # callback now fails the original handle's in_flight CAS guard.
            row.status, row.error_code = "unknown", "operator_confirmed_interruption"
            row.finished_at = iso(utcnow())
        item = ProviderReconciliation(call_id=row.id, decision_hash=identity,
            decision=decision, operator_billing=operator_billing, evidence_sha256=evidence_sha256,
            actor_hash=fingerprint(actor.strip()))
        db.add(item); db.flush()
        return {"status": "recorded", "reused": False, "decision": decision_payload(item), "provider_calls": 0}


def resolve_existing(factory, call_id, *, retry_authorization=None, commit_guard=None):
    """Follow committed replacements; dispatch a new attempt ONLY with its ticket.

    Reservation, resource transfer and ticket consumption commit atomically.
    The caller must independently authorize any paid execution. No credentials
    or request data are reconstructed from audit metadata.
    """
    with factory.begin() as db:
        db.execute(update(ProviderCall).where(ProviderCall.id == call_id).values(status=ProviderCall.status))
        row = db.get(ProviderCall, call_id)
        consumed_ticket_seen = False
        for _ in range(32):
            item = db.scalar(select(ProviderReconciliation).where(ProviderReconciliation.call_id == row.id))
            if item and item.replacement_call_id:
                if retry_authorization == item.id:
                    consumed_ticket_seen = True
                row = db.get(ProviderCall, item.replacement_call_id)
                if row is None:
                    raise CallConflict("Replacement audit record is missing")
                continue
            if retry_authorization is None or consumed_ticket_seen:
                return CallHandle(factory, row.id, reused=True)
            if not item or item.id != retry_authorization or item.decision != "authorize_retry":
                raise CallConflict("Authorization does not match this exact request")
            if row.status != "unknown" or row.kind not in RETRY_KINDS:
                raise CallBlocked(row.id, row.status)
            if commit_guard:
                commit_guard(db)
            child = ProviderCall(operation_hash=fingerprint(["authorized-retry", row.id, item.id]),
                request_hash=row.request_hash, kind=row.kind, provider=row.provider,
                model=row.model, data_mode=row.data_mode, input_count=row.input_count, status="in_flight")
            db.add(child); db.flush()
            db.execute(update(ProviderCallResource).where(ProviderCallResource.call_id == row.id)
                       .values(call_id=child.id))
            item.replacement_call_id = child.id
            return CallHandle(factory, child.id, reused=False)
        raise CallConflict("Retry ancestry exceeds inspection limit")
