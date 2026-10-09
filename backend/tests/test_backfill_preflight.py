import pytest
from app.backfill_preflight import preflight_database, preflight_provider


def test_preflight_sqlite_fixture_is_read_only(seeded):
    s=seeded.app.state.settings.model_copy(update={
        "embedding_provider":"fixture","embedding_model":"fixture-sha256-v1","embedding_dim":8})
    with seeded.app.state.session_factory() as db:
        result=preflight_database(db,s)
    assert result["schema_ready"] is True
    assert result["database"]=="sqlite"
    assert result["pgvector_ready"] is False


def test_preflight_rejects_paid_provider_without_authorization(settings):
    s=settings.model_copy(update={
        "embedding_provider":"openai","embedding_model":"text-embedding-3-small",
        "embedding_dim":256,"embedding_api_key":"test-not-real"})
    with pytest.raises(ValueError,match="live mode"):
        preflight_provider(s,apply=True,confirm_provider="openai",allow_paid_api=True)


def test_preflight_rejects_provider_mismatch(settings):
    s=settings.model_copy(update={
        "embedding_provider":"fixture","embedding_model":"fixture-sha256-v1","embedding_dim":8})
    with pytest.raises(ValueError,match="confirmation"):
        preflight_provider(s,apply=True,confirm_provider="openai",allow_paid_api=False)


def test_preflight_dry_run_requires_no_paid_authorization(settings):
    s=settings.model_copy(update={
        "embedding_provider":"fixture","embedding_model":"fixture-sha256-v1","embedding_dim":8})
    assert preflight_provider(s,apply=False,confirm_provider=None,allow_paid_api=False) is None
