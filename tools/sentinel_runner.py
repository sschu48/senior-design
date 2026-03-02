"""SENTINEL demo pipeline runner.

Runs the full IQ → detect → antenna → log pipeline.  Supports both
synthetic signals (default) and live USRP B210 hardware (--live).

Usage:
    python -m tools.sentinel_runner [--config CONFIG] [--frames N] [--headless]
    python -m tools.sentinel_runner --live [--device ARGS] [--frames N]
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import sys
import time

from src.antenna.controller import SimulatedController
from src.pipeline.engine import PipelineEngine
from src.sdr.capture import SignalDef, SyntheticSource, USRPSource
from src.sdr.config import load_config


# ---------------------------------------------------------------------------
# Synthetic drone signals
# ---------------------------------------------------------------------------

# DJI OcuSync-like: wideband OFDM centered +5 MHz from center freq
DJI_SIGNAL = SignalDef(
    freq_offset_hz=5e6,
    bandwidth_hz=10e6,
    power_dbm=-55.0,
    signal_type="wideband",
    num_subcarriers=128,
)

# RC control link: narrow tone at -8 MHz
RC_TONE = SignalDef(
    freq_offset_hz=-8e6,
    bandwidth_hz=0.0,
    power_dbm=-60.0,
    signal_type="tone",
)

# ELRS-like: narrow spread spectrum at +12 MHz
ELRS_SIGNAL = SignalDef(
    freq_offset_hz=12e6,
    bandwidth_hz=500e3,
    power_dbm=-65.0,
    signal_type="wideband",
    num_subcarriers=16,
)


def build_source(config) -> SyntheticSource:
    """Create a SyntheticSource with drone-like signals."""
    return SyntheticSource(
        sample_rate_hz=config.sdr.rx_a.sample_rate_hz,
        noise_power_dbm=-90.0,
        signals=[DJI_SIGNAL, RC_TONE, ELRS_SIGNAL],
        seed=42,
    )


def build_antenna(config) -> SimulatedController:
    """Create a SimulatedController from config."""
    mount = config.antenna.mount
    scan = config.scan
    return SimulatedController(
        azimuth_min_deg=mount.azimuth_min_deg,
        azimuth_max_deg=mount.azimuth_max_deg,
        slew_rate_deg_per_sec=mount.azimuth_speed_deg_per_sec,
        scan_speed_deg_per_sec=scan.scan_speed_deg_per_sec,
        elevation_deg=mount.elevation_deg,
        cue_timeout_sec=scan.cue_timeout_sec,
        track_oscillation_deg=scan.track_oscillation_deg,
        track_lost_timeout_sec=scan.track_lost_timeout_sec,
    )


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(level: str = "INFO") -> None:
    """Configure structured JSON logging to stderr."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger("sentinel")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(handler)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _synthetic_config_overrides(config):
    """Override config values for fast convergence with synthetic data.

    Production config is tuned for real-time at ~15k fps. For synthetic
    mode we process frames back-to-back, so we use:
    - Faster noise floor convergence (0.5s window vs 10s)
    - Lower tripwire trigger duration (10ms vs 50ms)
    - Lower CFAR min BW (30kHz vs 100kHz) — tones are valid test signals
    """
    dsp = config.dsp
    new_tripwire = dataclasses.replace(
        dsp.tripwire,
        noise_floor_window_sec=0.5,
        min_trigger_duration_ms=10,
    )
    new_cfar = dataclasses.replace(
        dsp.cfar,
        min_detection_bw_hz=30e3,
    )
    new_dsp = dataclasses.replace(dsp, tripwire=new_tripwire, cfar=new_cfar)
    return dataclasses.replace(config, dsp=new_dsp)


async def run_pipeline(
    config_path: str | None,
    max_frames: int,
    headless: bool,
    live: bool = False,
    device_args: str = "",
) -> None:
    """Run the detection pipeline."""
    config = load_config(config_path)

    if live:
        # Live mode: use production config, USRP hardware source
        setup_logging(config.system.log_level)
        source = USRPSource(
            channel_config=config.sdr.rx_a,
            device_args=device_args,
            channel=0,
        )
        mode_label = "LIVE (USRP B210)"
        signal_label = f"device={device_args or 'auto-detect'}"
    else:
        # Synthetic mode: override config for fast convergence
        config = _synthetic_config_overrides(config)
        setup_logging(config.system.log_level)
        source = build_source(config)
        mode_label = "Synthetic Mode"
        signal_label = "DJI wideband, RC tone, ELRS narrowband"

    antenna = build_antenna(config)
    engine = PipelineEngine(config=config, source=source, antenna=antenna)

    print("=" * 60)
    print(f"SENTINEL — Detection Pipeline ({mode_label})")
    print("=" * 60)
    print(f"  Sample rate:  {config.sdr.rx_a.sample_rate_hz / 1e6:.2f} MSPS")
    print(f"  Center freq:  {config.sdr.rx_a.center_freq_hz / 1e9:.3f} GHz")
    print(f"  FFT size:     {config.dsp.fft_size}")
    print(f"  Signals:      {signal_label}")
    print(f"  Max frames:   {max_frames if max_frames > 0 else 'unlimited'}")
    print("=" * 60)

    await engine.start()

    start_time = time.monotonic()

    try:
        if max_frames > 0:
            await engine.run(max_frames=max_frames)
        else:
            # Run until interrupted
            while engine.running:
                detections = await engine.process_one_frame()

                if not headless and detections:
                    for d in detections:
                        print(json.dumps({
                            "frame": engine.frame_count,
                            "freq_mhz": round(d.freq_hz / 1e6, 2),
                            "bw_khz": round(d.bandwidth_hz / 1e3, 1),
                            "power_dbm": round(d.power_dbm, 1),
                            "snr_db": round(d.snr_db, 1),
                            "antenna_az": round(
                                antenna.get_state().azimuth_deg, 1
                            ),
                            "antenna_mode": antenna.get_state().mode.value,
                        }))

                await asyncio.sleep(0)
    except KeyboardInterrupt:
        pass
    finally:
        elapsed = time.monotonic() - start_time
        await engine.stop()

    print("=" * 60)
    print(f"  Frames:       {engine.frame_count}")
    print(f"  Detections:   {engine.detection_count}")
    print(f"  Elapsed:      {elapsed:.2f}s")
    if elapsed > 0:
        print(f"  Frame rate:   {engine.frame_count / elapsed:.0f} fps")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="SENTINEL demo pipeline runner")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--frames", type=int, default=100, help="Max frames (0=unlimited)")
    parser.add_argument("--headless", action="store_true", help="Suppress per-detection output")
    parser.add_argument("--live", action="store_true", help="Use USRP B210 hardware instead of synthetic source")
    parser.add_argument("--device", default="", help="UHD device args (e.g. serial=31E345B)")
    args = parser.parse_args()

    asyncio.run(run_pipeline(args.config, args.frames, args.headless, args.live, args.device))


if __name__ == "__main__":
    main()
