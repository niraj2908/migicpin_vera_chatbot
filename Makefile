.PHONY: install test lint run serve

install:
	pip install -e ".[dev]"

test:
	pytest -q

lint:
	ruff check src tests scripts
	mypy src

run:
	uvicorn vera.api.app:app --reload --port 8000

# Production entrypoint: binds 0.0.0.0 (required to be reachable from outside a container/host --
# uvicorn's default of 127.0.0.1 is not), reads $PORT (the convention most PaaS platforms inject
# and require binding to; falls back to 8000 for local use), and deliberately omits --reload
# (dev-only: spawns a file-watcher subprocess with no place in a deployed process).
serve:
	uvicorn vera.api.app:app --host 0.0.0.0 --port $${PORT:-8000}
