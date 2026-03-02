# SENTINEL — Passive RF Drone Detection System
# Common dev commands. No manual venv activation needed.

SHELL := /bin/bash
VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.DEFAULT_GOAL := help

.PHONY: help venv test test-quick test-hardware run-radar run-pipeline run-pipeline-headless run-live run-live-headless spectrum spectrum-live setup clean clean-all

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

test-quick: venv ## Run tests (no slow/field/hardware markers)
	$(PYTEST) -v -m "not slow and not field and not hardware"

run-radar: ## Start Express+Socket.IO radar app
	cd radar-app && npm start

run-pipeline: venv ## Run detection pipeline (synthetic data)
	$(PYTHON) -m tools.sentinel_runner --frames 100

run-pipeline-headless: venv ## Run pipeline headless (no per-detection output)
	$(PYTHON) -m tools.sentinel_runner --frames 100 --headless

run-live: venv ## Run detection pipeline (live USRP B210)
	$(PYTHON) -m tools.sentinel_runner --live --frames 100

run-live-headless: venv ## Run live pipeline headless
	$(PYTHON) -m tools.sentinel_runner --live --frames 100 --headless

test-hardware: venv ## Run hardware-dependent tests (requires USRP)
	$(PYTEST) -v -m hardware

spectrum: venv ## Launch spectrum analyzer (synthetic signals)
	$(PYTHON) -m src.ui.spectrum

spectrum-live: venv ## Launch spectrum analyzer (live USRP B210)
	$(PYTHON) -m src.ui.spectrum --live

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
