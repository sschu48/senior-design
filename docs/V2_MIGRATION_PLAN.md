# SENTINEL V2 Migration Plan

> **Status:** Approved 2026-05-03 — hard rewrite, no parity preservation.
> **Branch:** `feature/v2-pipeline`
> **Companion docs:** `docs/ARCHITECTURE_V2.md` (the why), `CLAUDE.md` (the rules), this doc (the how).
>
> **Why no parity:** The V1 pipeline never produced reliable detections in field testing. There is nothing to match against. Algorithmic primitives (protected noise floor, CA-CFAR averaging, deterministic track IDs, duration gating) are kept as **design invariants**, not as behavioral targets.

---

## 1. Goals & non-goals

### Goals

1. Replace the current parallel-detector layout (Tripwire on omni + CFAR on Yagi, ad-hoc bearing) with an explicit **typed, staged DAG**.
2. Give each stage a **single responsibility** with a typed input and typed output, so the pipeline is readable as code and visualizable as a live diagram.
3. Make every stage **independently testable** with synthetic IQ or fixture inputs.
4. **Delete** the duplicated/parallel detection paths rather than keep them alongside V2 ("no rats nest").
5. Land V2 as a clean rewrite — no behavioral parity required against V1, since V1 was never validated.

### Non-goals (for this branch)

- New SDR hardware — still B210, MIMO 30.72 MHz/channel.
- ML classifiers — Stage 2 starts rule-based; ML is a later swap-in at the same interface.
- Matched filtering for ELRS preamble — optional Stage 2 path, deferred.
- 900 MHz coverage — open question in `ARCHITECTURE_V2.md` §9.

---

## 2. Target architecture

### 2.1 The DAG

```
                           ┌──────────┐
                           │  Source  │  DualIQSource (omni + yagi)
                           └────┬─────┘
                                │ DualIQFrame
                                ▼
                           ┌──────────┐
                           │ Spectro  │  rolling time-frequency buffer
                           └────┬─────┘
                                │ SpectrogramFrame (omni + yagi PSD + history)
                                ▼
                           ┌──────────┐
                           │  Stage1  │  burst extractor (time-frequency objects)
                           │ (omni)   │  adaptive noise floor + persistence
                           └────┬─────┘
                                │ tuple[Burst]
                                ▼
                           ┌──────────┐
                           │ Cluster  │  group bursts → Candidate emissions
                           └────┬─────┘
                                │ tuple[Candidate]
                                ▼
                           ┌──────────┐
                           │  Stage2  │  rule-based classifier
                           │          │  (ELRS / OcuSync / WiFi / unknown)
                           └────┬─────┘
                                │ tuple[Classification]
                                │  ── cue ──▶ AntennaController state machine
                                ▼
                           ┌──────────┐
                           │  Stage3  │  cued bearing estimator (yagi)
                           └────┬─────┘
                                │ tuple[BearingEstimate]
                                ▼
                           ┌──────────┐
                           │  Fuse    │  Classification + BearingEstimate → Event
                           └────┬─────┘
                                │ tuple[DetectionEvent]
                                ▼
                           ┌──────────┐
                           │  Track   │  frame-to-frame association (RFEventTracker)
                           └────┬─────┘
                                │ tuple[TrackedEmitter]
                                ▼
                       sinks: log + dashboard + db
```

### 2.2 Stage abstraction

```python
# src/pipeline/stage.py  (new)
class Stage(Protocol[InMsg, OutMsg]):
    name: str
    async def process(self, msg: InMsg) -> OutMsg: ...
    def reset(self) -> None: ...
```

- All stages async, all stateful state lives on the stage instance.
- Pipeline = ordered list of stages, assembled in **one** place from `config.yaml`.
- Each stage emits to a single output topic; the dashboard subscribes to whatever topic it wants to render. This is how visualization comes for free.

### 2.3 Message types (mostly already exist)

| Message | Status | Source |
|---|---|---|
| `DualIQFrame` | ✅ exists | `contracts.py` |
| `PSDFrame` | ✅ exists | `contracts.py` |
| `SpectrogramFrame` | 🆕 add | rolling buffer of `PSDFrame` per role |
| `Burst` | 🆕 add | `(role, t0, t1, f_lo, f_hi, peak_dbm, snr)` — replaces `Detection` |
| `Candidate` | 🆕 add | clustered bursts: `(role, t0, t1, f_center, bw, bursts, hop_rate?)` |
| `Classification` | 🆕 add | wraps `DetectionVerdict` + protocol guess + features |
| `BearingEstimate` | 🆕 add | `(candidate_id, bearing_deg, confidence, sweep_data)` |
| `DetectionEvent` | ≈ `RFEvent` | `contracts.py` — extend if needed |
| `TrackedEmitter` | ✅ exists | `contracts.py` |

