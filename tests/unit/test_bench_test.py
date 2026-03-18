"""Tests for tools.bench_test — config overrides, WiFi channel lookup, report."""

import argparse
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.sdr.config import load_config
from tools.bench_test import (
    WIFI_CHANNEL_FREQ_HZ,
    BenchResults,
    apply_cli_overrides,
    compute_report,
    _deduplicate,
)
from src.dsp.detector import Detection


# ---------------------------------------------------------------------------
# WiFi channel lookup
# ---------------------------------------------------------------------------

class TestWiFiChannelFreq:
    def test_all_channels_present(self):
        for ch in range(1, 15):
            assert ch in WIFI_CHANNEL_FREQ_HZ

    def test_channel_1_freq(self):
        assert WIFI_CHANNEL_FREQ_HZ[1] == 2.412e9

    def test_channel_6_freq(self):
        assert WIFI_CHANNEL_FREQ_HZ[6] == 2.437e9

    def test_channel_14_freq(self):
        assert WIFI_CHANNEL_FREQ_HZ[14] == 2.484e9

    def test_channels_monotonic(self):
        freqs = [WIFI_CHANNEL_FREQ_HZ[ch] for ch in range(1, 14)]
        for i in range(len(freqs) - 1):
            assert freqs[i] < freqs[i + 1]


# ---------------------------------------------------------------------------
# CLI config overrides
# ---------------------------------------------------------------------------

