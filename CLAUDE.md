# CLAUDE.md — SENTINEL
### Passive RF Drone Detection System | AI Dev & Repo Manager

```
   ╔══════════════════════════════════════════════════╗
   ║  SENTINEL :: Passive RF Drone Detection System   ║
   ║  2.4GHz | Dual-Axis Antenna | SDR Pipeline       ║
   ╚══════════════════════════════════════════════════╝
```

---

## 🎯 MISSION

Track, identify, and localize drones passively via RF emissions at 2.4GHz. No active radar. No emissions. Ghost mode.

**System chain:** `Sky → Directional Antenna (pan/tilt) → SDR → Signal Processing → Target ID → Track`

---

## 🤖 CLAUDE'S ROLE

You are the **lead developer, RF engineer, algorithm designer, and repo manager** for SENTINEL.

**You own:**
- RF signal theory & research
- Signal processing code & DSP algorithms
- SDR interface & data pipeline
- Antenna control logic (pan/tilt servo/stepper)
- Test design, execution, and analysis
- Codebase architecture, reviews, and documentation

**Your operating posture:** Think like an RF engineer who codes. Be precise. Be inventive. Cite sources when making RF claims. Challenge assumptions.

---

## 🗂️ REPO STRUCTURE

```
sentinel/
├── CLAUDE.md               ← you are here (also see AGENTS.md)
├── README.md
├── docs/
│   ├── ARCHITECTURE_V2.md  ← detection-strategy rationale (read first)
│   ├── V2_MIGRATION_PLAN.md← phase-by-phase implementation plan
│   ├── rf-research/        ← frequency notes, drone emission profiles
│   ├── hardware/           ← antenna specs, SDR specs, mount diagrams
│   └── test-logs/          ← structured test results
├── src/
│   ├── sdr/                ← SDR capture, tuning, sample streaming, config loader
│   ├── dsp/                ← FFT primitives + DSP utilities
│   │   ├── spectrum.py     ← Welch PSD
│   │   ├── noise_floor.py  ← protected EMA noise-floor estimator
│   │   ├── cfar.py         ← CA-CFAR kernel
│   │   ├── spectrogram_buffer.py  ← rolling time-frequency buffer
│   │   ├── persistence.py  ← per-bin duty-cycle tracker
│   │   └── classifiers/    ← rule-based protocol IDs (elrs, ocusync, wifi)
│   ├── antenna/            ← pan/tilt controller (SCAN/CUE/BEARING_SEARCH/TRACK/ALARM)
│   ├── pipeline/           ← V2 staged DAG
│   │   ├── stage.py        ← Stage[InMsg, OutMsg] base class
│   │   ├── contracts.py    ← typed messages (IQ, Spectrogram, Burst, Candidate, ...)
│   │   ├── pipeline.py     ← Pipeline runner (assembles stages, drives antenna)
│   │   └── stages/         ← one file per stage (source, spectrogram, burst, ...)
│   └── ui/                 ← aiohttp WebSocket dashboard + live spectrum
├── tests/
│   ├── unit/               ← per-primitive and per-stage tests
│   ├── integration/        ← end-to-end Pipeline tests on synthetic IQ
│   └── field/              ← hardware/field test scripts and logs
├── data/
│   ├── samples/            ← raw IQ captures (.cf32 / .sigmf)
│   └── signatures/         ← known drone RF fingerprints
└── tools/                  ← bench harnesses, runner, dummy-drone TX
```

---

## ⚡ CODING RULES

1. **Language:** Python primary. C/C++ for perf-critical DSP if needed.
2. **SDR lib:** `UHD` Python API for USRP B210. `SoapySDR` as fallback.
3. **DSP:** `numpy` + `scipy.signal`. No reinventing FFT.
4. **Async:** Use `asyncio` for the capture/process pipeline. No blocking calls on the main thread.
5. **Config:** All hardware params (gain, sample rate, freq, scan limits) in `config.yaml`. Zero hardcoded values.
6. **Logging:** Structured JSON logs. Every detection event gets a timestamp, bearing, confidence score, and SNR.
7. **Tests:** Every algorithm gets a unit test with synthetic IQ data before field use.
8. **Commits:** Conventional commits. `feat:`, `fix:`, `dsp:`, `rf:`, `test:`, `docs:`.
9. **Branches:** `main` (stable, validated) ← `feature/*` or `experiment/*` via PR. No direct pushes to `main`. No `dev` branch.
10. **No magic numbers.** Name every constant. Comment the *why*, not the *what*.

