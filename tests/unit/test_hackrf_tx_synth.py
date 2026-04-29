"""Tests for tools.hackrf_tx.synth — IQ synthesis and format conversion."""

import numpy as np
import pytest

from tools.hackrf_tx.synth import (
    cf32_to_cs8,
    cs8_to_cf32,
    synth_ofdm,
    synth_periodic_burst,
    synth_tone,
)


# ---------------------------------------------------------------------------
# synth_tone
# ---------------------------------------------------------------------------

class TestSynthTone:
    def test_dtype_is_complex64(self):
        iq = synth_tone(sample_rate_hz=2e6, duration_s=0.001)
        assert iq.dtype == np.complex64

    def test_correct_length(self):
        iq = synth_tone(sample_rate_hz=10e6, duration_s=0.0005)
        assert len(iq) == 5000

    def test_zero_duration_raises(self):
        with pytest.raises(ValueError, match="too short"):
            synth_tone(sample_rate_hz=10e6, duration_s=0.0)

    def test_dc_tone_is_constant_envelope(self):
        """A 0 Hz offset tone should have flat magnitude."""
        iq = synth_tone(sample_rate_hz=2e6, duration_s=0.001, freq_offset_hz=0.0)
        mags = np.abs(iq)
        assert np.std(mags) < 1e-5

    def test_offset_tone_peaks_at_correct_bin(self):
        """A 1 MHz offset tone should peak at +1 MHz in the FFT."""
        rate = 4e6
        offset = 1e6
        iq = synth_tone(sample_rate_hz=rate, duration_s=0.002, freq_offset_hz=offset)
        spec = np.fft.fftshift(np.fft.fft(iq))
        freqs = np.fft.fftshift(np.fft.fftfreq(len(iq), d=1.0 / rate))
        peak_freq = freqs[np.argmax(np.abs(spec))]
        bin_w = rate / len(iq)
        assert abs(peak_freq - offset) < 2 * bin_w


# ---------------------------------------------------------------------------
# synth_ofdm
# ---------------------------------------------------------------------------

class TestSynthOFDM:
    def test_dtype_and_length(self):
        iq = synth_ofdm(
            sample_rate_hz=10e6, duration_s=0.001,
            bandwidth_hz=8e6, num_subcarriers=64,
        )
        assert iq.dtype == np.complex64
        assert len(iq) == 10000

    def test_invalid_subcarriers_raises(self):
        with pytest.raises(ValueError, match="num_subcarriers"):
            synth_ofdm(sample_rate_hz=10e6, duration_s=0.001,
                       bandwidth_hz=8e6, num_subcarriers=0)

    def test_bandwidth_exceeds_rate_raises(self):
        with pytest.raises(ValueError, match="exceeds"):
            synth_ofdm(sample_rate_hz=10e6, duration_s=0.001,
                       bandwidth_hz=15e6, num_subcarriers=64)

    def test_invalid_envelope_raises(self):
        with pytest.raises(ValueError, match="envelope"):
            synth_ofdm(sample_rate_hz=10e6, duration_s=0.001,
                       bandwidth_hz=8e6, num_subcarriers=64, envelope="bogus")

    def test_inband_dominates_outofband(self):
        """OFDM energy should be concentrated within the configured BW."""
        rate = 20e6
        bw = 6e6
        iq = synth_ofdm(
            sample_rate_hz=rate, duration_s=0.002,
            bandwidth_hz=bw, num_subcarriers=128, envelope="rect",
        )
        spec = np.abs(np.fft.fftshift(np.fft.fft(iq))) ** 2
        freqs = np.fft.fftshift(np.fft.fftfreq(len(iq), d=1.0 / rate))
        in_band = np.abs(freqs) <= bw / 2
        # "Out of band" = clearly outside the synthesized BW but still inside
        # the captured spectrum.  Skirt margin is 1 MHz to skip skirts.
        out_band = (np.abs(freqs) > bw / 2 + 1e6) & (np.abs(freqs) < rate / 2 - 1e6)
        assert np.any(out_band), "test setup error: no out-of-band bins"
        ratio_db = 10 * np.log10(spec[in_band].mean() / spec[out_band].mean())
        assert ratio_db > 15, f"in/out ratio only {ratio_db:.1f} dB"

    def test_hann_envelope_has_zero_edges(self):
        """Hann envelope ramps from 0, so first and last samples should be ~0."""
        iq = synth_ofdm(
            sample_rate_hz=10e6, duration_s=0.001,
            bandwidth_hz=6e6, num_subcarriers=64, envelope="hann",
        )
        peak = np.max(np.abs(iq))
        # First and last samples are within numerical noise of zero.
        assert abs(iq[0]) < peak * 1e-3
        assert abs(iq[-1]) < peak * 1e-3

    def test_rect_envelope_no_taper(self):
        """Rect envelope should NOT have near-zero edges."""
        iq = synth_ofdm(
            sample_rate_hz=10e6, duration_s=0.001,
            bandwidth_hz=6e6, num_subcarriers=64, envelope="rect",
        )
        peak = np.max(np.abs(iq))
        # Edges should be a meaningful fraction of peak.
        assert abs(iq[0]) > peak * 0.05
        assert abs(iq[-1]) > peak * 0.05

    def test_amplitude_within_headroom(self):
        """Output peak must stay below 1.0 (so cs8 conversion has headroom)."""
        iq = synth_ofdm(
            sample_rate_hz=10e6, duration_s=0.001,
            bandwidth_hz=8e6, num_subcarriers=128, envelope="rect",
        )
        assert np.max(np.abs(iq)) < 1.0


