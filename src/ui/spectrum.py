"""SENTINEL — Live Spectrum Analyzer.

Real-time PSD display using matplotlib FuncAnimation.
Point the antenna at a WiFi AP, watch the PSD light up.

Usage:
    python -m src.ui.spectrum              # synthetic signals
    python -m src.ui.spectrum --live       # live USRP B210
    python -m src.ui.spectrum --fps 30     # faster update rate
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import threading

import os

import matplotlib

# Select an interactive backend.
# Desktop backends need a display server (X11/Wayland).
# WebAgg works everywhere — serves the plot at http://localhost:8988.
_HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

_BACKEND = None
if _HAS_DISPLAY:
    for _be in ("Qt5Agg", "TkAgg", "GTK3Agg"):
        try:
            matplotlib.use(_be)
            _BACKEND = _be
            break
        except ImportError:
            continue

if _BACKEND is None:
    # No display or no desktop backend — use WebAgg (browser-based)
    matplotlib.use("WebAgg")
    _BACKEND = "WebAgg"
    matplotlib.rcParams["webagg.open_in_browser"] = False
    matplotlib.rcParams["webagg.port"] = 8988
    matplotlib.rcParams["webagg.address"] = "0.0.0.0"  # bind all interfaces (Tailscale, etc.)

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.animation import FuncAnimation  # noqa: E402

from src.dsp.spectrum import compute_psd_from_config
from src.sdr.capture import IQSource, SyntheticSource, USRPSource
from src.sdr.config import load_config
from src.sdr.signals import DEFAULT_SIGNALS


# ---------------------------------------------------------------------------
# Spectrum display
# ---------------------------------------------------------------------------

class SpectrumDisplay:
    """Real-time PSD display driven by matplotlib FuncAnimation."""

    def __init__(
        self,
        source: IQSource,
        config,
        fps: int = 15,
    ) -> None:
        self.source = source
        self.config = config
        self.fps = fps
        self.frame_count = 0

        # Samples per frame = one FFT block
        self.num_samples = config.dsp.fft_size

        # Async event loop runs in a background thread so it doesn't
        # conflict with WebAgg's tornado event loop on the main thread.
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True
        )

    def _run_async(self, coro):
        """Submit a coroutine to the background loop and wait for the result."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def _read_iq(self) -> np.ndarray:
        """Synchronous wrapper around the async IQSource.read()."""
        return self._run_async(self.source.read(self.num_samples))

    def _start_source(self) -> None:
        """Start the background event loop thread, then start the IQ source."""
        self._loop_thread.start()
        self._run_async(self.source.start())

    def _stop_source(self) -> None:
        """Stop the IQ source and shut down the background event loop."""
        self._run_async(self.source.stop())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=2)

    def run(self) -> None:
        """Launch the matplotlib window and block until closed."""
        if _BACKEND == "WebAgg":
            port = matplotlib.rcParams.get("webagg.port", 8988)
            print(f"  Open in browser: http://localhost:{port}")
            # Show Tailscale IP if available
            try:
                import subprocess
                ts_ip = subprocess.check_output(
                    ["tailscale", "ip", "-4"], stderr=subprocess.DEVNULL
                ).decode().strip()
                print(f"  Tailscale URL:   http://{ts_ip}:{port}")
            except Exception:
                pass

        self._start_source()

        # Initial PSD to set up the plot
        iq = self._read_iq()
        freq_hz, power_dbm = compute_psd_from_config(iq, self.config)
        freq_mhz = freq_hz / 1e6

        # Set up figure
        self.fig, self.ax = plt.subplots(figsize=(12, 6))
        if hasattr(self.fig.canvas.manager, "set_window_title"):
            self.fig.canvas.manager.set_window_title("SENTINEL — Spectrum Analyzer")

        (self.line,) = self.ax.plot(freq_mhz, power_dbm, color="#00ff88", linewidth=0.8)

        self.ax.set_xlabel("Frequency (MHz)")
        self.ax.set_ylabel("Power (dBm)")
        self.ax.set_xlim(freq_mhz[0], freq_mhz[-1])
        self.ax.set_ylim(-100, -20)
        self.ax.grid(True, alpha=0.3)
        self.ax.set_facecolor("#1a1a2e")
        self.fig.patch.set_facecolor("#0f0f1a")
        self.ax.tick_params(colors="#cccccc")
        self.ax.xaxis.label.set_color("#cccccc")
        self.ax.yaxis.label.set_color("#cccccc")
        for spine in self.ax.spines.values():
            spine.set_color("#444444")

        center_mhz = self.config.sdr.rx_a.center_freq_hz / 1e6
        rate_msps = self.config.sdr.rx_a.sample_rate_hz / 1e6
        self.title = self.ax.set_title(
            f"Center: {center_mhz:.1f} MHz | BW: {rate_msps:.1f} MSPS | Frame 0",
            color="#cccccc",
            fontsize=11,
        )

        self.frame_count = 1

        interval_ms = max(1, int(1000 / self.fps))
        anim = FuncAnimation(
            self.fig,
            self._update,
            interval=interval_ms,
            blit=False,
            cache_frame_data=False,
        )
        # Store on figure to prevent garbage collection
        self.fig._sentinel_anim = anim

        try:
            plt.tight_layout()
            plt.show(block=True)
        finally:
            self._stop_source()
            self._loop.close()

    def _update(self, _frame) -> None:
        """Animation callback — fetch IQ, compute PSD, update plot."""
        try:
            iq = self._read_iq()
        except Exception as exc:
            print(f"Read error: {exc}", file=sys.stderr)
            return

        freq_hz, power_dbm = compute_psd_from_config(iq, self.config)

        self.line.set_ydata(power_dbm)

        # Update title with peak info
        peak_idx = np.argmax(power_dbm)
        peak_freq_mhz = freq_hz[peak_idx] / 1e6
        peak_power = power_dbm[peak_idx]

        center_mhz = self.config.sdr.rx_a.center_freq_hz / 1e6
        rate_msps = self.config.sdr.rx_a.sample_rate_hz / 1e6
        self.title.set_text(
            f"Center: {center_mhz:.1f} MHz | BW: {rate_msps:.1f} MSPS | "
            f"Frame {self.frame_count} | Peak: {peak_freq_mhz:.2f} MHz @ {peak_power:.1f} dBm"
        )

        self.frame_count += 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="SENTINEL — Live Spectrum Analyzer",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Use USRP B210 hardware (default: synthetic signals)",
    )
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--device", default="", help="UHD device args (e.g. serial=31E345B)")
    parser.add_argument("--fps", type=int, default=15, help="Target update rate (default: 15)")
    args = parser.parse_args()

    config = load_config(args.config)

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
            signals=DEFAULT_SIGNALS,
            seed=42,
        )
        mode = "Synthetic"

    print(f"SENTINEL Spectrum Analyzer — {mode}")
    print(f"  Center: {config.sdr.rx_a.center_freq_hz / 1e6:.1f} MHz")
    print(f"  Rate:   {config.sdr.rx_a.sample_rate_hz / 1e6:.2f} MSPS")
    print(f"  FFT:    {config.dsp.fft_size}")
    print(f"  FPS:    {args.fps}")

    display = SpectrumDisplay(source=source, config=config, fps=args.fps)
    display.run()


if __name__ == "__main__":
    main()
