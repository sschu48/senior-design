# SENTINEL — Passive RF Drone Detection System

Detect, identify, and localize consumer drones at 2.4 GHz by passively receiving their RF emissions. No active radar, no transmission, no detectability.

**Target:** 200-yard detection range (open sky) using a USRP B210 SDR with dual-channel MIMO — omnidirectional antenna for presence detection, directional Yagi for cued bearing estimation and signal identification.

---

## Current Status

> **Active branch:** [`feature/v2-pipeline`](https://github.com/sschu48/senior-design/tree/feature/v2-pipeline) — V2 detection pipeline rewrite ([PR #1, draft](https://github.com/sschu48/senior-design/pull/1)).
> **`main`:** V1 (deprecated, never validated). Don't build off it until V2 merges.

| Phase | Status | Notes |
|---|---|---|
| Phase 0 — V2 scaffold | ✅ Complete | Stage protocol, contract types, config |
| Phase 1 — V2 pipeline | ✅ Complete | All 8 stages, V1 deleted, 250+ tests |
| Phase 1.5 — Architecture gap-close | ✅ Complete | Spectrogram buffer, swept bearing, BEARING_SEARCH/ALARM |
| **Phase 2 — Hardware validation** | 🔧 **In progress** | ESP32 + HackRF bench runs |
| Phase 3 — Enrichment | ⏳ Future | Multi-burst clustering, ML, decoders |

Why the V2 rewrite: V1 (`TripwireDetector` + `CFARDetector` parallel paths) never produced reliable detections in field testing — see `docs/ARCHITECTURE_V2.md` for the full rationale and `docs/V2_MIGRATION_PLAN.md` for the phase-by-phase plan.

---

## Quick Start

```bash
git clone https://github.com/sschu48/senior-design.git
cd senior-design
git checkout feature/v2-pipeline       # V2 lives here until merge
bash scripts/setup-ubuntu.sh           # install deps, create venv, download FPGA images
source .venv/bin/activate
make test                              # 271 tests should pass
make run-pipeline                      # run V2 pipeline against synthetic signals
```

See [docs/linux-setup.md](docs/linux-setup.md) for manual setup, troubleshooting, and hardware verification.

---

## Architecture (V2)

```
Omni Antenna ──► BPF+LNA ──► USRP B210 RX-A ──┐
                                              │  DualIQFrame
Yagi Antenna ──► BPF+LNA ──► USRP B210 RX-B ──┘
   ↑                                          │
Pan Servo                                     ▼
   ↑                                  Source ─► Spectrogram ─► Burst ─► Cluster ─► Classify
   │                                                                             │ cue
   │                                                                             ▼
   └─── BEARING_SEARCH ◄──── ALARM ◄──── TRACK ◄────── Bearing ─► Fuse ─► Track
```

**Typed staged DAG.** Each stage is one file under `src/pipeline/stages/`, takes one typed input, returns one typed output. Subscribe to any stage's emit queue for visualization or logging without touching the pipeline.

**Cued architecture.** Omni channel does always-on structural burst detection. When a burst classifies as a known protocol, the Yagi state machine enters `BEARING_SEARCH`, sweeps the azimuth range while accumulating per-azimuth Yagi power, and emits an `RFEvent` (the operational `ALARM`) once the sweep completes — then settles into `TRACK` at the peak bearing.

Full design rationale: [docs/ARCHITECTURE_V2.md](docs/ARCHITECTURE_V2.md). Phase plan: [docs/V2_MIGRATION_PLAN.md](docs/V2_MIGRATION_PLAN.md).

---

## Project Structure

```
src/
  sdr/         IQ capture (Synthetic + USRP B210, single + dual RX) + config loader
  dsp/         FFT primitives + DSP utilities
    spectrum.py            Welch PSD
    noise_floor.py         Protected EMA noise-floor estimator
    cfar.py                CA-CFAR kernel
    spectrogram_buffer.py  Rolling time-frequency buffer
    persistence.py         Per-bin duty-cycle tracker
    classifiers/           Rule-based protocol IDs (elrs, ocusync, wifi)
  antenna/     Pan controller (SimulatedController)
               State machine: IDLE → SCAN → CUE → BEARING_SEARCH → ALARM → TRACK
  pipeline/    V2 staged DAG
    stage.py               Stage[InMsg, OutMsg] base class with subscribe/emit
    contracts.py           Typed messages (IQ, Spectrogram, Burst, Candidate, ...)
    pipeline.py            Pipeline runner (assembles stages, drives antenna)
    stages/                One file per stage:
                           source, spectrogram, burst, cluster,
                           classify, bearing, fuse, track
  ui/          aiohttp WebSocket dashboard + live spectrum view
tools/
  sentinel_runner.py     Demo pipeline runner (synthetic or USRP)
  bench_test.py          Configurable bench harness with JSON reports
  bench_snr_sweep.py     USRP gain/FFT/window sweep
  hackrf_bench.py        RX harness for HackRF dummy-drone bench
  hackrf_tx/             "Dummy drone" TX: DJI DroneID / OcuSync / tone / replay
  esp32_beacon/           ESP32 WiFi-beacon test transmitter
tests/
  unit/                  Per-primitive + per-stage tests
  integration/           End-to-end Pipeline tests on synthetic IQ
  field/                 Hardware/field test scripts and logs
docs/
  ARCHITECTURE_V2.md     Detection-strategy rationale (read first)
  V2_MIGRATION_PLAN.md   Phase-by-phase implementation plan
  rf-research/, hardware/, test-logs/
config.yaml              All hardware/DSP parameters (zero hardcoded values)
```

---

## Branch Policy

- **`main`** — stable, validated. **No direct pushes.**
- **`feature/<name>`** — feature branches. Open a draft PR early, push freely; merge only after validation.
- **`experiment/<name>`** — exploratory work that may not land.

**Workflow:** branch off `main` → push commits → open draft PR → mark "ready for review" when done → merge via GitHub. Each push to a feature branch auto-updates its PR.

---

## Make Targets

```
make test              Run full test suite
make test-quick        Skip slow/field/hardware tests
make test-hardware     Run USRP B210 hardware tests only
make run-pipeline      V2 pipeline against synthetic dual-RX signals
make run-live          V2 pipeline against live USRP B210
make spectrum          Real-time spectrum analyzer (synthetic)
make spectrum-live     Real-time spectrum analyzer (live B210)
make hackrf-tx         Dummy drone TX (HackRF, default DJI DroneID profile)
make hackrf-tx-list    List available HackRF TX profiles
make help              Show all targets
```

---

## Documentation

| Document | Contents |
|---|---|
| [ARCHITECTURE_V2.md](docs/ARCHITECTURE_V2.md) | V2 detection strategy: why energy-bump-on-Yagi fails for FHSS drones, what replaces it |
| [V2_MIGRATION_PLAN.md](docs/V2_MIGRATION_PLAN.md) | Phase-by-phase plan for the V1 → V2 rewrite |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Module architecture, data flow, how to extend the system |
| [TESTING.md](docs/TESTING.md) | Test strategy, writing tests, field test protocol |
| [phase-1-5-rf-validation.md](docs/phase-1-5-rf-validation.md) | RF validation plan and checkpoint |
| [hardware/hackrf-bench-setup.md](docs/hardware/hackrf-bench-setup.md) | Pi/HackRF transmitter vs B210 receiver bench setup |
| [linux-setup.md](docs/linux-setup.md) | Ubuntu setup, B210 verification, troubleshooting |
| [rf-research/rf-primer-for-sentinel.md](docs/rf-research/rf-primer-for-sentinel.md) | RF fundamentals, link-budget intuition, research reading list |
| [rf-research/drone-emissions.md](docs/rf-research/drone-emissions.md) | Protocol profiles, clutter analysis, references |
| [hardware/b210-notes.md](docs/hardware/b210-notes.md) | USRP B210 configuration, MIMO limits, input protection |
| [CLAUDE.md](CLAUDE.md) / [AGENTS.md](AGENTS.md) | AI development guide, coding rules, decision rules |
