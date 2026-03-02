# SENTINEL — Testing Guide

## Running Tests

```bash
make test              # full suite
make test-quick        # skip slow, field, and hardware markers
make test-hardware     # USRP B210 tests only (requires connected hardware)
```

Or directly via pytest:

```bash
.venv/bin/pytest -v                              # all tests
.venv/bin/pytest tests/unit/test_detector.py -v  # single module
.venv/bin/pytest -k "test_detects_strong_tone"   # single test by name
```

## Test Structure

```
tests/
└── unit/
    ├── test_config.py       Config loading, type correctness, frozen immutability
    ├── test_synthetic.py    SyntheticSource output: dtype, shape, noise power, tone detection
    ├── test_spectrum.py     PSD computation: shape, freq range, tone recovery (±3 dB), DC removal
    ├── test_detector.py     TripwireDetector + CFARDetector (most comprehensive — 469 lines)
    ├── test_antenna.py      SimulatedController: all 4 modes, slew rate, timeouts
    ├── test_pipeline.py     Full pipeline: lifecycle, detection, antenna transitions
    └── test_usrp.py         USRP B210 hardware validation (skipped without hardware)
```

## Test Markers

Tests can be tagged with pytest markers. Use `-m` to filter:

| Marker | Meaning | When to use |
|---|---|---|
| `hardware` | Requires USRP B210 connected | `make test-hardware` |
| `slow` | Takes >5 seconds | Skipped by `make test-quick` |
| `field` | Field test script | Skipped by `make test-quick` |

Mark a test:

```python
@pytest.mark.hardware
def test_usrp_reads_samples():
    ...
```

## Synthetic Signal Fixtures

All unit tests use **synthetic IQ data** — no hardware required. Two levels:

### PSD-Level Helpers (for detector tests)

Defined at the top of `test_detector.py`:

```python
SAMPLE_RATE = 30.72e6
FFT_SIZE = 2048
CENTER_FREQ = 2.437e9
BIN_WIDTH = SAMPLE_RATE / FFT_SIZE   # ~15 kHz per bin

def make_freq_axis():
    """Frequency axis centered on 2.437 GHz, 2048 bins."""

def make_noise_psd(power_dbm=-90.0, seed=0):
    """Flat noise floor with small random variation."""

def inject_tone(psd, bin_idx, power_dbm, width_bins=3):
    """Inject a peak at a specific bin index."""
```

These operate directly on PSD arrays — fast, no IQ generation needed. Use these when testing detector logic.

### IQ-Level Source (for pipeline/spectrum tests)

```python
from src.sdr.capture import SyntheticSource, SignalDef

source = SyntheticSource(
    sample_rate_hz=30.72e6,
    noise_power_dbm=-90.0,
    signals=[
        SignalDef(freq_offset_hz=5e6, power_dbm=-50.0, signal_type="tone"),
        SignalDef(freq_offset_hz=-3e6, bandwidth_hz=10e6, power_dbm=-55.0,
                  signal_type="wideband", num_subcarriers=128),
    ],
    seed=42,
)
```

- `freq_offset_hz`: offset from center frequency (can be negative)
- `signal_type="tone"`: single complex sinusoid
- `signal_type="wideband"`: sum of random-phase subcarriers (OFDM-like)
- `seed`: deterministic output for reproducible tests
- Phase-continuous across successive `read()` calls

### Pre-Defined Signals (in `tools/sentinel_runner.py`)

These model real-world drone signals for demo/integration use:

| Name | Offset | BW | Power | Models |
|---|---|---|---|---|
| `DJI_SIGNAL` | +5 MHz | 10 MHz | -55 dBm | DJI OcuSync wideband OFDM |
| `RC_TONE` | -8 MHz | tone | -60 dBm | RC control link |
| `ELRS_SIGNAL` | +12 MHz | 500 kHz | -65 dBm | ExpressLRS spread spectrum |

## Writing a Unit Test

Follow the pattern in existing tests. Example for a new detector:

