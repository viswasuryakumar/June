.PHONY: setup lint format test run dry-run discover-only resume-hitl playwright-install pre-commit-install

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

# Extra args, e.g.:  make run ARGS="--dry-run"
ARGS ?=

## Create the venv and install runtime + dev dependencies.
setup:
	python3.11 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

## Install Playwright's Chromium browser (see README note on disk-constrained
## environments: set PLAYWRIGHT_BROWSERS_PATH to point elsewhere if / is full).
playwright-install:
	$(PYTHON) -m playwright install chromium

## Install git pre-commit hooks (ruff + black on staged files).
pre-commit-install:
	$(VENV)/bin/pre-commit install

lint:
	$(VENV)/bin/ruff check src tests

format:
	$(VENV)/bin/black src tests
	$(VENV)/bin/ruff check --fix src tests

test:
	$(PYTHON) -m pytest -q

## NOTE ON `make run --dry-run`:
## GNU Make itself reserves the long option `--dry-run` (alias for -n,
## "print recipe commands, don't execute them") and parses it *before* ever
## looking at targets - so `make run --dry-run` makes *make* a no-op, not
## the `run` recipe. There is no portable way to make a bare `--dry-run`
## flag pass through to a recipe. Use one of these instead, all of which
## are verified to work:
##   make dry-run
##   make run ARGS="--dry-run"
##   .venv/bin/python -m src.cli.main run --dry-run
run:
	$(PYTHON) -m src.cli.main run $(ARGS)

dry-run:
	$(PYTHON) -m src.cli.main run --dry-run

discover-only:
	$(PYTHON) -m src.cli.main discover-only

## Usage: make resume-hitl TICKET_ID=abc123
resume-hitl:
	$(PYTHON) -m src.cli.main resume-hitl $(TICKET_ID)
