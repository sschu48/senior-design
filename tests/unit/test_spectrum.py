"""Tests for src.dsp.spectrum."""

import numpy as np
import pytest

from src.dsp.spectrum import compute_psd, remove_dc_offset


SAMPLE_RATE = 30.72e6
FFT_SIZE = 2048
CENTER_FREQ = 2.437e9


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_tone(freq_offset: float, power_dbm: float, num_samples: int) -> np.ndarray:
    """Generate a complex sinusoid at the given offset and power."""
    amp = np.sqrt(10 ** ((power_dbm - 30) / 10))
    t = np.arange(num_samples, dtype=np.float64)
    phase = 2 * np.pi * freq_offset / SAMPLE_RATE * t
    return (amp * np.exp(1j * phase)).astype(np.complex64)


def make_noise(power_dbm: float, num_samples: int, seed: int = 0) -> np.ndarray:
    """Generate complex AWGN at the given power level."""
    rng = np.random.default_rng(seed)
    amp = np.sqrt(10 ** ((power_dbm - 30) / 10))
    std = amp / np.sqrt(2)
    noise = rng.normal(0, std, num_samples) + 1j * rng.normal(0, std, num_samples)
    return noise.astype(np.complex64)


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

class TestOutputShape:
    def test_output_length_equals_fft_size(self):
        iq = make_noise(-90, FFT_SIZE * 4)
        freq, psd = compute_psd(iq, SAMPLE_RATE, fft_size=FFT_SIZE)
        assert len(freq) == FFT_SIZE
        assert len(psd) == FFT_SIZE


# ---------------------------------------------------------------------------
# Frequency range
# ---------------------------------------------------------------------------

class TestFrequencyRange:
    def test_freq_spans_center_plus_minus_half_fs(self):
        iq = make_noise(-90, FFT_SIZE * 4)
        freq, _ = compute_psd(iq, SAMPLE_RATE, fft_size=FFT_SIZE, center_freq=CENTER_FREQ)

        expected_min = CENTER_FREQ - SAMPLE_RATE / 2
        expected_max = CENTER_FREQ + SAMPLE_RATE / 2

        # Allow one bin width of tolerance
        bin_width = SAMPLE_RATE / FFT_SIZE
        assert freq[0] >= expected_min - bin_width
        assert freq[-1] <= expected_max + bin_width


# ---------------------------------------------------------------------------
# Tone detection
# ---------------------------------------------------------------------------

class TestTonePeak:
    def test_tone_peak_at_correct_frequency(self):
        """A tone at +5 MHz should produce a PSD peak at center + 5 MHz."""
        tone_offset = 5e6
        tone_power = -40.0
        noise_power = -100.0

        iq = make_tone(tone_offset, tone_power, FFT_SIZE * 8) + make_noise(
            noise_power, FFT_SIZE * 8, seed=1
        )
        freq, psd = compute_psd(
            iq, SAMPLE_RATE, fft_size=FFT_SIZE, center_freq=CENTER_FREQ
        )

        peak_idx = np.argmax(psd)
        peak_freq = freq[peak_idx]
        expected_freq = CENTER_FREQ + tone_offset

        bin_width = SAMPLE_RATE / FFT_SIZE
        assert abs(peak_freq - expected_freq) < 2 * bin_width, (
            f"Peak at {peak_freq/1e6:.2f} MHz, expected {expected_freq/1e6:.2f} MHz"
        )

    def test_tone_power_recoverable(self):
        """Measured tone power should be within +-3 dB of injected power."""
        tone_offset = 3e6
        tone_power = -50.0
        noise_power = -100.0

        iq = make_tone(tone_offset, tone_power, FFT_SIZE * 16) + make_noise(
            noise_power, FFT_SIZE * 16, seed=2
        )
        freq, psd = compute_psd(
            iq, SAMPLE_RATE, fft_size=FFT_SIZE, center_freq=CENTER_FREQ
        )

        peak_power = np.max(psd)
        assert abs(peak_power - tone_power) < 3.0, (
            f"Measured {peak_power:.1f} dBm, expected {tone_power:.1f} dBm"
        )


# ---------------------------------------------------------------------------
# Noise floor
# ---------------------------------------------------------------------------

class TestNoiseFloor:
    def test_noise_floor_matches_expected(self):
        """Noise-only PSD should match expected level within +-3 dB.

        Expected per-bin power for AWGN:
            P_bin = P_total * (bin_width / sample_rate) ≈ P_total / N
        But Welch with windowing distributes power — we just check the
        median is in the right ballpark.
        """
        noise_power = -90.0
        iq = make_noise(noise_power, FFT_SIZE * 16, seed=3)
        _, psd = compute_psd(iq, SAMPLE_RATE, fft_size=FFT_SIZE)

        # Median PSD level — should be near noise_power - 10*log10(FFT_SIZE)
        # because power is spread across bins
        median_psd = np.median(psd)
        expected_per_bin = noise_power - 10 * np.log10(FFT_SIZE)

        assert abs(median_psd - expected_per_bin) < 3.0, (
            f"Median PSD {median_psd:.1f} dBm, expected ~{expected_per_bin:.1f} dBm"
        )


# ---------------------------------------------------------------------------
# DC offset removal
# ---------------------------------------------------------------------------

class TestDCRemoval:
    def test_removes_dc(self):
        iq = np.ones(1024, dtype=np.complex64) * (0.5 + 0.3j)
        corrected = remove_dc_offset(iq)
        assert np.abs(np.mean(corrected)) < 1e-6
