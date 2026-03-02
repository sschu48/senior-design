"""Tests for src.dsp.detector (TripwireDetector and CFARDetector)."""

import numpy as np
import pytest

from src.dsp.detector import CFARDetector, Detection, TripwireDetector


SAMPLE_RATE = 30.72e6
FFT_SIZE = 2048
CENTER_FREQ = 2.437e9
BIN_WIDTH = SAMPLE_RATE / FFT_SIZE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_freq_axis() -> np.ndarray:
    """Build a standard frequency axis centered on CENTER_FREQ."""
    return np.linspace(
        CENTER_FREQ - SAMPLE_RATE / 2,
        CENTER_FREQ + SAMPLE_RATE / 2,
        FFT_SIZE,
        endpoint=False,
    )


def make_noise_psd(power_dbm: float = -90.0, seed: int = 0) -> np.ndarray:
    """Generate a flat noise PSD floor with small random variation."""
    rng = np.random.default_rng(seed)
    return power_dbm + rng.normal(0, 0.5, FFT_SIZE)


def inject_tone(psd: np.ndarray, bin_idx: int, power_dbm: float, width_bins: int = 3) -> np.ndarray:
    """Inject a tone-like peak into a PSD array at a given bin."""
    out = psd.copy()
    half = width_bins // 2
    lo = max(0, bin_idx - half)
    hi = min(FFT_SIZE, bin_idx + half + 1)
    out[lo:hi] = power_dbm
    return out


# ===========================================================================
# TripwireDetector tests
# ===========================================================================

class TestTripwireNoSignal:
    """With only noise, tripwire should produce zero detections."""

    def test_no_detection_on_noise(self):
        det = TripwireDetector(
            threshold_db=10,
            noise_floor_frames=5,
            min_trigger_frames=2,
            sample_rate_hz=SAMPLE_RATE,
            center_freq_hz=CENTER_FREQ,
            fft_size=FFT_SIZE,
        )
        freq = make_freq_axis()
        # Feed several frames of pure noise
        for i in range(10):
            psd = make_noise_psd(seed=i)
            results = det.process(psd, freq)
        assert results == []


class TestTripwireToneAboveThreshold:
    """A strong tone should trigger after enough frames."""

    def test_detects_strong_tone(self):
        det = TripwireDetector(
            threshold_db=10,
            noise_floor_frames=5,
            min_trigger_frames=3,
            sample_rate_hz=SAMPLE_RATE,
            center_freq_hz=CENTER_FREQ,
            fft_size=FFT_SIZE,
        )
        freq = make_freq_axis()

        # Warm up noise floor with clean frames
        for i in range(10):
            det.process(make_noise_psd(seed=i), freq)

        # Now inject a strong tone
        tone_bin = FFT_SIZE // 4
        results = []
        for i in range(10):
            psd = inject_tone(make_noise_psd(seed=100 + i), tone_bin, -60.0, width_bins=5)
            results = det.process(psd, freq)

        assert len(results) >= 1
        d = results[0]
        assert d.bin_start <= tone_bin <= d.bin_end
        assert d.snr_db >= 10


class TestTripwireToneBelowThreshold:
    """A weak tone (below threshold) should not trigger."""

    def test_no_detection_weak_tone(self):
        det = TripwireDetector(
            threshold_db=10,
            noise_floor_frames=5,
            min_trigger_frames=2,
            sample_rate_hz=SAMPLE_RATE,
            center_freq_hz=CENTER_FREQ,
            fft_size=FFT_SIZE,
        )
        freq = make_freq_axis()

        # Warm up
        for i in range(10):
            det.process(make_noise_psd(seed=i), freq)

        # Inject tone only 3 dB above noise — below 10 dB threshold
        tone_bin = FFT_SIZE // 2
        results = []
        for i in range(10):
            psd = inject_tone(make_noise_psd(seed=200 + i), tone_bin, -87.0, width_bins=3)
            results = det.process(psd, freq)

        assert results == []


class TestTripwireTransientRejection:
    """A signal present for fewer frames than min_trigger_frames should be rejected."""

    def test_transient_rejected(self):
        det = TripwireDetector(
            threshold_db=10,
            noise_floor_frames=5,
            min_trigger_frames=5,
            sample_rate_hz=SAMPLE_RATE,
            center_freq_hz=CENTER_FREQ,
            fft_size=FFT_SIZE,
        )
        freq = make_freq_axis()

        # Warm up
        for i in range(10):
            det.process(make_noise_psd(seed=i), freq)

        # Inject tone for only 2 frames (< 5 required)
        tone_bin = FFT_SIZE // 3
        all_results = []
        for i in range(2):
            psd = inject_tone(make_noise_psd(seed=300 + i), tone_bin, -60.0, width_bins=5)
            all_results.extend(det.process(psd, freq))

        # Then return to noise
        for i in range(5):
            all_results.extend(det.process(make_noise_psd(seed=400 + i), freq))

        assert all_results == []


