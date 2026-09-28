.PHONY: test demo live web typecheck

test:
	cd backend && python -m pytest -q

demo:
	python scripts/run_local.py --demo

live:
	python scripts/run_local.py

web:
	cd apps/web && npm run dev

typecheck:
	cd apps/web && npm run typecheck