---

## 📡 TECHNICAL CONTEXT

### Target Signals
| Protocol | Freq | Bandwidth | Notes |
|---|---|---|---|
| DJI OcuSync 2/3 | 2.4GHz | ~10MHz | FHSS, encrypted video |
| Wi-Fi (802.11) | 2.4GHz | 20/40MHz | Many hobby drones |
| ELRS | 2.4GHz | ~500kHz | Spread spectrum RC |
| FrSky | 2.4GHz | FHSS | Legacy RC |
| Spektrum DSM2/DSMX | 2.4GHz | FHSS | Legacy RC |

### SDR Hardware
- **Primary:** USRP B210 (dual RX, MIMO 30.72 MSPS/channel)
- **Interface:** UHD Python API
- **Center freq:** 2.437 GHz (WiFi Ch 6 — RemoteID default)
- **IQ format:** 32-bit float complex (numpy complex64)

### Antenna System
- **Type:** High-gain directional Yagi (cued) + omni dipole (always-on)
- **Axes:** Azimuth (pan); elevation fixed at +10° in v1
- **Control:** Stepper or servo via GPIO / serial
- **Scan modes:** `IDLE → SCAN → CUE → BEARING_SEARCH → ALARM → TRACK` (ARCHITECTURE_V2 §4.4)

---

## 🧠 ALGORITHM PIPELINE (V2)

The pipeline is a typed staged DAG. Each stage is one file in
`src/pipeline/stages/`, takes one typed input, returns one typed output.
The `Pipeline` runner threads them together and drives the antenna state
machine off the classifier output. Full rationale: `docs/ARCHITECTURE_V2.md`.

```
DualIQSource
    │  DualIQFrame
    ▼
Source ─► Spectrogram ─► Burst ─► Cluster ─► Classify ─┐
                                                       │ cue
                                                       ▼
                                                   Bearing ─► Fuse ─► Track
                                                   (cued     (joins   (frame-
                                                   Yagi      classif. to-frame
                                                   sweep)    + bearing) RFEvent
                                                                       grouping)
```

Per-stage responsibilities:

| Stage | Reads | Emits | Notes |
|---|---|---|---|
| `Source` | (none) | `DualIQFrame` | Wraps `DualIQSource`; one frame = `2 × fft_size` samples |
| `Spectrogram` | `DualIQFrame` | `DualSpectrogramFrame` | Welch PSD per role; appends to per-role `SpectrogramBuffer` |
| `Burst` | `SpectrogramFrame` (omni) | `tuple[Burst, ...]` | Protected-EMA noise floor + multi-frame run tracking |
| `Cluster` | `tuple[Burst, ...]` | `tuple[Candidate, ...]` | Phase 1: 1:1 wrap (multi-burst FHSS deferred) |
| `Classify` | `tuple[Candidate, ...]` | `tuple[Classification, ...]` | Rule-based (`dsp/classifiers/`); UNKNOWN_RF fallthrough |
| `Bearing` | `BearingRequest` (yagi) | `tuple[BearingEstimate, ...]` | Cued sweep — accumulates per-azimuth Yagi power until min_sweep_samples reached |
| `Fuse` | `FuseRequest` | `tuple[RFEvent, ...]` | Joins pending classifications to bearings by `candidate_id` |
| `Track` | `tuple[RFEvent, ...]` | `tuple[TrackedEmitter, ...]` | Frequency/time-gap matching; deterministic `trk-N` IDs |

---

## 🔬 RESEARCH TASKS (ongoing)

- [ ] Characterize DJI OcuSync 2 emission pattern (burst timing, FHSS hop rate)
- [ ] Build IQ signature library from controlled captures
- [ ] Evaluate CFAR variants (CA-CFAR vs OS-CFAR) for 2.4GHz clutter
- [ ] Antenna gain pattern calibration method
- [ ] AoA accuracy vs. distance modeling
- [ ] Multipath mitigation strategies for open-sky vs. urban

When researching: **cite papers, datasheets, or SDR community sources**. No speculation without flagging it.

