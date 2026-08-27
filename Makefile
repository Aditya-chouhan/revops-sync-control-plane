.PHONY: install lint type test verify migrate serve demo

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
