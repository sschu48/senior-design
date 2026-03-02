"""Tests for src.sdr.capture.SyntheticSource."""

import asyncio

import numpy as np
import pytest

from src.sdr.capture import SignalDef, SyntheticSource


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


SAMPLE_RATE = 30.72e6
NUM_SAMPLES = 65536


# ---------------------------------------------------------------------------
# Basic output properties
# ---------------------------------------------------------------------------

class TestOutputShape:
    def test_dtype_is_complex64(self):
        src = SyntheticSource(sample_rate_hz=SAMPLE_RATE, seed=0)
        run(src.start())
        iq = run(src.read(NUM_SAMPLES))
        run(src.stop())
        assert iq.dtype == np.complex64

    def test_correct_length(self):
        src = SyntheticSource(sample_rate_hz=SAMPLE_RATE, seed=0)
        run(src.start())
        iq = run(src.read(NUM_SAMPLES))
        run(src.stop())
        assert len(iq) == NUM_SAMPLES


class TestNoisePower:
    def test_noise_matches_configured_dbm(self):
        """Noise-only power should match configured dBm within +-0.5 dB."""
        target_dbm = -90.0
        src = SyntheticSource(
            sample_rate_hz=SAMPLE_RATE,
            noise_power_dbm=target_dbm,
            seed=42,
        )
        run(src.start())
        iq = run(src.read(NUM_SAMPLES))
        run(src.stop())

        # Measured power: mean |x|^2 → dBm
        measured_watts = np.mean(np.abs(iq.astype(np.complex128)) ** 2)
        measured_dbm = 10 * np.log10(measured_watts) + 30
        assert abs(measured_dbm - target_dbm) < 0.5, (
            f"Expected {target_dbm} dBm, got {measured_dbm:.2f} dBm"
        )


# ---------------------------------------------------------------------------
# Tone detection
# ---------------------------------------------------------------------------

class TestToneDetection:
    def test_tone_at_correct_bin(self):
        """Injected tone should produce a peak at the correct FFT bin."""
        tone_offset = 5e6  # +5 MHz
        src = SyntheticSource(
            sample_rate_hz=SAMPLE_RATE,
            noise_power_dbm=-100.0,
            signals=[SignalDef(freq_offset_hz=tone_offset, power_dbm=-40.0, signal_type="tone")],
            seed=1,
        )
        run(src.start())
        iq = run(src.read(NUM_SAMPLES))
        run(src.stop())

        # FFT and find peak
        spectrum = np.fft.fftshift(np.fft.fft(iq))
        freqs = np.fft.fftshift(np.fft.fftfreq(NUM_SAMPLES, d=1.0 / SAMPLE_RATE))
        peak_idx = np.argmax(np.abs(spectrum))
        peak_freq = freqs[peak_idx]

        # Allow +-1 bin tolerance
        bin_width = SAMPLE_RATE / NUM_SAMPLES
        assert abs(peak_freq - tone_offset) < 2 * bin_width, (
            f"Expected peak at {tone_offset/1e6:.2f} MHz, got {peak_freq/1e6:.4f} MHz"
        )


# ---------------------------------------------------------------------------
# Wideband signal
# ---------------------------------------------------------------------------

class TestWidebandSignal:
    def test_inband_vs_outofband_ratio(self):
        """Wideband signal should have >20 dB in-band vs out-of-band ratio."""
        bw = 10e6
        offset = 0.0
        src = SyntheticSource(
            sample_rate_hz=SAMPLE_RATE,
            noise_power_dbm=-110.0,  # very low noise
            signals=[
                SignalDef(
                    freq_offset_hz=offset,
                    bandwidth_hz=bw,
                    power_dbm=-40.0,
                    signal_type="wideband",
                    num_subcarriers=128,
                )
            ],
            seed=7,
        )
        run(src.start())
        iq = run(src.read(NUM_SAMPLES))
        run(src.stop())

        spectrum = np.abs(np.fft.fftshift(np.fft.fft(iq))) ** 2
        freqs = np.fft.fftshift(np.fft.fftfreq(NUM_SAMPLES, d=1.0 / SAMPLE_RATE))

        in_band = np.abs(freqs - offset) <= bw / 2
        out_band = np.abs(freqs - offset) > bw * 1.5  # well outside

        in_power = np.mean(spectrum[in_band])
        out_power = np.mean(spectrum[out_band])
        ratio_db = 10 * np.log10(in_power / out_power)

        assert ratio_db > 20, f"In-band/out-of-band ratio only {ratio_db:.1f} dB (need >20)"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestStartRequired:
    def test_read_before_start_raises(self):
        src = SyntheticSource(sample_rate_hz=SAMPLE_RATE)
        with pytest.raises(RuntimeError, match="not started"):
            run(src.read(1024))


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------

class TestReproducibility:
    def test_same_seed_identical_output(self):
        """Same seed must produce identical output."""
        src1 = SyntheticSource(sample_rate_hz=SAMPLE_RATE, seed=99)
        src2 = SyntheticSource(sample_rate_hz=SAMPLE_RATE, seed=99)

        run(src1.start())
        run(src2.start())
        iq1 = run(src1.read(4096))
        iq2 = run(src2.read(4096))
        run(src1.stop())
        run(src2.stop())

        np.testing.assert_array_equal(iq1, iq2)


# ---------------------------------------------------------------------------
# Phase continuity
# ---------------------------------------------------------------------------

class TestPhaseContinuity:
    def test_consecutive_reads_continuous(self):
        """Tone phase should be continuous across successive read() calls."""
        tone_freq = 1e6
        src = SyntheticSource(
            sample_rate_hz=SAMPLE_RATE,
            noise_power_dbm=-120.0,
            signals=[SignalDef(freq_offset_hz=tone_freq, power_dbm=-30.0, signal_type="tone")],
            seed=0,
        )
        run(src.start())
        iq1 = run(src.read(1024))
        iq2 = run(src.read(1024))
        run(src.stop())

        # Concatenate and check for phase discontinuity at the boundary
        combined = np.concatenate([iq1, iq2])

        # Phase difference between consecutive samples should be constant
        # (within noise tolerance) for a pure tone
        phases = np.angle(combined.astype(np.complex128))
        phase_diffs = np.diff(phases)
        # Unwrap to handle +-pi discontinuities
        phase_diffs = np.arctan2(np.sin(phase_diffs), np.cos(phase_diffs))

        # The boundary sample (index 1023→1024) should have same phase diff
        boundary_diff = phase_diffs[1023]
        nearby_diffs = phase_diffs[1020:1023]
        max_deviation = np.max(np.abs(nearby_diffs - boundary_diff))
        assert max_deviation < 0.01, (
            f"Phase discontinuity at boundary: max deviation = {max_deviation:.4f} rad"
        )
