# Developer convenience targets. Install the dev tooling first:
#     make install        # = python -m pip install -e ".[dev]"
#
# `make check` runs lint, type-check, and tests together.
# `make run` starts the service locally with auto-reload.
#
# PYTHON selects the interpreter for every target, so install and the import
# guard always use the *same* one. It defaults to python, falling back to
# python3 (whichever is on PATH). Override it to target a specific env, e.g.
#     make install PYTHON=.venv/bin/python

PYTHON ?= $(shell command -v python || command -v python3)

ifeq ($(strip $(PYTHON)),)
$(error No python3/python found on PATH — install Python or pass PYTHON=/path/to/python)
endif

.PHONY: help install run test lint typecheck check _require-install

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

install:  ## Editable install with dev extras (pulls mf_engine from the git pin)
	$(PYTHON) -m pip install -e ".[dev]"

run:  ## Run the service locally with auto-reload
	$(PYTHON) -m uvicorn mf_service:app --reload

# typecheck and tests import mf_service and mf_engine, so the package must be
# installed first (`make install`). This guard fails fast with that hint
# instead of a confusing ModuleNotFoundError on a fresh checkout. It uses the
# same $(PYTHON) as `install`, so a pip/python interpreter mismatch can't make
# it misfire after a successful install.
_require-install:
	@$(PYTHON) -c "import mf_service, mf_engine" 2>/dev/null \
		|| { echo "mf_service / mf_engine not importable — run 'make install' first (same interpreter: $(PYTHON))."; exit 1; }

test: _require-install  ## Run the test suite (with coverage)
	$(PYTHON) -m pytest

lint:  ## Lint with ruff
	$(PYTHON) -m ruff check .

typecheck: _require-install  ## Type-check with mypy
	$(PYTHON) -m mypy

check: lint typecheck test  ## Run lint, type-check, and tests
