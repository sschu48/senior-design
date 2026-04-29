"""Tests for the HackRF bench receiver harness."""

import argparse

import numpy as np
import pytest

from src.pipeline.contracts import ChannelRole
from src.sdr.config import load_config
from tools.hackrf_bench import (
    ChannelAccumulator,
    BandMeasurement,
    apply_rx_overrides,
    build_source,
    build_tx_command,
    load_bench_profile,
    measure_expected_band,
    run_bench,
)


def _args(**overrides) -> argparse.Namespace:
    defaults = dict(
        config=None,
        profile="tone_2437",
        setup_only=False,
        live=False,
        dual=False,
        device="",
        gain=None,
        freq=None,
        channel=None,
        sample_rate=None,
        bandwidth=None,
        fft_size=None,
        signal_bw=None,
        expect_freq=None,
        min_snr=8.0,
        cfar_threshold=None,
        min_detection_bw=None,
        duration=None,
        frames=8,
        warmup=0.0,
        warmup_frames=0,
        save_iq=False,
        output=None,
        tx_gain=0,
        rx_distance=3.0,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_load_tone_profile_uses_measurement_bandwidth():
    profile = load_bench_profile("tone_2437")
    assert profile.center_freq_hz == 2.437e9
    assert profile.measurement_bw_hz == 200e3
    assert profile.is_bursty is False


def test_load_bursty_profile_marks_bursty():
    profile = load_bench_profile("dji_droneid")
    assert profile.is_bursty is True
    assert profile.default_capture_sec == 5.0


def test_tx_command_matches_existing_hackrf_runner():
    cmd = build_tx_command("tone_2437", tx_gain_db=0, rx_distance_m=3.0)
    assert cmd == (
        "python -m tools.hackrf_tx --profile tone_2437 --gain 0 --rx-distance 3"
    )


def test_apply_rx_overrides_tunes_both_channels_to_profile():
    config = load_config()
    profile = load_bench_profile("tone_2437")
    result = apply_rx_overrides(config, _args(gain=12.0), profile)

    assert result.sdr.rx_a.center_freq_hz == 2.436e9
    assert result.sdr.rx_b.center_freq_hz == 2.436e9
    assert result.sdr.rx_a.gain_db == 12.0
    assert result.sdr.rx_b.gain_db == 12.0
    assert result.dsp.cfar.min_detection_bw_hz == 0.0


def test_apply_rx_overrides_channel_shortcut():
    config = load_config()
    profile = load_bench_profile("tone_2437")
    result = apply_rx_overrides(config, _args(channel=1), profile)

    assert result.sdr.rx_a.center_freq_hz == 2.412e9
    assert result.sdr.rx_b.center_freq_hz == 2.412e9


def test_apply_rx_overrides_freq_override_wins_over_tone_offset():
    config = load_config()
    profile = load_bench_profile("tone_2437")
    result = apply_rx_overrides(config, _args(freq=2.437e9), profile)

    assert result.sdr.rx_a.center_freq_hz == 2.437e9
    assert result.sdr.rx_b.center_freq_hz == 2.437e9


def test_measure_expected_band_finds_peak_and_snr():
    center = 2.437e9
    freq = np.linspace(center - 1e6, center + 1e6, 101)
    power = np.full(101, -90.0)
    power[50] = -50.0

    measurement = measure_expected_band(
        freq_hz=freq,
        power_dbm=power,
        expected_freq_hz=center,
        measurement_bw_hz=200e3,
    )

    assert measurement.peak_freq_hz == center
    assert measurement.peak_power_dbm == -50.0
    assert measurement.noise_floor_dbm == -90.0
    assert measurement.snr_db == 40.0
    assert measurement.freq_error_hz == 0.0


def test_measure_expected_band_rejects_out_of_range_signal():
    freq = np.linspace(2.436e9, 2.438e9, 101)
    power = np.full(101, -90.0)

    with pytest.raises(ValueError, match="does not overlap"):
        measure_expected_band(
            freq_hz=freq,
            power_dbm=power,
            expected_freq_hz=2.500e9,
            measurement_bw_hz=200e3,
        )


def test_channel_accumulator_summary_reports_presence_rate():
    acc = ChannelAccumulator(role=ChannelRole.YAGI, min_snr_db=8.0)
    acc.record_measurement(
        BandMeasurement(
            peak_freq_hz=2.437e9,
            peak_power_dbm=-50.0,
            noise_floor_dbm=-90.0,
            snr_db=40.0,
            freq_error_hz=0.0,
        )
    )
    acc.record_measurement(
        BandMeasurement(
            peak_freq_hz=2.437e9,
            peak_power_dbm=-88.0,
            noise_floor_dbm=-90.0,
            snr_db=2.0,
            freq_error_hz=0.0,
        )
    )

    summary = acc.summary()
    assert summary["frames"] == 2
    assert summary["frames_above_min_snr"] == 1
    assert summary["signal_presence_rate"] == 0.5
    assert summary["snr_db"]["max"] == 40.0


async def test_run_bench_synthetic_dual_tone(tmp_path):
    config = load_config()
    profile = load_bench_profile("tone_2437")
    args = _args(dual=True, frames=6, warmup_frames=0)
    config = apply_rx_overrides(config, args, profile)
    source = build_source(config, args, profile)

    report = await run_bench(
        config=config,
        source=source,
        args=args,
        profile=profile,
        output_path=tmp_path / "hackrf_bench_tone.json",
    )

    assert report["test"] == "hackrf_bench"
    assert report["dual_rx"] is True
    assert report["pass"]["passed"] is True
    assert report["channels"]["omni"]["frames"] == 6
    assert report["channels"]["yagi"]["frames"] == 6
    assert report["dual_agreement"] is not None