```python
"""Tests for MyNewDetector."""

import numpy as np
from src.dsp.detector import MyNewDetector, Detection

SAMPLE_RATE = 30.72e6
FFT_SIZE = 2048
CENTER_FREQ = 2.437e9

def make_freq_axis():
    return np.linspace(
        CENTER_FREQ - SAMPLE_RATE / 2,
        CENTER_FREQ + SAMPLE_RATE / 2,
        FFT_SIZE, endpoint=False,
    )

def make_noise_psd(power_dbm=-90.0, seed=0):
    rng = np.random.default_rng(seed)
    return power_dbm + rng.normal(0, 0.5, FFT_SIZE)

def inject_tone(psd, bin_idx, power_dbm, width_bins=3):
    out = psd.copy()
    half = width_bins // 2
    lo = max(0, bin_idx - half)
    hi = min(FFT_SIZE, bin_idx + half + 1)
    out[lo:hi] = power_dbm
    return out


class TestNoSignal:
    """Pure noise → zero detections."""

    def test_no_detection_on_noise(self):
        det = MyNewDetector(...)
        freq = make_freq_axis()
        psd = make_noise_psd()
        results = det.process(psd, freq)
        assert results == []


class TestStrongSignal:
    """Strong tone → detected with correct SNR."""

    def test_detects_strong_tone(self):
        det = MyNewDetector(...)
        freq = make_freq_axis()
        psd = inject_tone(make_noise_psd(), 512, -60.0, width_bins=5)
        results = det.process(psd, freq)
        assert len(results) >= 1
        assert results[0].snr_db > 10


class TestWeakSignal:
    """Signal below threshold → rejected."""

    def test_rejects_weak_tone(self):
        det = MyNewDetector(...)
        freq = make_freq_axis()
        psd = inject_tone(make_noise_psd(), 512, -87.0, width_bins=3)
        results = det.process(psd, freq)
        assert results == []
```

### Minimum test cases for any detector

1. **Noise immunity** — pure noise produces zero detections
2. **Strong signal detection** — signal well above threshold is found
3. **Weak signal rejection** — signal below threshold is ignored
4. **Multiple signals** — two separated signals produce two detections
5. **Bandwidth estimation** — detection bandwidth tracks signal width

### Async pipeline tests

Pipeline tests use `pytest-asyncio`. Test classes contain `async def` methods:

```python
class TestPipeline:
    async def test_detects_tone(self):
        cfg = make_test_config()
        source = make_source(signals=[...])
        engine = PipelineEngine(config=cfg, source=source)
        await engine.start()
        await engine.run(max_frames=20)
        await engine.stop()
        assert engine.detection_count > 0
```

See `tests/unit/test_pipeline.py` for the `make_test_config()` helper — it builds a full `SentinelConfig` tuned for fast synthetic testing (lower duration gates, faster noise floor convergence).

## Hardware Tests

Tests marked `@pytest.mark.hardware` are skipped by default. They require a USRP B210 connected via USB 3.0.

```bash
make test-hardware
```

These validate:
- B210 start/stop lifecycle
- Sample read (correct dtype, shape)
- Multiple consecutive reads
- Large reads (32k samples)

If no B210 is available, these tests are automatically skipped.

## Field Test Protocol

When conducting a field test with a real drone:

### Before the test

1. Record: date, location (GPS), weather, drone model, expected distance
2. Verify B210 is connected: `uhd_usrp_probe`
3. Run `make test-quick` to confirm software is working
4. Ensure `data/samples/` directory exists

### During the test

1. Start pipeline: `make run-live`
2. Log the drone's actual position/bearing at regular intervals
3. Record all detection output (pipeline logs to `logs/sentinel.jsonl`)

### After the test

1. Save raw IQ captures: `data/samples/YYYY-MM-DD_<testname>.cf32`
2. Compare detected bearings vs actual bearings
3. Calculate: detection rate, false positive rate, bearing error, latency
4. Document results in `docs/test-logs/`

### Pass criteria

| Metric | Target |
|---|---|
| Detection range | >= 200m (open sky) |
| Bearing accuracy | <= +/-5 deg at 100m |
| False positive rate | < 1 per 10 min |
| Detection latency | < 2 sec |
