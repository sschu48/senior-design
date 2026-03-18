"""SENTINEL — Web Dashboard Entry Point.

Streams real-time PSD data to a browser via WebSocket.
Works headless over Tailscale — no display server required.

Usage:
    python -m src.ui.dashboard              # synthetic signals
    python -m src.ui.dashboard --live       # live USRP B210
    python -m src.ui.dashboard --fps 30     # faster updates
    python -m src.ui.dashboard --port 8080  # custom port
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess

from src.sdr.capture import IQSource, SignalDef, SyntheticSource, USRPSource
from src.sdr.config import load_config
from src.ui.server import DashboardServer

# ---------------------------------------------------------------------------
# Synthetic signal definitions (same profiles as spectrum.py)
# ---------------------------------------------------------------------------

DJI_SIGNAL = SignalDef(
    freq_offset_hz=5e6,
    bandwidth_hz=10e6,
    power_dbm=-55.0,
    signal_type="wideband",
    num_subcarriers=128,
)

RC_TONE = SignalDef(
    freq_offset_hz=-8e6,
    bandwidth_hz=0.0,
    power_dbm=-60.0,
    signal_type="tone",
)

ELRS_SIGNAL = SignalDef(
    freq_offset_hz=12e6,
    bandwidth_hz=500e3,
    power_dbm=-65.0,
    signal_type="wideband",
    num_subcarriers=16,
)


def _get_tailscale_ip() -> str | None:
    """Return the Tailscale IPv4 address, or None if unavailable."""
    try:
        return subprocess.check_output(
            ["tailscale", "ip", "-4"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SENTINEL — Web Dashboard (Spectrum Analyzer)",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Use USRP B210 hardware (default: synthetic signals)",
    )
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--device", default="", help="UHD device args (e.g. serial=31E345B)")
    parser.add_argument("--fps", type=int, default=None, help="Target update rate (default: from config or 15)")
    parser.add_argument("--port", type=int, default=None, help="Server port (default: from config or 3000)")
    parser.add_argument("--host", default=None, help="Bind address (default: from config or 0.0.0.0)")
    parser.add_argument("--detect", action="store_true",
                        help="Enable real-time detection overlay on spectrum")
    args = parser.parse_args()

    config = load_config(args.config)

    # Resolve server params: CLI flags override config
    host = args.host or config.server.host
    port = args.port or config.server.port
    fps = args.fps or 15

    if args.live:
        source: IQSource = USRPSource(
            channel_config=config.sdr.rx_a,
            device_args=args.device,
            channel=0,
        )
        mode = "LIVE (USRP B210)"
    else:
        source = SyntheticSource(
            sample_rate_hz=config.sdr.rx_a.sample_rate_hz,
            noise_power_dbm=-90.0,
            signals=[DJI_SIGNAL, RC_TONE, ELRS_SIGNAL],
            seed=42,
        )
        mode = "Synthetic"

    # Startup banner
    print()
    print("  ╔══════════════════════════════════════════╗")
    print("  ║  SENTINEL — Web Dashboard                ║")
    print("  ╚══════════════════════════════════════════╝")
    print()
    print(f"  Mode:   {mode}")
    print(f"  Detect: {'ON' if args.detect else 'OFF'}")
    print(f"  Center: {config.sdr.rx_a.center_freq_hz / 1e6:.1f} MHz")
    print(f"  Rate:   {config.sdr.rx_a.sample_rate_hz / 1e6:.2f} MSPS")
    print(f"  FFT:    {config.dsp.fft_size}")
    print(f"  FPS:    {fps}")
    print()
    print(f"  Local:     http://localhost:{port}")

    ts_ip = _get_tailscale_ip()
    if ts_ip:
        print(f"  Tailscale: http://{ts_ip}:{port}")

    print()

    server = DashboardServer(
        source=source,
        config=config,
        fps=fps,
        host=host,
        port=port,
        enable_detections=args.detect,
    )

    asyncio.run(server.start())


if __name__ == "__main__":
    main()
