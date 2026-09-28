from uuid import uuid4
from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base
from .timeutil import iso, utcnow

def uid():
    return str(uuid4())

def now():
    return iso(utcnow())

class Workspace(Base):
    __tablename__ = "workspace"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    data_mode: Mapped[str] = mapped_column(String(20))
    preferences: Mapped[dict] = mapped_column(JSON)

class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    url: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_success_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_attempt_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)

class Article(Base):
    __tablename__ = "articles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    canonical_url: Mapped[str] = mapped_column(Text, unique=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    title: Mapped[str] = mapped_column(Text)
    published_at: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    updated_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    first_seen_at: Mapped[str] = mapped_column(String(40), default=now)
    event_time: Mapped[str | None] = mapped_column(String(40), nullable=True)
    current_snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    topic: Mapped[str] = mapped_column(String(30), default="其他")
    bookmarked: Mapped[bool] = mapped_column(Boolean, default=False)
    data_mode: Mapped[str] = mapped_column(String(20))

class Snapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("article_id", "content_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    article_id: Mapped[str] = mapped_column(ForeignKey("articles.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    captured_at: Mapped[str] = mapped_column(String(40), default=now)
    scope: Mapped[str] = mapped_column(String(40), default="feed_entry")

class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(String(30), index=True)
    data_mode: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    updated_at: Mapped[str] = mapped_column(String(40), default=now)
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

class EventArticle(Base):
    __tablename__ = "event_articles"
    __table_args__ = (UniqueConstraint("article_id"),)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), primary_key=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("articles.id"), primary_key=True)
    match_score: Mapped[float] = mapped_column(Float, default=1.0)

class EventVersion(Base):
    __tablename__ = "event_versions"
    __table_args__ = (UniqueConstraint("event_id", "fingerprint"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    article_snapshots: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String(40), default=now)

class FetchRun(Base):
    __tablename__ = "fetch_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    started_at: Mapped[str] = mapped_column(String(40), default=now)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(25), default="queued")
    # One active local pipeline. Database uniqueness closes concurrent-submit races.
    active_key: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    events: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # v0.2 durable execution. Legacy rows remain local and are never replayed.
    engine: Mapped[str] = mapped_column(String(20), default="local", server_default="local")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    available_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[str | None] = mapped_column(String(40), nullable=True)
    request_key: Mapped[str | None] = mapped_column(String(180), unique=True, nullable=True)
    request: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")

class Brief(Base):
    __tablename__ = "briefs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    local_date: Mapped[str] = mapped_column(String(10), index=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    cutoff_at: Mapped[str] = mapped_column(String(40))
    window_start: Mapped[str] = mapped_column(String(40))
    timezone: Mapped[str] = mapped_column(String(70))
    status: Mapped[str] = mapped_column(String(30))
    data_mode: Mapped[str] = mapped_column(String(20))
    generation_mode: Mapped[str] = mapped_column(String(20))
    model: Mapped[str | None] = mapped_column(String(150), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(30), default="evidence-v1")
    preferences: Mapped[dict] = mapped_column(JSON)
    source_health: Mapped[list] = mapped_column(JSON, default=list)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    approved_at: Mapped[str | None] = mapped_column(String(40), nullable=True)

class BriefItem(Base):
    __tablename__ = "brief_items"
    __table_args__ = (UniqueConstraint("brief_id", "position"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    brief_id: Mapped[str] = mapped_column(ForeignKey("briefs.id"), index=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("articles.id"))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshots.id"))
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    source_name: Mapped[str] = mapped_column(String(150))
    source_url: Mapped[str] = mapped_column(Text)
    published_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    topic: Mapped[str] = mapped_column(String(30))
    evidence_quote: Mapped[str] = mapped_column(Text)
    quote_start: Mapped[int] = mapped_column(Integer)
    quote_end: Mapped[int] = mapped_column(Integer)
    verification: Mapped[str] = mapped_column(String(40))
    relevance_reason: Mapped[str] = mapped_column(Text)


class DailySchedule(Base):
    __tablename__ = "daily_schedule"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    local_time: Mapped[str] = mapped_column(String(5), default="08:00")
    timezone: Mapped[str] = mapped_column(String(70), default="America/Los_Angeles")
    send: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[str] = mapped_column(String(40), default=now)


class Delivery(Base):
    __tablename__ = "deliveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    brief_id: Mapped[str] = mapped_column(ForeignKey("briefs.id"), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True)
    transport: Mapped[str] = mapped_column(String(20))
    recipient: Mapped[str] = mapped_column(String(320))
    sender: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(250))
    body_text: Mapped[str] = mapped_column(Text)
    message_id: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    available_at: Mapped[str] = mapped_column(String(40), default=now)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_name: Mapped[str | None] = mapped_column(String(100), nullable=True)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    last_seen_at: Mapped[str] = mapped_column(String(40))
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ReadingState(Base):
    __tablename__ = "reading_states"
    article_id: Mapped[str] = mapped_column(ForeignKey("articles.id"), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshots.id"))
    read_at: Mapped[str] = mapped_column(String(40), default=now)


class Investigation(Base):
    __tablename__ = "investigations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    request_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(Text)
    brief_id: Mapped[str | None] = mapped_column(ForeignKey("briefs.id"), nullable=True)
    data_mode: Mapped[str] = mapped_column(String(20))
    generation_mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="running")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    trace: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)

class EvidenceChunk(Base):
    __tablename__ = "evidence_chunks"
    __table_args__ = (UniqueConstraint("snapshot_id", "ordinal"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshots.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    embedding_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)

