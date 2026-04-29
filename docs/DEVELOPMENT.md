# SENTINEL — Development Guide

How the code fits together, and how to extend it.

## Module Map

```
src/
├── sdr/
│   ├── config.py       YAML → frozen dataclasses (SentinelConfig)
│   └── capture.py      IQSource ABC → SyntheticSource, USRPSource
│
├── dsp/
│   ├── spectrum.py     Welch PSD: IQ → (freq_hz[], power_dbm[])
│   ├── detector.py     TripwireDetector, CFARDetector → Detection[]
│   └── events.py       Detection → RFEvent conversion + lightweight tracking
│
├── antenna/
│   └── controller.py   AntennaController ABC → SimulatedController
│                        ScanMode: IDLE → SCAN → CUE → TRACK
│
├── pipeline/
│   ├── contracts.py    Shared IQ, PSD, RF event, track, and verdict contracts
│   └── engine.py       PipelineEngine: wires everything into async loop
│
└── ui/
    └── spectrum.py     Live matplotlib PSD display
```

## Current Path

As of 2026-04-29, SENTINEL is moving from Phase 1 foundation into
Phase 1.5 RF validation. The code can run a synthetic dual-RX pipeline, but
live dual-channel hardware behavior, antenna pattern, and local RF clutter have
not been measured yet.

Current decision: do not treat raw 2.4 GHz energy as a drone answer key. The
system should first produce repeatable RF evidence: channel agreement, noise
floor, calibrated Yagi response, saved IQ, replayable detections, and local
survey logs. The detailed checkpoint is tracked in
[phase-1-5-rf-validation.md](phase-1-5-rf-validation.md).

## Data Flow

The Phase 0 upgrade path is contract-first.  Later dual-RX and classifier work
should exchange these objects instead of raw tuples:

```
IQChannelFrame
    One channel of complex64 IQ plus role, channel index, timing, RF tuning,
    and antenna pointing context.

DualIQFrame
    Paired RX-A/RX-B frame. RX-A is the omni tripwire channel, RX-B is the
    Yagi channel.

PSDFrame
    One channel spectrum with frequency axis, dBm/bin power, timing, and
    antenna context.

RFEvent
    A time-frequency object built from detector hits. This is where burst
    cadence, duty cycle, hop rate, persistence, and bearing evidence attach.

TrackedEmitter
    A sequence of RFEvent objects believed to come from one emitter.

DetectionVerdict
    Final multi-evidence label: DRONE_CONFIRMED, DRONE_LIKELY, UNKNOWN_RF,
    or CLUTTER.
```

Target dual-channel flow:

```
USRP B210 MIMO
    │
    ▼
DualIQFrame
    ├── RX-A omni IQ  → PSDFrame → TripwireDetector
    └── RX-B yagi IQ  → PSDFrame → CFARDetector → RFEvent tracker
                                                   │
                         antenna azimuth/elevation ┘
    ▼
TrackedEmitter → DetectionVerdict → log/UI/capture
```

Initial RF safety assumptions for live use:

- RX-A and RX-B should share one center frequency until B210 tuning behavior is
  validated for the specific hardware mode.
- The high-gain Yagi path needs a limiter or switchable attenuation before
  field operation near unknown 2.4 GHz emitters.
- Raw IQ capture should stay enabled for suspicious events so UNKNOWN_RF can be
  reviewed and turned into labeled test data.

## Current Data Flow

Every processing frame follows this path:

```
IQSource.read(num_samples)
    │
    ▼
remove_dc_offset(iq)              # src/dsp/spectrum.py
    │
    ▼
compute_psd(iq) → freq_hz, psd_dbm   # Welch method, returns dBm per bin
    │
    ├──→ TripwireDetector.process(psd_dbm, freq_hz) → Detection[]
    │       Adaptive EMA noise floor, duration gating
    │       Omni channel: "is something there?"
    │
    └──→ CFARDetector.process(psd_dbm, freq_hz) → Detection[]
            CA-CFAR with guard cells, min bandwidth filter
            Yagi channel: precision detection
    │
    ▼
_deduplicate(all_detections)      # keep highest SNR for overlapping bins
    │
    ▼
_update_antenna(detections, dt)   # SCAN→CUE→TRACK state transitions
    │
    ▼
_log_detection(d)                 # structured JSON to logger
```

This loop runs in `PipelineEngine.process_one_frame()` (`src/pipeline/engine.py:129`).

## Key Abstractions

### IQSource (`src/sdr/capture.py`)

Abstract base with three methods: `start()`, `stop()`, `read(num_samples) → complex64[]`.

