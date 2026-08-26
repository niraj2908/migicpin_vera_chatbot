.PHONY: install test lint run

install:
	pip install -e ".[dev]"

test:
	pytest -q

lint:
	ruff check src tests
	mypy src

run:
	uvicorn vera.api.app:app --reload --port 8000