class TestTripwireNoiseFloorAdaptation:
    """Noise floor should adapt to changing background level."""

    def test_noise_floor_tracks_background(self):
        det = TripwireDetector(
            threshold_db=10,
            noise_floor_frames=10,
            min_trigger_frames=2,
            sample_rate_hz=SAMPLE_RATE,
            center_freq_hz=CENTER_FREQ,
            fft_size=FFT_SIZE,
        )
        freq = make_freq_axis()

        # Feed frames at -90 dBm
        for i in range(15):
            det.process(make_noise_psd(-90.0, seed=i), freq)

        nf1 = det.noise_floor.copy()

        # Feed frames at -80 dBm (10 dB higher)
        for i in range(15):
            det.process(make_noise_psd(-80.0, seed=500 + i), freq)

        nf2 = det.noise_floor.copy()

        # Noise floor should have increased
        assert np.median(nf2) > np.median(nf1) + 5


class TestTripwireMultipleDetections:
    """Two separated tones should produce two separate detections."""

    def test_two_tones_two_detections(self):
        det = TripwireDetector(
            threshold_db=10,
            noise_floor_frames=5,
            min_trigger_frames=2,
            sample_rate_hz=SAMPLE_RATE,
            center_freq_hz=CENTER_FREQ,
            fft_size=FFT_SIZE,
        )
        freq = make_freq_axis()

        # Warm up
        for i in range(10):
            det.process(make_noise_psd(seed=i), freq)

        # Two tones well separated
        tone_a = FFT_SIZE // 4
        tone_b = 3 * FFT_SIZE // 4
        results = []
        for i in range(5):
            psd = make_noise_psd(seed=600 + i)
            psd = inject_tone(psd, tone_a, -60.0, width_bins=5)
            psd = inject_tone(psd, tone_b, -65.0, width_bins=5)
            results = det.process(psd, freq)

        assert len(results) >= 2


class TestTripwireBandwidthEstimate:
    """Detection bandwidth should reflect the width of injected bins."""

    def test_bandwidth_proportional_to_width(self):
        det = TripwireDetector(
            threshold_db=10,
            noise_floor_frames=5,
            min_trigger_frames=2,
            sample_rate_hz=SAMPLE_RATE,
            center_freq_hz=CENTER_FREQ,
            fft_size=FFT_SIZE,
        )
        freq = make_freq_axis()

        for i in range(10):
            det.process(make_noise_psd(seed=i), freq)

        # Inject a wide signal (20 bins)
        tone_bin = FFT_SIZE // 2
        width = 20
        results = []
        for i in range(5):
            psd = make_noise_psd(seed=700 + i)
            psd = inject_tone(psd, tone_bin, -60.0, width_bins=width)
            results = det.process(psd, freq)

        assert len(results) >= 1
        d = results[0]
        # Bandwidth should be roughly width * bin_width
        expected_bw = width * BIN_WIDTH
        assert d.bandwidth_hz >= expected_bw * 0.5
        assert d.bandwidth_hz <= expected_bw * 2.0


class TestTripwireReset:
    """Reset should clear all internal state."""

    def test_reset_clears_state(self):
        det = TripwireDetector(
            threshold_db=10,
            noise_floor_frames=5,
            min_trigger_frames=2,
            sample_rate_hz=SAMPLE_RATE,
            center_freq_hz=CENTER_FREQ,
            fft_size=FFT_SIZE,
        )
        freq = make_freq_axis()

        for i in range(10):
            det.process(make_noise_psd(seed=i), freq)
        assert det.noise_floor is not None

        det.reset()
        assert det.noise_floor is None


# ===========================================================================
# CFARDetector tests
# ===========================================================================

class TestCFARSingleTone:
    """A single strong tone should be detected by CA-CFAR."""

    def test_detects_single_tone(self):
        det = CFARDetector(
            guard_cells=4,
            reference_cells=16,
            threshold_factor_db=10,
            min_detection_bw_hz=0,  # no BW filter for this test
            sample_rate_hz=SAMPLE_RATE,
            center_freq_hz=CENTER_FREQ,
            fft_size=FFT_SIZE,
        )
        freq = make_freq_axis()
        psd = make_noise_psd(-90.0, seed=0)

        # Strong tone at bin 512
        tone_bin = 512
        psd = inject_tone(psd, tone_bin, -60.0, width_bins=5)

        results = det.process(psd, freq)
        assert len(results) >= 1
        d = results[0]
        assert d.bin_start <= tone_bin <= d.bin_end
        assert d.snr_db > 10


class TestCFARMultipleTones:
    """Two separated tones should produce two detections."""

    def test_detects_two_tones(self):
        det = CFARDetector(
            guard_cells=4,
            reference_cells=16,
            threshold_factor_db=10,
            min_detection_bw_hz=0,
            sample_rate_hz=SAMPLE_RATE,
            center_freq_hz=CENTER_FREQ,
            fft_size=FFT_SIZE,
        )
        freq = make_freq_axis()
        psd = make_noise_psd(-90.0, seed=1)
        psd = inject_tone(psd, 400, -55.0, width_bins=5)
        psd = inject_tone(psd, 1600, -55.0, width_bins=5)

        results = det.process(psd, freq)
        assert len(results) >= 2