# ---------------------------------------------------------------------------
# synth_periodic_burst
# ---------------------------------------------------------------------------

class TestSynthPeriodicBurst:
    def test_total_length_matches_period(self):
        burst = synth_tone(sample_rate_hz=10e6, duration_s=0.001)
        out = synth_periodic_burst(burst, sample_rate_hz=10e6, period_s=0.6)
        assert len(out) == 6_000_000

    def test_burst_at_start(self):
        burst = synth_tone(sample_rate_hz=10e6, duration_s=0.001)
        out = synth_periodic_burst(burst, sample_rate_hz=10e6, period_s=0.01)
        # Active burst region equals the original.
        np.testing.assert_array_equal(out[: len(burst)], burst)

    def test_silence_after_burst(self):
        burst = synth_tone(sample_rate_hz=10e6, duration_s=0.001)
        out = synth_periodic_burst(burst, sample_rate_hz=10e6, period_s=0.01)
        # Tail beyond burst is zero.
        assert np.all(out[len(burst):] == 0)

    def test_period_too_short_raises(self):
        burst = synth_tone(sample_rate_hz=10e6, duration_s=0.005)
        with pytest.raises(ValueError, match="too short"):
            synth_periodic_burst(burst, sample_rate_hz=10e6, period_s=0.001)


# ---------------------------------------------------------------------------
# cf32 ↔ cs8 round-trip
# ---------------------------------------------------------------------------

class TestFormatConversion:
    def test_cf32_to_cs8_doubles_length(self):
        iq = np.array([1 + 1j, -1 - 1j], dtype=np.complex64)
        out = cf32_to_cs8(iq)
        assert len(out) == 4
        assert out.dtype == np.int8

    def test_cs8_to_cf32_halves_length(self):
        cs8 = np.array([100, 50, -100, -50], dtype=np.int8)
        out = cs8_to_cf32(cs8)
        assert len(out) == 2
        assert out.dtype == np.complex64

    def test_odd_cs8_length_raises(self):
        with pytest.raises(ValueError, match="must be even"):
            cs8_to_cf32(np.array([1, 2, 3], dtype=np.int8))

    def test_zero_input_returns_empty(self):
        out = cf32_to_cs8(np.zeros(0, dtype=np.complex64))
        assert out.size == 0

    def test_all_zeros_pass_through(self):
        iq = np.zeros(100, dtype=np.complex64)
        out = cf32_to_cs8(iq)
        assert np.all(out == 0)
        back = cs8_to_cf32(out)
        np.testing.assert_array_equal(back, iq)

    def test_round_trip_preserves_within_quantization(self):
        """cf32 → cs8 → cf32 preserves shape up to a single scale factor.

        The forward conversion normalizes peak to ~127/1.05; the reverse
        divides by 127.  So |back| ≈ |iq| / 1.05 plus quantization noise.
        We compare after rescaling.
        """
        rng = np.random.default_rng(0)
        iq = (rng.normal(0, 0.3, 1000) + 1j * rng.normal(0, 0.3, 1000)).astype(np.complex64)
        # Pre-normalize so cf32 peak is well-defined.
        iq /= np.max(np.abs(iq))
        iq *= 0.9  # peak = 0.9 — no clipping at full scale

        cs8 = cf32_to_cs8(iq)
        back = cs8_to_cf32(cs8)

        # Best-fit scalar (handles the 1.05 headroom factor).
        alpha = float(np.real(np.vdot(back, iq) / np.vdot(back, back)))
        max_err = np.max(np.abs(back * alpha - iq))
        assert max_err < 0.02, f"round-trip residual after rescale: {max_err:.4f}"

    def test_no_clipping_at_full_scale(self):
        """A complex sinusoid at amplitude 1.0 should not clip to ±127."""
        iq = np.exp(1j * np.linspace(0, 100, 200)).astype(np.complex64)
        cs8 = cf32_to_cs8(iq)
        # With 5% headroom, peak should be at most ~121 (= 127/1.05).
        assert np.max(np.abs(cs8)) < 127

    def test_interleaving_order(self):
        """First two cs8 bytes are real, imag of first sample."""
        iq = np.array([0.5 + 0.25j], dtype=np.complex64)
        out = cf32_to_cs8(iq)
        # peak = 0.5, scale = 127 / (0.5 * 1.05) ≈ 241.9
        # real * scale ≈ 121, imag * scale ≈ 60
        assert out[0] > 0  # real positive
        assert out[1] > 0  # imag positive
        assert out[0] > out[1]  # real > imag in magnitude