| Implementation | Use |
|---|---|
| `SyntheticSource` | Testing. Generates noise + configurable tones/wideband signals. Phase-continuous, seedable. |
| `USRPSource` | Hardware. Wraps UHD `recv()` in `asyncio.to_thread()` for non-blocking reads. |

To add a new SDR backend (HackRF, RTL-SDR): subclass `IQSource`, implement the three methods.

### Detectors (`src/dsp/detector.py`)

Both detectors consume `(psd_dbm, freq_hz)` arrays and return `list[Detection]`.

**TripwireDetector** — energy threshold with protected noise floor:
- Per-bin EMA noise floor that only updates "quiet" bins (bins below threshold)
- This prevents persistent signals from raising their own noise estimate
- Duration gating: signal must persist N consecutive frames before reporting
- Designed for the omni channel

**CFARDetector** — Cell-Averaging Constant False Alarm Rate:
- Compares each bin against the mean of surrounding reference cells
- Guard cells prevent signal leakage into the noise estimate
- Min bandwidth filter rejects single-bin spikes
- Stateless per frame (no history needed)
- Designed for the yagi channel

Both return the same `Detection` dataclass: `freq_hz`, `bandwidth_hz`, `power_dbm`, `snr_db`, `bin_start`, `bin_end`.

### AntennaController (`src/antenna/controller.py`)

Abstract base with: `start()`, `stop()`, `get_state()`, `set_mode()`, `cue_to()`, `tick(dt)`.

`SimulatedController` models a pan-only mount with slew rate limiting:

```
IDLE ──(start)─────→ SCAN (sweep back and forth)
SCAN ──(detection)──→ CUE  (slew to bearing, timeout → SCAN)
CUE  ──(detection)──→ TRACK (oscillate ±15° around bearing)
TRACK ──(no signal)─→ SCAN  (lost timeout)
```

To add real hardware control: subclass `AntennaController`, implement GPIO/serial commands in `tick()`.

### Config (`src/sdr/config.py`)

`load_config()` reads `config.yaml` → returns `SentinelConfig` (frozen dataclass tree). All fields are immutable after creation. Numeric values are explicitly cast to handle PyYAML's scientific notation quirks.

Config auto-discovers `config.yaml` by walking up from the source file to find the project root.

## How To: Add a New Detector

1. **Define the detector** in `src/dsp/detector.py`:

```python
@dataclass
class MyDetector:
    """Docstring explaining the detection strategy."""

    # Config params
    threshold_db: float = 10.0
    sample_rate_hz: float = 30.72e6
    center_freq_hz: float = 2.437e9
    fft_size: int = 2048

    def process(self, psd_dbm: np.ndarray, freq_hz: np.ndarray) -> list[Detection]:
        # Your detection logic here
        # Return list of Detection objects
        ...
```

2. **Wire it into the pipeline** in `src/pipeline/engine.py`:
   - Initialize in `start()` alongside `_tripwire` and `_cfar`
   - Call `.process()` in `process_one_frame()` and extend `all_detections`

3. **Write unit tests** in `tests/unit/test_detector.py`:
   - Use `make_noise_psd()` and `inject_tone()` helpers (already defined)
   - Test: noise immunity, strong signal detection, weak signal rejection, edge cases

4. **Add config** if needed:
   - Add a frozen dataclass in `src/sdr/config.py`
   - Add the section to `config.yaml`
   - Wire parsing in `load_config()`

## How To: Add an IQ Source

1. Subclass `IQSource` in `src/sdr/capture.py`
2. Implement `start()`, `stop()`, `read(num_samples) → np.ndarray` (complex64)
3. For blocking hardware reads, wrap in `asyncio.to_thread()` (see `USRPSource._read_sync`)
4. Use it in `tools/sentinel_runner.py` or pass to `PipelineEngine`

## How To: Modify Config

1. Add the YAML key to `config.yaml` with a comment explaining the value
2. Add a frozen dataclass field in `src/sdr/config.py`
3. Parse it in `load_config()` with explicit type casting
4. Update `tests/unit/test_config.py` to verify the new field loads correctly

## Conventions

- **Async everywhere**: Pipeline is `asyncio`. Hardware reads use `to_thread()`.
- **No hardcoded values**: Everything in `config.yaml`. Name every constant.
- **Frozen configs**: `@dataclass(frozen=True)`. No runtime mutation.
- **Structured logging**: JSON to `sentinel.*` loggers. Every detection event logged.
- **Conventional commits**: `feat:`, `fix:`, `dsp:`, `rf:`, `test:`, `docs:`
