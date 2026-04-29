# Phase 1.5 - RF Validation and Calibration Checkpoint

Date: 2026-04-29
Status: Active next phase

## Sprint Goal

Turn SENTINEL from a working synthetic/SDR pipeline into a calibrated RF
measurement instrument before making strong drone-detection claims.

The system should first prove that it can measure the RF environment
repeatably: noise floor, channel agreement, antenna pattern, received power,
bearing stability, and false alarm behavior. Drone classification comes after
that evidence exists.

## Why This Phase Exists

The current architecture is a blind passive receiver. A drone controller has
major advantages SENTINEL does not have yet: protocol knowledge, expected
timing, synchronization, paired-link behavior, and a receiver designed for the
specific waveform. A high-gain Yagi helps link budget, but it does not solve
search, hop timing, clutter, calibration, or classification by itself.

This phase makes that limitation explicit. The near-term target is not
"identify DJI at range" from raw energy alone. The near-term target is:

1. Detect and log suspicious RF activity.
2. Measure whether the omni and Yagi channels agree.
3. Estimate bearing from calibrated Yagi power vs angle.
4. Preserve raw IQ and metadata so detections can be replayed and challenged.
5. Promote events from UNKNOWN_RF to likely drone only when multi-evidence
   behavior supports it.

Relevant internal references:

- [Drone emission research notes](rf-research/drone-emissions.md)
- [USRP B210 hardware notes](hardware/b210-notes.md)
- [Development guide](DEVELOPMENT.md)

## Current Position

| Area | Current State | Evidence / Notes |
|---|---|---|
| Phase 0 contracts | Complete | IQ, PSD, RF event, track, and verdict dataclasses exist. |
| Dual-RX foundation | Built | Synthetic dual source, USRP dual source, and dual pipeline engine are implemented. |
| Detector split | Built | Omni path uses TripwireDetector; Yagi path uses CFARDetector. |
| Event tracking | Built | Detector hits now convert into RFEvent objects and lightweight tracks. |
| Synthetic validation | Passing | `make test-quick` passed with 189 tests and 4 deselected on 2026-04-29. |
| Synthetic dual smoke test | Passing | `tools.sentinel_runner --dual --frames 3 --headless` produced detections. |
| Live dual-RX validation | Not started | No connected B210 hardware test was run in this phase. |
| B210 dual-center behavior | Unvalidated | Live dual mode warns if RX-A and RX-B centers differ. Use shared center until measured. |
| Antenna pattern calibration | Not started | No measured Yagi RSSI/SNR vs azimuth table exists yet. |
| RF survey mode | Not built | Need a tool to map occupied 2.4 GHz spectrum and local clutter. |
| HackRF bench environment | Built, not hardware-run | `tools.hackrf_bench` pairs with `tools.hackrf_tx` for tone, continuous OFDM, and burst tests. |
| Replay-first artifacts | Partial | Synthetic tests exist; live IQ + metadata capture/replay is not yet integrated into the pipeline. |
| Drone classifier | Deferred | Classification should wait until calibration and replay data exist. |

## Phase 1.5 Approach

### 1. Bench-validate dual RX

Feed the same known 2.4 GHz source into both B210 channels through safe
attenuation. Confirm sample shape, timing, center frequency, PSD agreement,
relative power offset, gain response, and overrun behavior.

Output artifact:

- `docs/test-logs/YYYY-MM-DD_dual-rx-bench.md`
- Raw IQ capture plus metadata when hardware is available

Acceptance gate:

- Both channels produce stable PSDs with a measured relative offset.
- No overruns at the configured sample rate.
- Center-frequency behavior is documented for the exact B210 mode used.

### 2. Calibrate RF front end and antenna pattern

Use a known transmitter at fixed distance and bearing. Sweep the Yagi through
azimuth angles and log RSSI/SNR at each angle. Repeat at multiple gains.

Output artifact:

- Yagi azimuth response table
- Noise floor table by gain setting
- Safe operating gain/attenuation recommendation

Acceptance gate:

- Main lobe and side-lobe behavior are measured.
- Bearing confidence can be tied to SNR and antenna angle.
- Front-end input protection plan is confirmed before field operation.

### 3. Build replay-first capture artifacts

Every live test should preserve enough information to rerun detection later:
raw IQ, config snapshot, antenna angle, detector outputs, and operator notes.

Output artifact:

- `.cf32` or SigMF-compatible IQ capture
- JSON metadata sidecar
- Structured detection log

Acceptance gate:

- A saved capture can be replayed through the same detector path used live.
- Results are deterministic enough to compare algorithm changes.

### 4. Add RF survey mode

Before looking for drones, map the local 2.4 GHz environment. Log occupied
channels, burst activity, noise floor, persistent emitters, and likely clutter
bearings.

Output artifact:

- Survey report with PSD summaries and event counts
- Known-clutter bearings for exclusion masks

Acceptance gate:

- The system can distinguish quiet, persistent Wi-Fi-like, bursty, and
  wideband activity as UNKNOWN_RF classes without claiming drone identity.

### 5. Enter Phase 2 detection only after evidence gates pass

Phase 2 should add burst/FHSS features, classifier rules, and confidence
scoring only after the Phase 1.5 measurements exist.

Minimum Phase 2 input data:

- Calibrated dual-RX channel offsets
- Antenna pattern table
- At least one local RF survey
- At least one controlled transmitter capture
- Replay harness for saved IQ

## Immediate Build Order

1. Run the HackRF bench environment in conducted mode and save the first report.
2. Add a dedicated dual-RX channel calibration tool if the HackRF bench report
   shows channel offset or timing questions that need deeper measurement.
3. Add a reusable capture artifact writer for IQ + JSON metadata.
4. Add RF survey mode using existing PSD and detector code.
5. Add tests for metadata schema and replay determinism.
6. Run hardware bench validation and record the first test log.

## Open Blockers

- Confirm exact antenna model and mounted polarization.
- Confirm whether limiter/attenuator hardware is available before using the
  Yagi + LNA path near strong emitters.
- Confirm B210 hardware, power supply, USB 3.0 host, and UHD version.
- Choose a controlled transmitter for calibration that is legal and repeatable.
