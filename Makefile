# Developer convenience targets. Install the dev tooling first:
#     pip install -e ".[dev]"
#
# `make check` runs lint, type-check, and tests together.
# `make run` starts the service locally with auto-reload.

.PHONY: help install run test lint typecheck check _require-install

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

install:  ## Editable install with dev extras (pulls mf_engine from the git pin)
	pip install -e ".[dev]"

run:  ## Run the service locally with auto-reload
	uvicorn mf_service:app --reload

# typecheck and tests import mf_service and mf_engine, so the package must be
# installed first (`make install`). This guard fails fast with that hint
# instead of a confusing ModuleNotFoundError on a fresh checkout.
_require-install:
	@python -c "import mf_service, mf_engine" 2>/dev/null \
		|| { echo "mf_service / mf_engine not importable — run 'make install' first."; exit 1; }

test: _require-install  ## Run the test suite (with coverage)
	pytest

lint:  ## Lint with ruff
	ruff check .

typecheck: _require-install  ## Type-check with mypy
	mypy

check: lint typecheck test  ## Run lint, type-check, and tests
