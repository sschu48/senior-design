"""Tests for tools.bench_test (V2 — Pipeline-backed)."""

import argparse

import numpy as np
import pytest

from src.pipeline.contracts import ChannelRole, RFEvent, SignalFamily
from src.sdr.config import load_config
from tools.bench_test import (
    WIFI_CHANNEL_FREQ_HZ,
    BenchResults,
    apply_cli_overrides,
    compute_report,
)


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
# CLI overrides
# ---------------------------------------------------------------------------

def _make_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        live=False, config=None, device="",
        gain=None, freq=None, bandwidth=None, sample_rate=None, channel=None,
        burst_threshold=None, cfar_threshold=None, fft_size=None,
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
        # Both rx_a and rx_b should share the override.
        assert result.sdr.rx_b.center_freq_hz == 2.45e9

    def test_channel_override(self):
        config = load_config()
        args = _make_args(channel=1)
        result = apply_cli_overrides(config, args)
        assert result.sdr.rx_a.center_freq_hz == 2.412e9

    def test_freq_takes_precedence_over_channel(self):
        config = load_config()
        args = _make_args(freq=2.45e9, channel=1)
        result = apply_cli_overrides(config, args)
        assert result.sdr.rx_a.center_freq_hz == 2.45e9

    def test_invalid_channel_raises(self):
        config = load_config()
        args = _make_args(channel=99)
        with pytest.raises(ValueError, match="Invalid WiFi channel"):
            apply_cli_overrides(config, args)

    def test_burst_threshold_override(self):
        config = load_config()
        args = _make_args(burst_threshold=12.0)
        result = apply_cli_overrides(config, args)
        assert result.dsp.burst.threshold_db == 12.0

    def test_cfar_threshold_override(self):
        config = load_config()
        args = _make_args(cfar_threshold=8.0)
        result = apply_cli_overrides(config, args)
        assert result.dsp.cfar.threshold_factor_db == 8.0

    def test_fft_size_override(self):
        config = load_config()
        args = _make_args(fft_size=2048)
        result = apply_cli_overrides(config, args)
        assert result.dsp.spectrogram.fft_size == 2048

    def test_sample_rate_override(self):
        config = load_config()
        args = _make_args(sample_rate=20e6)
        result = apply_cli_overrides(config, args)
        assert result.sdr.rx_a.sample_rate_hz == 20e6

    def test_synthetic_mode_fast_convergence(self):
        config = load_config()
        args = _make_args(live=False)
        result = apply_cli_overrides(config, args)
        assert result.dsp.burst.noise_floor_window_sec == 0.5
        assert result.dsp.burst.min_burst_duration_ms == 1.0

    def test_live_mode_no_fast_convergence(self):
        config = load_config()
        original_window = config.dsp.burst.noise_floor_window_sec
        args = _make_args(live=True)
        result = apply_cli_overrides(config, args)
        assert result.dsp.burst.noise_floor_window_sec == original_window


# ---------------------------------------------------------------------------
# BenchResults
# ---------------------------------------------------------------------------

def _event(*, freq_hz: float = 2.437e9, snr_db: float = 25.0) -> RFEvent:
    return RFEvent(
        event_id="evt-1",
        role=ChannelRole.OMNI,
        start_time_s=0.0,
        end_time_s=0.001,
        center_freq_hz=freq_hz,
        bandwidth_hz=10e6,
        peak_power_dbm=-55.0,
        snr_db=snr_db,
        family=SignalFamily.OFDM,
    )


class TestBenchResults:
    def test_record_frame(self):
        r = BenchResults()
        r.record_frame(-85.0)
        r.record_frame(-84.5)
        assert r.frame_count == 2
        assert len(r.noise_floors) == 2

    def test_record_event(self):
        r = BenchResults()
        r.record_event(_event(), frame=5)
        assert len(r.events) == 1
        assert r.events[0]["frame"] == 5
        assert r.events[0]["family"] == "ofdm"

    def test_record_iq(self):
        r = BenchResults()
        r.record_iq(np.zeros(1024, dtype=np.complex64))
        assert len(r.iq_buffer) == 1


# ---------------------------------------------------------------------------
# compute_report
# ---------------------------------------------------------------------------

class TestComputeReport:
    def test_report_structure(self):
        r = BenchResults(warmup_frames=10)
        for _ in range(20):
            r.record_frame(-85.0 + np.random.randn() * 0.5)

        args = _make_args()
        report = compute_report(r, load_config(), args, elapsed_sec=5.0)

        assert report["test"] == "bench_test"
        assert report["frames_collected"] == 20
        assert report["warmup_frames"] == 10
        assert report["detection_count"] == 0
        assert report["detection_rate"] == 0.0
        assert report["noise_floor_dbm"] is not None

    def test_report_with_events(self):
        r = BenchResults(warmup_frames=2)
        for _ in range(10):
            r.record_frame(-85.0)
        r.record_event(_event(snr_db=30.0), frame=5)

        report = compute_report(r, load_config(), _make_args(), elapsed_sec=2.0)
        assert report["detection_count"] == 1
        assert report["snr_db"]["mean"] == 30.0

    def test_report_freq_accuracy(self):
        r = BenchResults(warmup_frames=0)
        for _ in range(5):
            r.record_frame(-85.0)
        r.record_event(_event(freq_hz=2.437e9), frame=1)

        report = compute_report(
            r, load_config(),
            _make_args(expect_freq=2.437e9, freq_tolerance=1e6),
            elapsed_sec=1.0,
        )
        assert "freq_accuracy" in report
        assert report["freq_accuracy"]["accuracy_pct"] == 100.0
        assert report["freq_accuracy"]["mean_error_hz"] == 0.0
