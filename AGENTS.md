# Maintainer instructions

Use README.md and docs/RELEASE-v0.3.md as the current state, not historical plans.
Run `cd backend && python -m pytest -q` before changing the release status.
Never mix replay/synthetic data with live data. Never claim a mocked provider is a real model call, a file outbox is an email, a bridge browser is native networking, or syntax transpilation is a Next.js build.
Keep immutable snapshots and exact Unicode evidence offsets. Generated claims remain needs_review; quote identity is not semantic fact verification.
Keep secrets out of argv/logs/artifacts. Do not send mail or call paid APIs in tests. Changes to external effects require explicit configuration/authorization.
The current runtime is single-owner, one API process + one SQL queue Worker, not LangGraph/Redis/Celery. Research interruption is conservatively stopped, not automatically billed again.
Use disposable databases for destructive migration tests. Never downgrade production data.
