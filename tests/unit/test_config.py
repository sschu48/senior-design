"""Tests for src.sdr.config."""

import dataclasses

import pytest

from src.sdr.config import (
    AntennaConfig,
    CaptureConfig,
    CFARConfig,
    DSPConfig,
    DecoderConfig,
    DetectionConfig,
    MountConfig,
    OmniConfig,
    RxChannelConfig,
    ScanConfig,
    SDRConfig,
    SentinelConfig,
    ServerConfig,
    SystemConfig,
    TripwireConfig,
    YagiConfig,
    load_config,
)


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


class TestSystemConfig:
    """Tests for the system section."""

    def test_system_fields(self):
        cfg = load_config()
        assert isinstance(cfg.system, SystemConfig)
        assert cfg.system.name == "SENTINEL"
        assert cfg.system.version == "0.1.0"
        assert cfg.system.log_level == "INFO"
        assert cfg.system.log_format == "json"
        assert cfg.system.log_file == "logs/sentinel.jsonl"


class TestTripwireConfig:
    """Tests for dsp.tripwire section."""

    def test_tripwire_fields(self):
        cfg = load_config()
        tw = cfg.dsp.tripwire
        assert isinstance(tw, TripwireConfig)
        assert tw.threshold_db == 10
        assert tw.noise_floor_window_sec == 10
        assert tw.min_trigger_duration_ms == 50


class TestCFARConfig:
    """Tests for dsp.cfar section."""

    def test_cfar_fields(self):
        cfg = load_config()
        c = cfg.dsp.cfar
        assert isinstance(c, CFARConfig)
        assert c.type == "CA-CFAR"
        assert c.guard_cells == 4
        assert c.reference_cells == 16
        assert c.threshold_factor_db == 10
        assert c.min_detection_bw_hz == 100e3


class TestDecoderConfig:
    """Tests for dsp.decoder section."""

    def test_decoder_fields(self):
        cfg = load_config()
        d = cfg.dsp.decoder
        assert isinstance(d, DecoderConfig)
        assert d.remote_id.enabled is True
        assert d.remote_id.channel == 6
        assert d.dji_droneid.enabled is True
        assert d.dji_droneid.min_sample_rate_hz == 15.36e6
        assert d.dji_droneid.sync_threshold == 0.7


class TestAntennaConfig:
    """Tests for antenna section (yagi, omni, mount)."""

    def test_yagi_fields(self):
        cfg = load_config()
        y = cfg.antenna.yagi
        assert isinstance(y, YagiConfig)
        assert y.gain_dbi == 12
        assert y.beamwidth_deg == 45
        assert y.polarization == "horizontal"

    def test_omni_fields(self):
        cfg = load_config()
        o = cfg.antenna.omni
        assert isinstance(o, OmniConfig)
        assert o.gain_dbi == 2
        assert o.type == "vertical_dipole"

    def test_mount_fields(self):
        cfg = load_config()
        m = cfg.antenna.mount
        assert isinstance(m, MountConfig)
        assert m.type == "servo"
        assert m.azimuth_min_deg == 0
        assert m.azimuth_max_deg == 360
        assert m.azimuth_speed_deg_per_sec == 30
        assert m.elevation_enabled is False
        assert m.elevation_deg == 10
        assert m.serial_baud == 115200


class TestScanConfig:
    """Tests for scan section."""

    def test_scan_fields(self):
        cfg = load_config()
        s = cfg.scan
        assert isinstance(s, ScanConfig)
        assert s.default_mode == "SCAN"
        assert s.scan_speed_deg_per_sec == 30
        assert s.cue_timeout_sec == 5
        assert s.track_oscillation_deg == 15
        assert s.track_lost_timeout_sec == 10


class TestDetectionConfig:
    """Tests for detection section."""

    def test_detection_fields(self):
        cfg = load_config()
        d = cfg.detection
        assert isinstance(d, DetectionConfig)
        assert d.min_snr_db == 10
        assert d.min_confidence == 0.3
        assert d.bearing_exclusion_zones == []


class TestCaptureConfig:
    """Tests for capture section."""

    def test_capture_fields(self):
        cfg = load_config()
        c = cfg.capture
        assert isinstance(c, CaptureConfig)
        assert c.enabled is True
        assert c.format == "cf32"
        assert c.pre_trigger_sec == 1.0
        assert c.post_trigger_sec == 3.0
        assert c.output_dir == "data/samples"
        assert c.sigmf_metadata is True


class TestServerConfig:
    """Tests for server section."""

    def test_server_fields(self):
        cfg = load_config()
        s = cfg.server
        assert isinstance(s, ServerConfig)
        assert s.host == "0.0.0.0"
        assert s.port == 3000
        assert s.websocket_path == "/ws"


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

    def test_system_config_frozen(self):
        cfg = load_config()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.system.name = "OTHER"  # type: ignore[misc]

    def test_tripwire_config_frozen(self):
        cfg = load_config()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.dsp.tripwire.threshold_db = 99  # type: ignore[misc]

    def test_antenna_config_frozen(self):
        cfg = load_config()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.antenna.yagi.gain_dbi = 99  # type: ignore[misc]

    def test_scan_config_frozen(self):
        cfg = load_config()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.scan.cue_timeout_sec = 99  # type: ignore[misc]


class TestBadPath:
    """Missing config file should raise FileNotFoundError."""

    def test_nonexistent_path(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")