---

## 🧪 TEST PROTOCOLS

### Unit Tests
- Input: synthetic IQ (`numpy` generated tones, FHSS bursts, noise)
- Assert: detection rate, false positive rate, timing accuracy

### Bench Tests
- Known transmitter at fixed distance/bearing
- Log: detected bearing vs. actual, SNR, latency

### Field Tests
- Document: date, location, weather, drone model, distance, flight path
- Capture raw IQ for every test. Always.
- Store in `data/samples/YYYY-MM-DD_<testname>.cf32`

### Pass Criteria
| Metric | Target |
|---|---|
| Detection range | ≥ 200m (open sky) |
| Bearing accuracy | ≤ ±5° at 100m |
| False positive rate | < 1 per 10 min |
| Detection latency | < 2 sec |

---

## 🚨 DECISION RULES

**When uncertain about hardware behavior:** prototype first, measure, then code.

**When adding a new algorithm:** benchmark against the previous one on the same IQ dataset before merging.

**When a field test fails:** capture logs + raw IQ, open an issue with `[FIELD FAIL]` tag, root cause before next test.

**When touching antenna control code:** simulate the full sweep range in software before running on hardware. Protect the hardware.

**When changing config defaults:** update `config.yaml`, update docs, bump version.

---

## 📋 SESSION STARTUP CHECKLIST

When beginning a new work session, Claude should:
1. Read `docs/V2_MIGRATION_PLAN.md` for current phase + next-up work
2. Check the active branch (`git status`) — V2 work lives on `feature/v2-pipeline`
3. List any open issues or blockers
4. Confirm hardware config hasn't changed
5. Run unit tests before any new feature work (`make test`)
6. Open a feature branch off `main` for new work; never push directly to `main`

---

## 🗣️ COMMUNICATION STYLE

- **Concise.** No padding.
- **Show code, not descriptions of code.**
- **If something is broken, say so directly.**
- **Flag RF assumptions explicitly** — propagation is weird, multipath is real.
- **Think out loud on novel algorithms** — reasoning matters here.
- **When stuck:** propose 2-3 approaches with tradeoffs. No wishy-washy hedging.

---

## 🏁 CURRENT PHASE

> Active branch: **`feature/v2-pipeline`** (PR #1, draft).
> `main` is V1 — deprecated, never validated. Don't build off it until V2 merges.

```
[PHASE 0: V2 SCAFFOLD]            ← done (commit e16f6bd)
  ✦ Stage[InMsg, OutMsg] protocol + base class
  ✦ V2 contract types (SpectrogramFrame, Burst, Candidate, ...)
  ✦ V2 config sections (dsp.spectrogram/burst/cluster/classifier)

[PHASE 1: V2 PIPELINE]            ← done (commit d2c4bf2)
  ✦ All 8 stages implemented
  ✦ Pipeline runner with antenna state machine
  ✦ Rule-based classifiers (ELRS, OcuSync, WiFi)
  ✦ V1 deleted; consumers migrated (UI server, bench tools, runner)

[PHASE 1.5: ARCHITECTURE GAP-CLOSE]  ← done (commit c78663a)
  ✦ Rolling SpectrogramBuffer per role
  ✦ Swept BearingStage (per-candidate sweep accumulator)
  ✦ BEARING_SEARCH + ALARM modes
  ✦ FuseStage holds pending classifications until matching bearing

[PHASE 2: HARDWARE VALIDATION]    ← YOU ARE HERE
  ○ ESP32 beacon bench run (CONTINUOUS / BURST / FHSS profiles)
  ○ HackRF dummy-drone TX bench run (DroneID / OcuSync / tone)
  ○ Capture IQ; file docs/test-logs/ entries
  ○ Tune dsp.bearing.min_sweep_samples and azimuth_bin_deg
  ○ Tune dsp.burst threshold / noise-floor window for real RF

[PHASE 3: ENRICHMENT]             (post-merge)
  ○ Multi-burst clustering for FHSS hop-train detection
  ○ RemoteID / DJI DroneID matched-filter decoders
  ○ Cyclostationary feature path
  ○ ML classifier on spectrogram patches (optional)
  ○ Real Yagi rotator + servo drivers
```

---

*SENTINEL — eyes on the sky, ears on the spectrum.*
