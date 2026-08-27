.PHONY: install lint type test verify migrate serve demo hubspot-sync

install:
	python -m pip install -e ".[dev]"

lint:
	ruff check .

type:
	mypy src

test:
	pytest --cov=src/revops_sync --cov-report=term-missing

verify: lint type test

migrate:
	alembic upgrade head

serve:
	uvicorn revops_sync.main:app --reload

demo:
	revops-sync reconcile

hubspot-sync:
	# Requires HUBSPOT_ACCESS_TOKEN pointed at a free HubSpot developer test
	# portal (not production). See evidence/hubspot_live_sync_2026-08-27.json
	# for the last real run's output.
	python scripts/hubspot_live_sync.py
