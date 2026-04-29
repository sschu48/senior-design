# HackRF Bench Setup

Status: Phase 1.5 test environment

This is the simplest repeatable environment for testing SENTINEL against the
HackRF dummy-drone transmitter.

## Roles

Pi side:

- Raspberry Pi
- HackRF One
- `tools.hackrf_tx`
- 2.4 GHz dipole, or conducted cable path

SENTINEL side:

- USRP B210
- Omni on RX-A, Yagi on RX-B
- `tools.hackrf_bench`
- Optional attenuator/splitter for conducted tests

## Test Order

### 1. Conducted Cable Test

Use this before over-the-air tests.

Signal path:

```text
Pi -> HackRF -> 40-60 dB attenuation -> splitter -> B210 RX-A/RX-B
```

Purpose:

- Prove both B210 channels see the same known signal.
- Measure RX-A/RX-B power offset.
- Verify frequency alignment and sample-rate stability.
- Avoid multipath and accidental high-power radiated testing.

Pi command:

```bash
python -m tools.hackrf_tx --profile tone_2437 --gain 0 --rx-distance 3
```

SENTINEL command:

```bash
python -m tools.hackrf_bench --live --dual --profile tone_2437 --gain 10
```

The tone profile transmits at 2.437 GHz, but the RX harness tunes the B210
1 MHz low by default. That keeps the CW tone away from DC so DC-offset removal
does not erase the smoke-test signal.

Expected result:

- Both channels should pass the minimum SNR gate.
- RX-A/RX-B peak frequency delta should be small.
- Yagi-minus-omni power delta is the cable/splitter/channel offset, not antenna gain.

### 2. Low-Power Radiated Smoke Test

Use antennas only after the conducted test looks sane.

Signal path:

```text
Pi -> HackRF -> 2.4 GHz dipole  ))) air (((  B210 omni/Yagi
```

Start conservative:

```bash
python -m tools.hackrf_tx --profile tone_2437 --gain 0 --rx-distance 3
python -m tools.hackrf_bench --live --dual --profile tone_2437 --gain 10
```

Rules:

- Keep HackRF amp off.
- Start with at least 3 m antenna separation.
- Point the Yagi away first, then toward the HackRF.
- Do not use the Yagi + LNA path near unknown strong 2.4 GHz emitters until
  limiter/attenuator hardware is confirmed.

### 3. Continuous Wideband Test

This tests a drone-video-shaped wideband signal without burst timing ambiguity.

```bash
python -m tools.hackrf_tx --profile ocusync_video --gain 0 --rx-distance 3
python -m tools.hackrf_bench --live --dual --profile ocusync_video --gain 10 --duration 3
```

Expected result:

- CFAR should see energy across the expected wideband region on the Yagi path.
- The SNR should improve when the Yagi points toward the HackRF.

### 4. Bursty DroneID-Like Test

This tests low-duty-cycle burst detection. Run it after tone and continuous
wideband tests pass.

```bash
python -m tools.hackrf_tx --profile dji_droneid --gain 0 --rx-distance 3
python -m tools.hackrf_bench --live --dual --profile dji_droneid --gain 10 --duration 10
```

Expected result:

- Presence rate will be low because the profile is a 1 ms burst every 600 ms.
- Max SNR matters more than mean SNR for this test.
- Missed detections here do not automatically mean RF failure. They may mean
  detector timing or burst capture windows need tuning.

## Reports

The RX harness writes JSON reports under:

```text
data/bench/hackrf_bench_<profile>_<timestamp>.json
```

Optional IQ capture:

```bash
python -m tools.hackrf_bench --live --dual --profile tone_2437 --gain 10 --save-iq
```

This creates per-channel `.cf32` files next to the report.

## Quick Command Helper

Print the paired TX/RX commands without running hardware:

```bash
python -m tools.hackrf_bench --profile tone_2437 --setup-only
```

## What This Proves

This environment proves:

- The B210 can see a known 2.4 GHz emitter.
- RX-A and RX-B are tuned to the same expected signal.
- The detector path reports repeatable SNR and frequency evidence.
- The Yagi path can be compared against the omni path.

This environment does not prove:

- Real DJI decoding.
- True FHSS detection.
- Full-range drone detection.
- Legal or standards-compliant transmission behavior from HackRF.

Those come later, after calibrated captures and replay tests exist.