**Decision:** `Burst` replaces the current `Detection` dataclass. `Detection` is per-frame and per-detector; `Burst` is a time-frequency object spanning frames. This is the central V2 shift.

---

## 3. Mapping: current → V2

| Current | V2 | Action |
|---|---|---|
| `PipelineEngine` (single channel) | — | **Delete.** Tests cover only the dual path that matters. |
| `DualPipelineEngine` | `Pipeline` (assembles stages) | **Rewrite** as a stage runner; behavior moves into stages. |
| `TripwireDetector` (omni energy) | folded into Stage 1 burst extractor | **Delete** the dataclass; reuse the protected-noise-floor logic inside Stage 1. |
| `CFARDetector` (yagi CFAR) | folded into Stage 1 (omni) for primary detection; CFAR repurposed inside Stage 3 for cued bearing | **Move** the algorithm; **delete** the standalone class. |
| `Detection` dataclass | `Burst` | **Rename** + extend with `(t0, t1)` time bounds. |
| `deduplicate()` | inside `Cluster` stage | **Move.** |
| `create_detectors()` factory | `build_pipeline(config)` | **Replace.** |
| `RFEventTracker` | `Track` stage | **Wrap** — code is fine, becomes a stage. |
| `PersistenceDetector` | feature inside Stage 1 | **Wrap** — used as a feature provider, not a top-level detector. |
| `detection_to_event()` | `Fuse` stage | **Move.** |
| Antenna nudge logic in engine | `Stage2.cue()` → `AntennaController` | **Extract** into the cue path; engine no longer touches antenna directly. |

### What gets deleted on this branch

- `PipelineEngine` (single-channel) — never used in production, deleted outright
- `DualPipelineEngine` — replaced by the new `Pipeline` runner
- `TripwireDetector`, `CFARDetector` as exported classes — logic *concepts* preserved inside stages, but rewritten on first principles
- `Detection` dataclass — replaced by `Burst` (time-bounded, frame-spanning)
- `create_detectors()`, `deduplicate()` — replaced by stage-local equivalents
- The ad-hoc `_update_antenna()` block in the engine — antenna control moves into the cue path off `ClassifyStage`
- `test_detector.py` (468 lines), `test_pipeline.py` (356 lines) — replaced by per-stage tests against synthetic fixtures

**No honest cost — V1 didn't work, so there's nothing to lose.** The replacement tests verify V2 against synthetic IQ from `src/sdr/signals.py` and (Phase 3) against ESP32 beacon + HackRF dummy-drone TX.

---

## 4. Target file layout

```
src/
├── pipeline/
│   ├── __init__.py
│   ├── contracts.py        # msg types (extend existing)
│   ├── stage.py            # 🆕 Stage protocol + base class
│   ├── pipeline.py         # 🆕 Pipeline runner (replaces engine.py)
│   └── stages/             # 🆕 one file per stage
│       ├── __init__.py
│       ├── source.py       # wraps DualIQSource
│       ├── spectrogram.py
│       ├── burst.py        # Stage 1
│       ├── cluster.py
│       ├── classify.py     # Stage 2 (rules)
│       ├── bearing.py      # Stage 3 (cued)
│       ├── fuse.py
│       └── track.py
├── dsp/
│   ├── spectrum.py         # keep — pure FFT helpers
│   ├── noise_floor.py      # 🆕 protected EMA noise-floor (extracted from Tripwire)
│   ├── cfar.py             # 🆕 CA-CFAR kernel (extracted from CFARDetector)
│   ├── persistence.py      # keep
│   └── classifiers/        # 🆕 rule-based classifiers per protocol
│       ├── elrs.py
│       ├── ocusync.py
│       └── wifi.py
└── ...
```

**Principle:** `dsp/` holds pure functions and stateful primitives. `pipeline/stages/` holds the orchestration that wires those primitives into the DAG. No DSP code calls into `pipeline/`; the dependency points one way.

---

## 5. Migration phases

The branch lives in three commit-sized phases. Old code stays in place only as long as it takes for the new pipeline to compile and pass its own tests; it gets deleted at the end of Phase 1.

