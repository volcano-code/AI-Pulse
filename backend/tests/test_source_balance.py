"""Source balance is a selection policy, not evidence of truth or quality."""
from collections import Counter
from datetime import timedelta
import pytest
from sqlalchemy import select
from app.briefing import build_brief, brief_payload, choose_candidates
from app.feeds import Entry
from app.ingestion import ingest_entries
from app.models import Article, Brief, Event, EventArticle, Source, Workspace
from app.schemas import Preferences
from app.timeutil import iso, utcnow


def add(db, source_id, label, *, hours=1, text=None, title=None):
    ingest_entries(db, db.get(Source, source_id), [Entry(
        title or f"Agent {label}", f"https://example.com/{label}",
        text or f"Evidence for {label} about an Agent workflow.",
        iso(utcnow() - timedelta(hours=hours)))], "replay")
    return db.scalar(select(Article).where(Article.canonical_url == f"https://example.com/{label}"))


def test_balance_prevents_high_volume_source_monopoly(client):
    factory = client.app.state.session_factory
    with factory.begin() as db:
        for n in range(12): add(db, "arxiv", f"dominant-{n}")
        for n in range(4): add(db, "huggingface", f"hf-{n}", hours=2)
        for n in range(4): add(db, "langgraph", f"lg-{n}", hours=3)
    with factory() as db:
        rows = choose_candidates(db, Preferences(max_items=8), utcnow(), "replay")
        counts = Counter(source.id for _, _, source, _ in rows)
        assert len(rows) == 8 and len(counts) == 3 and max(counts.values()) <= 3
        assert all("来源均衡" in reason for *_, reason in rows)
        plain = choose_candidates(db, Preferences(max_items=8, diversify_sources=False), utcnow(), "replay")
        assert {source.id for _, _, source, _ in plain} == {"arxiv"}


def test_balance_fills_sparse_source_slots(client):
    factory = client.app.state.session_factory
    with factory.begin() as db:
        for n in range(12): add(db, "arxiv", f"a-{n}")
        add(db, "huggingface", "only-one", hours=3)
    with factory() as db:
        rows = choose_candidates(db, Preferences(max_items=8), utcnow(), "replay")
        assert len(rows) == 8
        assert Counter(s.id for _, _, s, _ in rows) == {"arxiv": 7, "huggingface": 1}


@pytest.mark.parametrize("exclusion", ["disabled", "stale", "future", "keyword"])
def test_balance_never_reintroduces_excluded_source(client, exclusion):
    factory = client.app.state.session_factory
    with factory.begin() as db:
        for n in range(8): add(db, "arxiv", f"safe-{n}")
        add(db, "huggingface", "exclude-me", hours={"stale": 200, "future": -3}.get(exclusion, 1))
        if exclusion == "disabled": db.get(Source, "huggingface").enabled = False
    prefs = Preferences(blocked_keywords=["exclude-me"] if exclusion == "keyword" else [])
    with factory() as db:
        rows = choose_candidates(db, prefs, utcnow(), "replay")
        assert len(rows) == 8 and {s.id for _, _, s, _ in rows} == {"arxiv"}


def test_balance_deduplicates_event_members_across_sources(client):
    factory = client.app.state.session_factory
    with factory.begin() as db:
        a = add(db, "arxiv", "version-a")
        b = add(db, "huggingface", "version-b")
        event = Event(title="Same announcement", topic="Agent", data_mode="replay")
        db.add(event); db.flush()
        db.add_all([EventArticle(event_id=event.id, article_id=x.id, match_score=1.0) for x in (a, b)])
    with factory() as db:
        assert len(choose_candidates(db, Preferences(), utcnow(), "replay")) == 1


def test_selection_summary_is_frozen_and_toggle_is_idempotent(seeded):
    factory, settings = seeded.app.state.session_factory, seeded.app.state.settings
    one = build_brief(factory, settings)
    assert build_brief(factory, settings)["brief_id"] == one["brief_id"]
    with factory() as db:
        first = brief_payload(db, db.get(Brief, one["brief_id"]))["selection_summary"]
        assert first["policy"] == "source_balanced" and first["source_count"] == 3
    with factory.begin() as db:
        db.get(Source, "huggingface").name = "Renamed today"
        db.get(Workspace, 1).preferences = Preferences(diversify_sources=False).model_dump()
    with factory() as db:
        assert brief_payload(db, db.get(Brief, one["brief_id"]))["selection_summary"] == first
    two = build_brief(factory, settings)
    assert two["brief_id"] != one["brief_id"]
    assert build_brief(factory, settings)["brief_id"] == two["brief_id"]


def test_legacy_preferences_get_safe_default_without_rewriting_old_brief(seeded):
    factory = seeded.app.state.session_factory
    with factory.begin() as db:
        prefs = dict(db.get(Workspace, 1).preferences); prefs.pop("diversify_sources", None)
        db.get(Workspace, 1).preferences = prefs
    # Existing stored JSON is not rewritten on GET; Pydantic/UI use the default.
    assert "diversify_sources" not in seeded.get("/api/v1/preferences").json()
    built = build_brief(factory, seeded.app.state.settings)
    with factory() as db:
        assert brief_payload(db, db.get(Brief, built["brief_id"]))["selection_summary"]["policy"] == "source_balanced"


def test_source_balance_respects_single_item_preference(client):
    factory = client.app.state.session_factory
    with factory.begin() as db:
        add(db, "arxiv", "preferred", hours=40)
        add(db, "huggingface", "other", hours=1, title="Vision image", text="Visual result")
    with factory() as db:
        rows = choose_candidates(db, Preferences(topics=["Agent"], max_items=1), utcnow(), "replay")
        assert rows[0][0].canonical_url.endswith("preferred")
