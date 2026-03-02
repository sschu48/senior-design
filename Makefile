# SENTINEL — Passive RF Drone Detection System
# Common dev commands. No manual venv activation needed.

SHELL := /bin/bash
VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.DEFAULT_GOAL := help

.PHONY: help venv test test-quick run-radar spectrum setup clean clean-all

help: ## Show available targets
	@echo "SENTINEL Development Commands"
	@echo "=============================="
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*##"}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

venv: $(VENV)/bin/activate ## Create venv and install all deps

$(VENV)/bin/activate:
	python3 -m venv --system-site-packages $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev,hardware]"
	@touch $(VENV)/bin/activate

test: venv ## Run full test suite
	$(PYTEST) -v

test-quick: venv ## Run tests (no slow/field markers)
	$(PYTEST) -v -m "not slow and not field"

run-radar: ## Start Express+Socket.IO radar app
	cd radar-app && npm start

spectrum: venv ## Launch spectrum analyzer tool
	$(PYTHON) -m src.ui.spectrum

setup: ## Run Ubuntu setup script
	bash scripts/setup-ubuntu.sh

clean: ## Remove Python cache and build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache build dist

clean-all: clean ## Remove venv and node_modules too
	rm -rf $(VENV)
	rm -rf radar-app/node_modules