def _make_args(**overrides) -> argparse.Namespace:
    """Build a Namespace with default bench_test args, applying overrides."""
    defaults = dict(
        live=False, config=None, device="",
        gain=None, freq=None, bandwidth=None, sample_rate=None, channel=None,
        cfar_threshold=None, tripwire_threshold=None, fft_size=None,
        duration=None, frames=None, warmup=2.0,
        save_iq=False, output=None,
        expect_freq=None, freq_tolerance=1e6,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestApplyCliOverrides:
    def test_no_overrides_returns_valid_config(self):
        config = load_config()
        args = _make_args()
        result = apply_cli_overrides(config, args)
        assert result.sdr.rx_a.center_freq_hz == config.sdr.rx_a.center_freq_hz

    def test_gain_override(self):
        config = load_config()
        args = _make_args(gain=25.0)
        result = apply_cli_overrides(config, args)
        assert result.sdr.rx_a.gain_db == 25.0

    def test_freq_override(self):
        config = load_config()
        args = _make_args(freq=2.45e9)
        result = apply_cli_overrides(config, args)
        assert result.sdr.rx_a.center_freq_hz == 2.45e9

    def test_channel_override(self):
        config = load_config()
        args = _make_args(channel=1)
        result = apply_cli_overrides(config, args)
        assert result.sdr.rx_a.center_freq_hz == 2.412e9

    def test_channel_overrides_freq(self):
        """--channel should be used when --freq is not set."""
        config = load_config()
        args = _make_args(channel=11)
        result = apply_cli_overrides(config, args)
        assert result.sdr.rx_a.center_freq_hz == 2.462e9

    def test_freq_takes_precedence_over_channel(self):
        """--freq wins if both --freq and --channel are set."""
        config = load_config()
        args = _make_args(freq=2.45e9, channel=1)
        result = apply_cli_overrides(config, args)
        assert result.sdr.rx_a.center_freq_hz == 2.45e9

    def test_invalid_channel_raises(self):
        config = load_config()
        args = _make_args(channel=99)
        with pytest.raises(ValueError, match="Invalid WiFi channel"):
            apply_cli_overrides(config, args)

    def test_cfar_threshold_override(self):
        config = load_config()
        args = _make_args(cfar_threshold=8.0)
        result = apply_cli_overrides(config, args)
        assert result.dsp.cfar.threshold_factor_db == 8.0

    def test_tripwire_threshold_override(self):
        config = load_config()
        args = _make_args(tripwire_threshold=15.0)
        result = apply_cli_overrides(config, args)
        assert result.dsp.tripwire.threshold_db == 15.0

    def test_fft_size_override(self):
        config = load_config()
        args = _make_args(fft_size=4096)
        result = apply_cli_overrides(config, args)
        assert result.dsp.fft_size == 4096

    def test_sample_rate_override(self):
        config = load_config()
        args = _make_args(sample_rate=20e6)
        result = apply_cli_overrides(config, args)
        assert result.sdr.rx_a.sample_rate_hz == 20e6

    def test_bandwidth_override(self):
        config = load_config()
        args = _make_args(bandwidth=15e6)
        result = apply_cli_overrides(config, args)
        assert result.sdr.rx_a.bandwidth_hz == 15e6

    def test_synthetic_mode_fast_convergence(self):
        """Synthetic mode should apply fast-convergence overrides."""
        config = load_config()
        args = _make_args(live=False)
        result = apply_cli_overrides(config, args)
        assert result.dsp.tripwire.noise_floor_window_sec == 0.5
        assert result.dsp.tripwire.min_trigger_duration_ms == 10
        assert result.dsp.cfar.min_detection_bw_hz == 30e3

    def test_live_mode_no_fast_convergence(self):
        """Live mode should NOT apply fast-convergence overrides."""
        config = load_config()
        original_nf_window = config.dsp.tripwire.noise_floor_window_sec
        args = _make_args(live=True)
        result = apply_cli_overrides(config, args)
        assert result.dsp.tripwire.noise_floor_window_sec == original_nf_window


# ---------------------------------------------------------------------------
# BenchResults
# ---------------------------------------------------------------------------

class TestBenchResults:
    def test_record_frame(self):
        r = BenchResults()
        r.record_frame(-85.0)
        r.record_frame(-84.5)
        assert r.frame_count == 2
        assert len(r.noise_floors) == 2

    def test_record_detection(self):
        r = BenchResults()
        d = Detection(
            freq_hz=2.442e9, bandwidth_hz=10e6,
            power_dbm=-55.0, snr_db=25.0,
            bin_start=100, bin_end=200,
        )
        r.record_detection(d, frame=5)
        assert len(r.detections) == 1
        assert r.detections[0]["frame"] == 5
        assert r.detections[0]["freq_hz"] == 2.442e9

    def test_record_iq(self):
        r = BenchResults()
        iq = np.zeros(1024, dtype=np.complex64)
        r.record_iq(iq)
        assert len(r.iq_buffer) == 1


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

class TestComputeReport:
    def test_report_structure(self):
        r = BenchResults(warmup_frames=10)
        for i in range(20):
            r.record_frame(-85.0 + np.random.randn() * 0.5)

        args = _make_args()
        config = load_config()
        report = compute_report(r, config, args, elapsed_sec=5.0)

        assert report["test"] == "bench_test"
        assert report["frames_collected"] == 20
        assert report["warmup_frames"] == 10
        assert report["detection_count"] == 0
        assert report["detection_rate"] == 0.0
        assert report["noise_floor_dbm"] is not None

    def test_report_with_detections(self):
        r = BenchResults(warmup_frames=2)
        for i in range(10):
            r.record_frame(-85.0)
        d = Detection(
            freq_hz=2.437e9, bandwidth_hz=10e6,
            power_dbm=-55.0, snr_db=30.0,
            bin_start=500, bin_end=600,
        )
        r.record_detection(d, frame=5)

        args = _make_args()
        config = load_config()
        report = compute_report(r, config, args, elapsed_sec=2.0)

        assert report["detection_count"] == 1
        assert report["snr_db"]["mean"] == 30.0

    def test_report_freq_accuracy(self):
        r = BenchResults(warmup_frames=0)
        for i in range(5):
            r.record_frame(-85.0)
        d = Detection(
            freq_hz=2.437e9, bandwidth_hz=10e6,
            power_dbm=-55.0, snr_db=25.0,
            bin_start=500, bin_end=600,
        )
        r.record_detection(d, frame=1)

        args = _make_args(expect_freq=2.437e9, freq_tolerance=1e6)
        config = load_config()
        report = compute_report(r, config, args, elapsed_sec=1.0)

        assert "freq_accuracy" in report
        assert report["freq_accuracy"]["accuracy_pct"] == 100.0
        assert report["freq_accuracy"]["mean_error_hz"] == 0.0


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplicate:
    def test_no_overlap(self):
        d1 = Detection(freq_hz=2.43e9, bandwidth_hz=1e6, power_dbm=-55, snr_db=20, bin_start=100, bin_end=150)
        d2 = Detection(freq_hz=2.45e9, bandwidth_hz=1e6, power_dbm=-60, snr_db=15, bin_start=300, bin_end=350)
        result = _deduplicate([d1, d2])
        assert len(result) == 2

    def test_overlap_keeps_higher_snr(self):
        d1 = Detection(freq_hz=2.44e9, bandwidth_hz=5e6, power_dbm=-55, snr_db=20, bin_start=100, bin_end=200)
        d2 = Detection(freq_hz=2.44e9, bandwidth_hz=5e6, power_dbm=-50, snr_db=25, bin_start=120, bin_end=220)
        result = _deduplicate([d1, d2])
        assert len(result) == 1
        assert result[0].snr_db == 25

    def test_empty_list(self):
        assert _deduplicate([]) == []

    def test_single_detection(self):
        d = Detection(freq_hz=2.44e9, bandwidth_hz=1e6, power_dbm=-55, snr_db=20, bin_start=100, bin_end=150)
        assert _deduplicate([d]) == [d]