### Phase 0 — Scaffold
- Add `Stage` protocol + base class in `src/pipeline/stage.py`.
- Add new message types (`SpectrogramFrame`, `Burst`, `Candidate`, `Classification`, `BearingEstimate`) in `contracts.py`.
- Add empty `src/pipeline/stages/` package and empty `src/dsp/classifiers/` package.
- Add V2 spectrogram + classifier sections to `config.yaml`.
- **Exit criteria:** new types and stage protocol import cleanly; `pytest` green on the existing suite (untouched).

### Phase 1 — Build all stages and delete V1
Single phase, single PR. We're not preserving V1, so there's no reason to spread this out:

- Implement: `SourceStage`, `SpectrogramStage`, `BurstStage`, `ClusterStage`, `ClassifyStage` (rule-based: ELRS / OcuSync / WiFi / unknown), `BearingStage` (cued, reuses CA-CFAR kernel), `FuseStage`, `TrackStage`.
- New `Pipeline` runner in `src/pipeline/pipeline.py` assembled from `config.yaml`.
- Per-stage tests under `tests/unit/test_stage_*.py`. Integration test under `tests/integration/test_pipeline_synthetic.py` against `src/sdr/signals.py` fixtures.
- **Delete** `engine.py`, `detector.py`, `events.py` (logic moves into `track.py` stage), `test_pipeline.py`, `test_detector.py`, `test_events.py`.
- Update `tools/sentinel_runner.py`, `tools/bench_test.py`, `tools/bench_snr_sweep.py`, `src/ui/server.py` to construct/consume the new `Pipeline`.
- **Exit criteria:** `pytest` green; dashboard renders against synthetic source; ELRS/OcuSync/WiFi synthetic fixtures produce labeled `DetectionEvent`s with bearings.

### Phase 1.5 — Close architecture gaps to ARCHITECTURE_V2 doc (✅ landed 2026-05-03)
- `SpectrogramBuffer` primitive + per-role rolling buffers in `SpectrogramStage` (§4.1)
- Swept `BearingStage` — accumulates per-azimuth Yagi power across frames, emits when ≥`min_sweep_samples` collected, peak-finds by azimuth bucket (§4.3 Stage 3)
- `BEARING_SEARCH` and `ALARM` modes added to `SimulatedController`; full state machine matches §4.4
- `FuseStage` holds pending classifications until matching bearing arrives — `RFEvent` is now the operational ALARM event
- New config: `dsp.bearing.{min_sweep_samples, azimuth_bin_deg}`, `scan.alarm_duration_sec`

### Phase 2 — Hardware validation
- Run `tools/bench_test.py` against ESP32 beacon (CONTINUOUS, BURST, FHSS profiles) and HackRF dummy-drone TX.
- Capture raw IQ for every test, file under `data/samples/YYYY-MM-DD_v2_*.cf32`.
- File field-test log under `docs/test-logs/`.
- **Exit criteria:** documented detection rate per profile + SNR table. Failures get root-caused before merge to `main`.

---

## 6. Visualization & testability

### Visualization (free, by construction)

- Each stage exposes its output via an async fan-out (one `asyncio.Queue` per subscriber).
- Dashboard server subscribes to whichever stages it wants to render: `spectrogram` for waterfall, `bursts` for highlighted boxes, `candidates` for cluster overlays, `classifications` for labels, `bearings` for the radar plot.
- Adding a new visualization = subscribing to an existing topic. No invasive surgery in the pipeline.

### Testability

- **Unit:** each stage gets a `tests/unit/test_stage_<name>.py`. Inputs are fixture messages; outputs are asserted as dataclasses. No mocking of internals.
- **Integration:** `tests/integration/test_pipeline_synthetic.py` runs the whole DAG against synthetic IQ from `src/sdr/signals.py` (already centralized — good).
- **Replay fixtures:** drop a `.cf32` capture into `data/samples/`, point the source stage at it, assert events. This becomes our regression suite for hardware behavior.
- **Stage-level parity check:** for every existing test in `test_detector.py` / `test_pipeline.py` we delete, we add an equivalent stage test asserting the same boundary condition.

---

## 7. Design invariants (verified in V2 stage tests)

These are **first-principles design properties** — good ideas extracted from V1 DSP that we still want, but verified directly against synthetic inputs in V2 tests, not against V1 output.

