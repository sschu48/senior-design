# SENTINEL — Passive RF Drone Detection System

Detect, identify, and localize consumer drones at 2.4 GHz by passively receiving their RF emissions. No active radar, no transmission, no detectability.

**Target:** 200-yard detection range (open sky) using a USRP B210 SDR with dual-channel MIMO — omnidirectional antenna for presence alerting, directional Yagi for bearing estimation and signal identification.

## Quick Start

```bash
git clone https://github.com/sschu48/senior-design.git
cd senior-design
bash scripts/setup-ubuntu.sh     # install deps, create venv, download FPGA images
source .venv/bin/activate
make test                        # verify everything works
make run-pipeline                # run detection pipeline with synthetic signals
```

See [docs/linux-setup.md](docs/linux-setup.md) for manual setup, troubleshooting, and hardware verification.

## Architecture

```
Omni Antenna ──→ BPF+LNA ──→ USRP B210 RX-A ──→ PSD ──→ TripwireDetector ──┐
                                                                              ├──→ Dedup ──→ Antenna Control ──→ JSON Log
Yagi Antenna ──→ BPF+LNA ──→ USRP B210 RX-B ──→ PSD ──→ CFARDetector ──────┘
     ↑
  Pan Servo (azimuth scan / cue / track)
```

**Dual-detector design:** The omni channel runs an energy tripwire ("something appeared in the band"). The yagi channel runs CA-CFAR for precision detection with bearing context. Detections drive the antenna through a SCAN → CUE → TRACK state machine.

## Project Structure

```
src/
  sdr/        IQ capture (SyntheticSource, USRPSource) + config loader
  dsp/        PSD computation (Welch) + detectors (Tripwire, CA-CFAR)
  antenna/    Pan/tilt controller (SimulatedController)
  pipeline/   Async engine wiring source → PSD → detect → antenna → log
  ui/         Real-time spectrum analyzer (matplotlib)
tools/
  esp32_beacon/   Cheap WiFi-beacon test transmitter (range/sensitivity sweeps)
  hackrf_tx/      "Dummy drone" RF source: synthesizes DJI DroneID / OcuSync /
                  CW tone, or replays captured IQ. See tools/hackrf_tx/README.md.
  bench_test.py   Configurable bench harness (synthetic or USRP)
  bench_snr_sweep.py  USRP gain/FFT/window sweep
  sentinel_runner.py  Demo pipeline runner
tests/unit/   60+ tests with synthetic IQ data
config.yaml   All hardware/DSP parameters (zero hardcoded values)
```

## Make Targets

```
make test              Run full test suite
make test-quick        Skip slow/field/hardware tests
make test-hardware     Run USRP B210 hardware tests only
make run-pipeline      Detection pipeline (synthetic data)
make run-live          Detection pipeline (live USRP B210)
make spectrum          Real-time spectrum analyzer (synthetic)
make spectrum-live     Real-time spectrum analyzer (live B210)
make hackrf-tx         Dummy drone TX (HackRF, default DJI DroneID profile)
make hackrf-tx-list    List available HackRF TX profiles
make help              Show all targets
```

## Documentation

| Document | Contents |
|---|---|
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Module architecture, data flow, how to extend the system |
| [TESTING.md](docs/TESTING.md) | Test strategy, writing tests, field test protocol |
| [architecture.md](docs/architecture.md) | Full system design, link budget, DB schema, block diagrams |
| [linux-setup.md](docs/linux-setup.md) | Ubuntu setup, B210 verification, troubleshooting |
| [rf-research/drone-emissions.md](docs/rf-research/drone-emissions.md) | Protocol profiles, clutter analysis, references |
| [hardware/b210-notes.md](docs/hardware/b210-notes.md) | USRP B210 configuration, MIMO limits, input protection |
| [CLAUDE.md](CLAUDE.md) | AI development guide, coding rules, decision rules |

## Current Status

**Phase 1 (Foundation)** — core pipeline operational, preparing for field testing.

| Component | Status |
|---|---|
| SDR capture (synthetic + USRP B210) | Done |
| PSD computation (Welch) | Done |
| TripwireDetector (adaptive noise floor) | Done |
| CFARDetector (CA-CFAR) | Done |
| Antenna controller (simulated) | Done |
| Pipeline engine (async) | Done |
| Config system (frozen dataclasses) | Done |
| RemoteID / DJI DroneID decoders | Not started |
| Real antenna hardware control | Not started |
| Radar app ↔ pipeline WebSocket | Not started |