class TestCFARWeakToneRejection:
    """A tone barely above the noise should be rejected."""

    def test_rejects_weak_tone(self):
        det = CFARDetector(
            guard_cells=4,
            reference_cells=16,
            threshold_factor_db=10,
            min_detection_bw_hz=0,
            sample_rate_hz=SAMPLE_RATE,
            center_freq_hz=CENTER_FREQ,
            fft_size=FFT_SIZE,
        )
        freq = make_freq_axis()
        psd = make_noise_psd(-90.0, seed=2)

        # Tone only 3 dB above noise — should not pass 10 dB threshold
        psd = inject_tone(psd, 1024, -87.0, width_bins=3)

        results = det.process(psd, freq)
        assert results == []


class TestCFARMinBandwidthFilter:
    """Detections narrower than min_detection_bw_hz should be filtered out."""

    def test_narrow_detection_rejected(self):
        # min_detection_bw_hz = 500 kHz → need ~33 bins
        det = CFARDetector(
            guard_cells=4,
            reference_cells=16,
            threshold_factor_db=6,
            min_detection_bw_hz=500e3,
            sample_rate_hz=SAMPLE_RATE,
            center_freq_hz=CENTER_FREQ,
            fft_size=FFT_SIZE,
        )
        freq = make_freq_axis()
        psd = make_noise_psd(-90.0, seed=3)

        # Inject a narrow 3-bin tone — way below 33 bins required
        psd = inject_tone(psd, 800, -50.0, width_bins=3)

        results = det.process(psd, freq)
        assert results == []

    def test_wide_detection_passes(self):
        # Guard cells must be wider than half the signal width to prevent
        # signal leakage into reference cells (classic CFAR masking)
        det = CFARDetector(
            guard_cells=12,
            reference_cells=16,
            threshold_factor_db=6,
            min_detection_bw_hz=100e3,
            sample_rate_hz=SAMPLE_RATE,
            center_freq_hz=CENTER_FREQ,
            fft_size=FFT_SIZE,
        )
        freq = make_freq_axis()
        psd = make_noise_psd(-90.0, seed=4)

        # Wide signal: 20 bins ≈ 300 kHz > 100 kHz threshold
        psd = inject_tone(psd, 1024, -50.0, width_bins=20)

        results = det.process(psd, freq)
        assert len(results) >= 1


class TestCFARGuardCells:
    """Guard cells should prevent leakage from a strong signal into its own reference cells."""

    def test_guard_cells_prevent_leakage(self):
        # With guard_cells=0, a strong signal can elevate its own noise estimate
        # With proper guard cells, the detection should be cleaner
        det_with_guard = CFARDetector(
            guard_cells=8,
            reference_cells=16,
            threshold_factor_db=8,
            min_detection_bw_hz=0,
            sample_rate_hz=SAMPLE_RATE,
            center_freq_hz=CENTER_FREQ,
            fft_size=FFT_SIZE,
        )
        freq = make_freq_axis()
        psd = make_noise_psd(-90.0, seed=5)
        psd = inject_tone(psd, 1024, -60.0, width_bins=5)

        results = det_with_guard.process(psd, freq)
        # Should detect with proper guard cells
        assert len(results) >= 1


class TestCFARThresholdSensitivity:
    """Higher threshold should reject signals that pass lower threshold."""

    def test_high_threshold_rejects(self):
        freq = make_freq_axis()
        psd = make_noise_psd(-90.0, seed=6)
        # Tone 15 dB above noise
        psd = inject_tone(psd, 1024, -75.0, width_bins=5)

        det_low = CFARDetector(
            guard_cells=4, reference_cells=16,
            threshold_factor_db=6, min_detection_bw_hz=0,
            sample_rate_hz=SAMPLE_RATE, center_freq_hz=CENTER_FREQ,
            fft_size=FFT_SIZE,
        )
        det_high = CFARDetector(
            guard_cells=4, reference_cells=16,
            threshold_factor_db=20, min_detection_bw_hz=0,
            sample_rate_hz=SAMPLE_RATE, center_freq_hz=CENTER_FREQ,
            fft_size=FFT_SIZE,
        )

        results_low = det_low.process(psd, freq)
        results_high = det_high.process(psd, freq)

        assert len(results_low) >= 1
        assert results_high == []


class TestCFARNoSignal:
    """Pure noise should produce zero detections."""

    def test_no_detection_on_noise(self):
        det = CFARDetector(
            guard_cells=4,
            reference_cells=16,
            threshold_factor_db=10,
            min_detection_bw_hz=0,
            sample_rate_hz=SAMPLE_RATE,
            center_freq_hz=CENTER_FREQ,
            fft_size=FFT_SIZE,
        )
        freq = make_freq_axis()
        psd = make_noise_psd(-90.0, seed=7)

        results = det.process(psd, freq)
        assert results == []
