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
├── CLAUDE.md               ← you are here
├── README.md
├── docs/
│   ├── rf-research/        ← frequency notes, drone emission profiles
│   ├── hardware/           ← antenna specs, SDR specs, mount diagrams
│   └── test-logs/          ← structured test results
├── src/
│   ├── sdr/                ← SDR capture, tuning, sample streaming
│   ├── dsp/                ← filters, FFT, detection algorithms
│   ├── antenna/            ← pan/tilt control, scan patterns
│   ├── pipeline/           ← async detection engine
│   └── ui/                 ← live display / dashboard
├── tests/
│   ├── unit/
│   ├── integration/
│   └── field/              ← field test scripts & data
├── data/
│   ├── samples/            ← raw IQ captures (.cf32 / .sigmf)
│   └── signatures/         ← known drone RF fingerprints
└── tools/                  ← calibration, replay, analysis utilities
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
9. **Branches:** `main` (stable) → `dev` (integration) → `feature/*` or `experiment/*`.
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
- **Type:** High-gain directional (Yagi or patch)
- **Axes:** Azimuth (pan) + Elevation (tilt)
- **Control:** Stepper or servo via GPIO / serial
- **Scan modes:** `SWEEP` (raster), `TRACK` (locked on signal), `IDLE`

---

## 🧠 ALGORITHM PIPELINE

```
IQ Samples
    │
    ▼
[DC Offset Removal]
    │
    ▼
[Bandpass Filter] → 2.4–2.5GHz passband
    │
    ▼
[FFT / PSD Estimation] → Welch method, 1024–4096 pts
    │
    ▼
[Threshold Detection] → Adaptive noise floor (CFAR)
    │
    ▼
[Feature Extraction] → BW, center freq, burst pattern, FHSS signature
    │
    ▼
[Classifier] → Rule-based first; ML fingerprinting later
    │
    ▼
[Bearing Estimator] → Peak RSSI per antenna position → AoA
    │
    ▼
[Target Tracker] → Kalman filter on bearing + elevation
    │
    ▼
[Alert / Display]
```

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
1. State current sprint goal
2. List any open issues or blockers
3. Confirm hardware config hasn't changed
4. Run unit tests before any new feature work
5. Pull latest `dev` before branching

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

```
[PHASE 1: FOUNDATION]  ← YOU ARE HERE
  ✦ SDR capture pipeline (SyntheticSource + USRPSource)
  ✦ Basic FFT detection (Welch PSD)
  ✦ CFAR detection (CA-CFAR on yagi channel)
  ✦ Antenna control scaffold (SimulatedController)
  ✦ Config system (frozen dataclasses)
  ○ RemoteID decoder integration
  ○ DJI DroneID decoder integration
  ○ Radar app ↔ pipeline WebSocket

[PHASE 2: DETECTION]
  ○ CFAR field tuning
  ○ FHSS classifier
  ○ Bearing estimation (AoA)

[PHASE 3: TRACKING]
  ○ Kalman tracker
  ○ Scan → Track handoff
  ○ Multi-target handling

[PHASE 4: FIELD OPS]
  ○ Field-hardened UI
  ○ Alert system
  ○ Signature library
```

---

*SENTINEL — eyes on the sky, ears on the spectrum.*
