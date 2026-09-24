.PHONY: install dev test lint fmt run doctor demo docker clean

install:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[brokers,llm]"

dev:
	.venv/bin/pip install -e ".[dev,brokers,llm]"

test:
	.venv/bin/python -m pytest tests -q

lint:
	.venv/bin/ruff check src tests

fmt:
	.venv/bin/ruff check --fix src tests
	.venv/bin/ruff format src tests

doctor:
	.venv/bin/coattail doctor

# Kompletter Durchlauf ohne Netz und ohne Zugangsdaten
demo:
	COATTAIL_SYNTHETIC_PRICES=1 .venv/bin/coattail bootstrap
	COATTAIL_SYNTHETIC_PRICES=1 .venv/bin/coattail actors
	COATTAIL_SYNTHETIC_PRICES=1 .venv/bin/coattail run-once

run:
	.venv/bin/coattail run

docker:
	docker compose up -d --build
	docker compose logs -f

clean:
	rm -rf data logs .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
