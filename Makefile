# SENTINEL — Passive RF Drone Detection System
# Common dev commands. No manual venv activation needed.

SHELL := /bin/bash
VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.DEFAULT_GOAL := help

.PHONY: help venv test test-quick test-hardware run-pipeline run-pipeline-headless run-dual run-dual-live run-live run-live-headless spectrum spectrum-live dashboard dashboard-live dashboard-detect dashboard-detect-live bench-test bench-test-live hackrf-bench-setup hackrf-bench-tone hackrf-bench-ocusync hackrf-bench-droneid hackrf-tx hackrf-tx-list hackrf-tx-tone hackrf-tx-ocusync hackrf-tx-droneid hackrf-tx-dryrun setup clean clean-all

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

run-pipeline: venv ## Run detection pipeline (synthetic data)
	$(PYTHON) -m tools.sentinel_runner --frames 100

run-pipeline-headless: venv ## Run pipeline headless (no per-detection output)
	$(PYTHON) -m tools.sentinel_runner --frames 100 --headless

run-dual: venv ## Run dual-RX pipeline (synthetic omni + yagi)
	$(PYTHON) -m tools.sentinel_runner --dual --frames 100

run-dual-live: venv ## Run dual-RX pipeline (live USRP B210 MIMO)
	$(PYTHON) -m tools.sentinel_runner --dual --live --frames 100

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

dashboard: venv ## Launch web dashboard (synthetic signals)
	$(PYTHON) -m src.ui.dashboard

dashboard-live: venv ## Launch web dashboard (live USRP B210)
	$(PYTHON) -m src.ui.dashboard --live

dashboard-detect: venv ## Dashboard with detection overlay (synthetic)
	$(PYTHON) -m src.ui.dashboard --detect

dashboard-detect-live: venv ## Dashboard with detection overlay + USRP
	$(PYTHON) -m src.ui.dashboard --live --detect

bench-test: venv ## Bench test (synthetic baseline)
	$(PYTHON) -m tools.bench_test

bench-test-live: venv ## Bench test with USRP (gain=25, indoor safe)
	$(PYTHON) -m tools.bench_test --live --gain 25

hackrf-bench-setup: venv ## Print the HackRF/B210 bench setup commands
	$(PYTHON) -m tools.hackrf_bench --profile tone_2437 --setup-only

hackrf-bench-tone: venv ## RX-side HackRF tone bench test (live dual B210)
	$(PYTHON) -m tools.hackrf_bench --live --dual --profile tone_2437 --gain 10

hackrf-bench-ocusync: venv ## RX-side HackRF continuous OFDM bench test
	$(PYTHON) -m tools.hackrf_bench --live --dual --profile ocusync_video --gain 10 --duration 3

hackrf-bench-droneid: venv ## RX-side HackRF bursty DroneID-like bench test
	$(PYTHON) -m tools.hackrf_bench --live --dual --profile dji_droneid --gain 10 --duration 10

hackrf-tx: venv ## Run HackRF dummy drone (default profile = DJI DroneID)
	$(PYTHON) -m tools.hackrf_tx

hackrf-tx-list: venv ## List available HackRF TX profiles
	$(PYTHON) -m tools.hackrf_tx --list-profiles

hackrf-tx-tone: venv ## Run HackRF with a CW tone at 2.437 GHz (smoke test)
	$(PYTHON) -m tools.hackrf_tx --profile tone_2437

hackrf-tx-ocusync: venv ## Run HackRF continuous OcuSync-like OFDM profile
	$(PYTHON) -m tools.hackrf_tx --profile ocusync_video --gain 0

hackrf-tx-droneid: venv ## Run HackRF bursty DroneID-like profile
	$(PYTHON) -m tools.hackrf_tx --profile dji_droneid --gain 0

hackrf-tx-dryrun: venv ## Generate IQ + show banner; no transmission
	$(PYTHON) -m tools.hackrf_tx --dry-run

setup: ## Run Ubuntu setup script
	bash scripts/setup-ubuntu.sh

clean: ## Remove Python cache and build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache build dist

clean-all: clean ## Remove venv too
	rm -rf $(VENV)
