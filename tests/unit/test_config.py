"""Tests for src.sdr.config."""

import dataclasses

import pytest

from src.sdr.config import DSPConfig, RxChannelConfig, SDRConfig, SentinelConfig, load_config


class TestLoadConfig:
    """Tests that config.yaml loads into correct types and values."""

    def test_loads_sentinel_config(self):
        cfg = load_config()
        assert isinstance(cfg, SentinelConfig)

    def test_sdr_fields(self):
        cfg = load_config()
        assert cfg.sdr.device == "b210"
        assert cfg.sdr.driver == "uhd"

    def test_rx_a_channel(self):
        cfg = load_config()
        rx = cfg.sdr.rx_a
        assert isinstance(rx, RxChannelConfig)
        assert rx.center_freq_hz == 2.437e9
        assert rx.sample_rate_hz == 30.72e6
        assert rx.gain_db == 40
        assert rx.agc is False

    def test_rx_b_channel(self):
        cfg = load_config()
        rx = cfg.sdr.rx_b
        assert rx.antenna == "TX/RX"
        assert rx.bandwidth_hz == 30.72e6

    def test_dsp_fields(self):
        cfg = load_config()
        assert isinstance(cfg.dsp, DSPConfig)
        assert cfg.dsp.fft_size == 2048
        assert cfg.dsp.window == "hann"
        assert cfg.dsp.overlap == 0.5
        assert cfg.dsp.dc_offset_window == 1024


class TestFrozenDataclasses:
    """Frozen dataclasses should reject attribute mutation."""

    def test_sentinel_config_frozen(self):
        cfg = load_config()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.sdr = None  # type: ignore[misc]

    def test_sdr_config_frozen(self):
        cfg = load_config()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.sdr.device = "hackrf"  # type: ignore[misc]

    def test_rx_channel_frozen(self):
        cfg = load_config()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.sdr.rx_a.gain_db = 99  # type: ignore[misc]

    def test_dsp_config_frozen(self):
        cfg = load_config()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.dsp.fft_size = 512  # type: ignore[misc]


class TestBadPath:
    """Missing config file should raise FileNotFoundError."""

    def test_nonexistent_path(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")
