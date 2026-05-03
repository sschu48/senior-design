"""SENTINEL V2 pipeline runner.

Runs the full V2 detection pipeline (Source → Spectrogram → Burst → Cluster
→ Classify → Bearing → Fuse → Track) end-to-end. Supports synthetic dual-RX
signals (default) and live USRP B210 dual-channel hardware (``--live``).

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
from src.pipeline.pipeline import Pipeline
from src.sdr.capture import (
    DualIQSource,
    SyntheticDualSource,
    USRPDualSource,
)
from src.sdr.config import SentinelConfig, load_config
from src.sdr.signals import DEFAULT_SIGNALS


# ---------------------------------------------------------------------------
# Source / antenna builders
# ---------------------------------------------------------------------------

def build_dual_source(config: SentinelConfig) -> SyntheticDualSource:
    return SyntheticDualSource(
        sample_rate_hz=config.sdr.rx_a.sample_rate_hz,
        center_freq_hz=config.sdr.rx_a.center_freq_hz,
        omni_center_freq_hz=config.sdr.rx_a.center_freq_hz,
        yagi_center_freq_hz=config.sdr.rx_b.center_freq_hz,
        omni_noise_power_dbm=-90.0,
        yagi_noise_power_dbm=-90.0,
        omni_signals=DEFAULT_SIGNALS,
        yagi_signals=DEFAULT_SIGNALS,
        seed=42,
    )


def build_antenna(config: SentinelConfig) -> SimulatedController:
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
# Logging
# ---------------------------------------------------------------------------

def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger("sentinel")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not root.handlers:
        root.addHandler(handler)


def _synthetic_overrides(config: SentinelConfig) -> SentinelConfig:
    """Tighter burst gating for back-to-back synthetic frames."""
    burst = dataclasses.replace(
        config.dsp.burst,
        noise_floor_window_sec=0.5,
        min_burst_duration_ms=1.0,
    )
    new_dsp = dataclasses.replace(config.dsp, burst=burst)
    return dataclasses.replace(config, dsp=new_dsp)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run_pipeline(
    config_path: str | None,
    max_frames: int,
    headless: bool,
    live: bool = False,
    device_args: str = "",
    omni_only: bool = False,
) -> None:
    config = load_config(config_path)

    if live:
        setup_logging(config.system.log_level)
        source: DualIQSource = USRPDualSource(
            rx_a_config=config.sdr.rx_a,
            rx_b_config=config.sdr.rx_b,
            device_args=device_args,
        )
        mode_label = "LIVE DUAL RX (USRP B210)"
        signal_label = f"device={device_args or 'auto-detect'}"
    else:
        config = _synthetic_overrides(config)
        setup_logging(config.system.log_level)
        source = build_dual_source(config)
        mode_label = "Synthetic Dual RX"
        signal_label = "DJI wideband, RC tone, ELRS narrowband"

    if omni_only:
        mode_label = f"{mode_label} • OMNI-ONLY (tripwire)"
        antenna = None
    else:
        antenna = build_antenna(config)
    pipeline = Pipeline(
        config=config, source=source, antenna=antenna, omni_only=omni_only
    )

    print("=" * 60)
    print(f"SENTINEL — V2 Pipeline ({mode_label})")
    print("=" * 60)
    print(f"  Sample rate: {config.sdr.rx_a.sample_rate_hz / 1e6:.2f} MSPS")
    print(f"  Center freq: {config.sdr.rx_a.center_freq_hz / 1e9:.3f} GHz")
    print(f"  FFT size:    {config.dsp.spectrogram.fft_size}")
    print(f"  Burst thr:   {config.dsp.burst.threshold_db:.1f} dB")
    print(f"  Signals:     {signal_label}")
    print(f"  Max frames:  {max_frames if max_frames > 0 else 'unlimited'}")
    print("=" * 60)

    await pipeline.start()
    start_time = time.monotonic()

    try:
        if max_frames > 0:
            await pipeline.run(max_frames=max_frames)
        else:
            while pipeline.running:
                result = await pipeline.process_one_frame()
                if not headless and result.events:
                    state = antenna.get_state() if antenna is not None else None
                    for ev in result.events:
                        payload = {
                            "frame": pipeline.frame_count,
                            "freq_mhz": round(ev.center_freq_hz / 1e6, 2),
                            "bw_khz": round(ev.bandwidth_hz / 1e3, 1),
                            "power_dbm": round(ev.peak_power_dbm, 1),
                            "snr_db": round(ev.snr_db, 1),
                            "family": ev.family.value,
                            "bearing_deg": ev.bearing_deg,
                        }
                        if state is not None:
                            payload["antenna_az"] = round(state.azimuth_deg, 1)
                            payload["antenna_mode"] = state.mode.value
                        print(json.dumps(payload))
                await asyncio.sleep(0)
    except KeyboardInterrupt:
        pass
    finally:
        elapsed = time.monotonic() - start_time
        await pipeline.stop()

    print("=" * 60)
    print(f"  Frames:      {pipeline.frame_count}")
    print(f"  Events:      {pipeline.event_count}")
    print(f"  Elapsed:     {elapsed:.2f}s")
    if elapsed > 0:
        print(f"  Frame rate:  {pipeline.frame_count / elapsed:.0f} fps")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="SENTINEL V2 pipeline runner")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--frames", type=int, default=100, help="Max frames (0=unlimited)")
    parser.add_argument("--headless", action="store_true", help="Suppress per-event output")
    parser.add_argument("--live", action="store_true", help="Use USRP B210 hardware")
    parser.add_argument("--device", default="", help="UHD device args")
    parser.add_argument(
        "--omni-only",
        action="store_true",
        help="Tripwire mode: skip antenna control + Yagi bearing stages, "
             "emit events from the omni channel only (RX-A).",
    )
    args = parser.parse_args()

    asyncio.run(run_pipeline(
        args.config,
        args.frames,
        args.headless,
        args.live,
        args.device,
        args.omni_only,
    ))


if __name__ == "__main__":
    main()