- [ ] **Protected noise-floor estimator** (`BurstStage`): per-bin EMA only updates on "quiet" bins so persistent emitters can't train themselves into the floor. Test: feed a constant tone in 1 bin, assert that bin's noise-floor estimate stays low after N frames.
- [ ] **Duration gating** (`BurstStage`): single-frame spikes do not become bursts. Test: feed 1-frame impulse noise, assert no `Burst` emitted.
- [ ] **CA-CFAR with guard cells, linear-domain averaging** (`BearingStage`): sliding window in linear power, guard cells bracket the CUT. Test: synthetic narrowband signal next to wideband noise; CFAR detects narrowband but not noise-floor.
- [ ] **Minimum-bandwidth filter** (`ClusterStage`): reject candidates narrower than `min_detection_bw_hz`. Test: feed sub-100 kHz tone, assert no `Candidate`.
- [ ] **Overlap-merge clustering** (`ClusterStage`): bursts overlapping in time-frequency become one `Candidate`. Test: two bursts on adjacent bins → 1 candidate.
- [ ] **Deterministic track IDs** (`TrackStage`): `trk-1`, `trk-2`, ... assigned in arrival order. Test: process N events, assert IDs match expected sequence.
- [ ] **Frequency + time gap matching** (`TrackStage`): events too far apart in freq or time start a new track. Test: two events with gap > threshold → two tracks.
- [ ] **Antenna state machine** (driven by `ClassifyStage` → `AntennaController`): `SCAN → CUE → TRACK` transitions with configured timeouts. Test: drive with mocked classifier output, assert state sequence and timeouts.
- [ ] **Structured JSON detection log** (`FuseStage` sink): every event gets timestamp, freq, bw, snr, bearing (if cued), classification, confidence, frame index. Test: capture log line, assert schema.
- [ ] **Dashboard WebSocket schema versioning** (`src/ui/server.py`): if message shape changes from V1, bump schema version and update dashboard in same PR.

---

## 8. Open questions to resolve before Phase 1

1. **Spectrogram buffer length.** ELRS bursts are <10 ms, FFT cadence is `sample_rate / fft_size` ≈ 3.75 kHz frame rate at 30.72 MSps / 8192 — so ~37 frames per 10 ms. A 1 s buffer = ~3700 frames × 8192 bins = 240 MB at float32. Options: shrink FFT, shrink history, store as float16. **Recommend:** start with 250 ms history at fft_size 4096 → ~30 MB. Revisit.
2. **Burst extractor algorithm.** Per-bin EMA threshold (current Tripwire) → contiguous-bin grouping in time-frequency, or 2D connected components on a thresholded spectrogram? **Recommend:** start with 1D-per-frame (matches current behavior) and stitch across frames in `ClusterStage`. 2D CC is a Phase 2 enhancement.
3. **Classifier seed rules.** What's the minimum rule set? **Recommend:** WiFi (20 MHz, continuous, beacon timing), OcuSync (10 MHz, ~10 ms duty), ELRS (~800 kHz, <10 ms bursts, hop pattern). Everything else → `UNKNOWN_RF`.
4. **Cued vs. continuous Yagi.** During TRACK, does the Yagi keep running CFAR on its own, or only when classifier says "still here"? **Recommend:** Yagi continuous during TRACK, idle otherwise. Re-evaluates if compute is tight.
5. **Where does `PersistenceDetector` plug in?** It's a generic feature, not a stage. **Recommend:** inject it into `BurstStage` as a feature provider; expose persistence per burst on the `Burst` message.

---

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| New pipeline has detection blind spots we don't catch in synthetic tests | Phase 2 hardware validation against ESP32 + HackRF before merge to `main`. Capture IQ for every failure. |
| Memory blowup from spectrogram buffer | Sized in §8.1; profile in Phase 1, configurable in `config.yaml`. |
| Dashboard breaks during cutover | Dashboard update lives in the same Phase 1 PR — no period where pipeline and dashboard are out of sync. |
| Scope creep into ML / matched filters | Explicit non-goals in §1; revisit only after V2 lands and is field-validated. |
| Stage abstraction over-engineered for the size of this codebase | Keep stages as plain async classes with a `process()` method — no DI framework, no event bus library, no plugin registry. Pipeline assembly is one explicit file. |

---

## 10. Decisions

All resolved 2026-05-03:

1. ✅ **Delete `PipelineEngine` outright** — never exercised in production.
2. ✅ **File layout per §4** — `pipeline/stages/` per-stage, `dsp/classifiers/` for rule files.
3. ✅ **Hard rewrite, not parity-first** — V1 was never validated, so there's no behavior to preserve. Algorithmic ideas kept as design invariants (§7), not as bit-match targets.
4. ✅ **Spectrogram default: 250 ms × 4096 bins**, configurable in `config.yaml`.
5. ✅ **§7 reframed as design invariants** — extend during Phase 1 as we discover more.

Next: Phase 0 scaffolding PR.
