"""Tests for src.pipeline.engine.PipelineEngine."""

import pytest

from src.antenna.controller import ScanMode, SimulatedController
from src.pipeline.engine import PipelineEngine
from src.sdr.capture import SignalDef, SyntheticSource
from src.sdr.config import (
    AntennaConfig,
    CaptureConfig,
    CFARConfig,
    DSPConfig,
    DecoderConfig,
    DetectionConfig,
    DJIDroneIDDecoderConfig,
    MountConfig,
    OmniConfig,
    RemoteIDDecoderConfig,
    RxChannelConfig,
    ScanConfig,
    SDRConfig,
    SentinelConfig,
    ServerConfig,
    SystemConfig,
    TripwireConfig,
    YagiConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_test_config() -> SentinelConfig:
    """Build a SentinelConfig tuned for fast synthetic-source testing.

    Key differences from production config:
    - CFAR min_detection_bw_hz = 0 (no BW filter — tones are valid detections)
    - Tripwire min_trigger_duration_ms = 0 (no duration gating for speed)
    - Tripwire noise_floor_window_sec = 0.1 (fast noise floor convergence)
    """
    rx = RxChannelConfig(
        antenna="RX2",
        center_freq_hz=2.437e9,
        sample_rate_hz=30.72e6,
        bandwidth_hz=30.72e6,
        gain_db=40.0,
        agc=False,
    )
    return SentinelConfig(
        system=SystemConfig(
            name="SENTINEL", version="0.1.0",
            log_level="WARNING", log_format="json",
            log_file="logs/sentinel.jsonl",
        ),
        sdr=SDRConfig(device="b210", driver="uhd", rx_a=rx, rx_b=rx),
        dsp=DSPConfig(
            fft_size=2048, window="hann", overlap=0.5, dc_offset_window=1024,
            tripwire=TripwireConfig(
                threshold_db=10.0,
                noise_floor_window_sec=0.1,
                min_trigger_duration_ms=10,  # require 2+ consecutive frames
            ),
            cfar=CFARConfig(
                type="CA-CFAR", guard_cells=4, reference_cells=16,
                threshold_factor_db=12.0,
                min_detection_bw_hz=30e3,  # reject single-bin noise spikes
            ),
            decoder=DecoderConfig(
                remote_id=RemoteIDDecoderConfig(enabled=False, channel=6),
                dji_droneid=DJIDroneIDDecoderConfig(
                    enabled=False, min_sample_rate_hz=15.36e6, sync_threshold=0.7,
                ),
            ),
        ),
        antenna=AntennaConfig(
            yagi=YagiConfig(gain_dbi=12.0, beamwidth_deg=45.0,
                            polarization="horizontal", connector="rp-sma",
                            max_input_power_w=50.0),
            omni=OmniConfig(gain_dbi=2.0, type="vertical_dipole"),
            mount=MountConfig(
                type="servo", azimuth_min_deg=0.0, azimuth_max_deg=360.0,
                azimuth_speed_deg_per_sec=30.0, elevation_enabled=False,
                elevation_deg=10.0, control_interface="serial",
                serial_port="/dev/ttyUSB0", serial_baud=115200,
            ),
        ),
        scan=ScanConfig(
            default_mode="SCAN", scan_speed_deg_per_sec=30.0,
            cue_timeout_sec=5.0, track_oscillation_deg=15.0,
            track_lost_timeout_sec=10.0,
        ),
        detection=DetectionConfig(
            min_snr_db=10.0, min_confidence=0.3,
            bearing_exclusion_zones=[],
        ),
        capture=CaptureConfig(
            enabled=False, format="cf32",
            pre_trigger_sec=1.0, post_trigger_sec=3.0,
            output_dir="data/samples", sigmf_metadata=True,
        ),
        server=ServerConfig(host="0.0.0.0", port=3000, websocket_path="/ws"),
    )


def make_source(signals=None, noise_dbm=-90.0, seed=0):
    """Create a SyntheticSource with optional signals."""
    return SyntheticSource(
        sample_rate_hz=30.72e6,
        noise_power_dbm=noise_dbm,
        signals=signals or [],
        seed=seed,
    )


def make_antenna():
    """Create a SimulatedController with test defaults."""
    return SimulatedController(
        azimuth_min_deg=0.0,
        azimuth_max_deg=360.0,
        slew_rate_deg_per_sec=60.0,
        scan_speed_deg_per_sec=30.0,
        elevation_deg=10.0,
        cue_timeout_sec=5.0,
        track_oscillation_deg=15.0,
        track_lost_timeout_sec=10.0,
    )


# ===========================================================================
# Start / stop
# ===========================================================================

class TestStartStop:
    """Pipeline should start and stop cleanly."""

    async def test_start_stop(self):
        cfg = make_test_config()
        source = make_source()
        engine = PipelineEngine(config=cfg, source=source)

        await engine.start()
        assert engine.running is True
        assert engine.frame_count == 0

        await engine.stop()
        assert engine.running is False

    async def test_start_stop_with_antenna(self):
        cfg = make_test_config()
        source = make_source()
        antenna = make_antenna()
        engine = PipelineEngine(config=cfg, source=source, antenna=antenna)

        await engine.start()
        assert engine.running is True
        assert antenna.get_state().mode == ScanMode.SCAN

        await engine.stop()
        assert antenna.get_state().mode == ScanMode.IDLE


# ===========================================================================
# Frame processing
# ===========================================================================

class TestFrameProcessing:
    """Pipeline should process frames and count them."""

    async def test_processes_frames(self):
        cfg = make_test_config()
        source = make_source(seed=42)
        engine = PipelineEngine(config=cfg, source=source)

        await engine.start()
        await engine.run(max_frames=10)
        await engine.stop()

        assert engine.frame_count == 10


# ===========================================================================
# Detection
# ===========================================================================

class TestDetection:
    """Pipeline should detect injected signals."""

    async def test_detects_tone(self):
        """A strong tone (40 dB above noise) should be detected by CFAR."""
        cfg = make_test_config()
        source = make_source(
            signals=[
                SignalDef(freq_offset_hz=5e6, power_dbm=-50.0, signal_type="tone"),
            ],
            noise_dbm=-90.0,
            seed=1,
        )
        engine = PipelineEngine(config=cfg, source=source)

        await engine.start()
        await engine.run(max_frames=20)
        await engine.stop()

        assert engine.detection_count > 0

    async def test_no_detection_on_noise(self):
        """Pure noise should produce zero or near-zero detections."""
        cfg = make_test_config()
        source = make_source(noise_dbm=-90.0, seed=2)
        engine = PipelineEngine(config=cfg, source=source)

        await engine.start()
        await engine.run(max_frames=20)
        await engine.stop()

        # Allow at most 1 spurious detection (statistical noise)
        assert engine.detection_count <= 1


# ===========================================================================
# Antenna integration
# ===========================================================================

class TestAntennaIntegration:
    """Pipeline should drive antenna mode transitions on detection."""

    async def test_detection_triggers_cue(self):
        """A detection during SCAN should trigger CUE mode."""
        cfg = make_test_config()
        source = make_source(
            signals=[
                SignalDef(freq_offset_hz=5e6, power_dbm=-50.0, signal_type="tone"),
            ],
            noise_dbm=-90.0,
            seed=3,
        )
        antenna = make_antenna()
        engine = PipelineEngine(config=cfg, source=source, antenna=antenna)

        await engine.start()

        saw_cue = False
        for _ in range(30):
            await engine.process_one_frame()
            state = antenna.get_state()
            if state.mode in (ScanMode.CUE, ScanMode.TRACK):
                saw_cue = True
                break

        await engine.stop()
        assert saw_cue, "Expected antenna to transition to CUE or TRACK on detection"

    async def test_runs_without_antenna(self):
        """Pipeline should work fine with antenna=None."""
        cfg = make_test_config()
        source = make_source(
            signals=[
                SignalDef(freq_offset_hz=5e6, power_dbm=-50.0, signal_type="tone"),
            ],
            noise_dbm=-90.0,
            seed=4,
        )
        engine = PipelineEngine(config=cfg, source=source, antenna=None)

        await engine.start()
        await engine.run(max_frames=10)
        await engine.stop()

        assert engine.frame_count == 10
