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
│   └── detector.py     TripwireDetector, CFARDetector → Detection[]
│
├── antenna/
│   └── controller.py   AntennaController ABC → SimulatedController
│                        ScanMode: IDLE → SCAN → CUE → TRACK
│
├── pipeline/
│   └── engine.py       PipelineEngine: wires everything into async loop
│
└── ui/
    └── spectrum.py     Live matplotlib PSD display
```

## Data Flow

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
