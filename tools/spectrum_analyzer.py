#!/usr/bin/env python3
"""SENTINEL live spectrum analyzer.

Displays a real-time PSD plot from a SyntheticSource with demo signals.
Usage: python tools/spectrum_analyzer.py [--config path/to/config.yaml]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation

# Add project root to path so imports work when run directly
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.dsp.spectrum import compute_psd, remove_dc_offset
from src.sdr.capture import SignalDef, SyntheticSource
from src.sdr.config import load_config


# ---------------------------------------------------------------------------
# Demo configuration
# ---------------------------------------------------------------------------

CENTER_FREQ_HZ = 2.437e9       # WiFi Ch 6
SAMPLE_RATE_HZ = 30.72e6       # B210 MIMO rate
FFT_SIZE = 2048
NOISE_POWER_DBM = -90.0
EMA_ALPHA = 0.15               # exponential moving average smoothing
FRAME_SAMPLES = 2 * FFT_SIZE   # samples per animation frame

# Demo signals — what you'd see in a typical 2.4 GHz environment
DEMO_SIGNALS = [
    # WiFi-like 20 MHz wideband centered at +5 MHz offset (~2442 MHz)
    SignalDef(
        freq_offset_hz=5e6,
        bandwidth_hz=20e6,
        power_dbm=-50.0,
        signal_type="wideband",
        num_subcarriers=128,
    ),
    # Narrowband RC link at -8 MHz offset (~2429 MHz)
    SignalDef(
        freq_offset_hz=-8e6,
        bandwidth_hz=0.0,
        power_dbm=-55.0,
        signal_type="tone",
    ),
    # Narrowband FHSS dwell at +12 MHz offset (~2449 MHz)
    SignalDef(
        freq_offset_hz=12e6,
        bandwidth_hz=0.0,
        power_dbm=-60.0,
        signal_type="tone",
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="SENTINEL live spectrum analyzer")
    parser.add_argument("--config", type=str, default=None, help="Path to config.yaml")
    args = parser.parse_args()

    # Load config (used only for display info; demo uses explicit params)
    try:
        config = load_config(args.config)
        fft_size = config.dsp.fft_size
        window = config.dsp.window
    except FileNotFoundError:
        fft_size = FFT_SIZE
        window = "hann"

    # --- Source setup ---
    source = SyntheticSource(
        sample_rate_hz=SAMPLE_RATE_HZ,
        noise_power_dbm=NOISE_POWER_DBM,
        signals=DEMO_SIGNALS,
        seed=42,
    )

    # Start the async source synchronously
    loop = asyncio.new_event_loop()
    loop.run_until_complete(source.start())

    # --- Plot setup (dark theme) ---
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(12, 6))
    fig.canvas.manager.set_window_title("SENTINEL Spectrum Analyzer")

    # Initial empty lines
    (line_inst,) = ax.plot([], [], color="#00ff88", linewidth=0.8, alpha=0.8, label="Instantaneous")
    (line_avg,) = ax.plot([], [], color="#ff8800", linewidth=1.2, alpha=0.9, label="Averaged (EMA)")

    freq_min_mhz = (CENTER_FREQ_HZ - SAMPLE_RATE_HZ / 2) / 1e6
    freq_max_mhz = (CENTER_FREQ_HZ + SAMPLE_RATE_HZ / 2) / 1e6
    ax.set_xlim(freq_min_mhz, freq_max_mhz)
    ax.set_ylim(-110, -30)
    ax.set_xlabel("Frequency (MHz)")
    ax.set_ylabel("Power (dBm)")
    ax.set_title(
        f"Center: {CENTER_FREQ_HZ/1e6:.1f} MHz  |  "
        f"Fs: {SAMPLE_RATE_HZ/1e6:.2f} MHz  |  "
        f"FFT: {fft_size}"
    )
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.3)

    # EMA state
    avg_power: np.ndarray | None = None

    def update(frame: int):
        nonlocal avg_power

        # Read samples from async source
        iq = loop.run_until_complete(source.read(FRAME_SAMPLES))
        iq = remove_dc_offset(iq)

        freq_hz, power_dbm = compute_psd(
            iq,
            sample_rate=SAMPLE_RATE_HZ,
            fft_size=fft_size,
            window=window,
            center_freq=CENTER_FREQ_HZ,
        )
        freq_mhz = freq_hz / 1e6

        # Update EMA
        if avg_power is None:
            avg_power = power_dbm.copy()
        else:
            avg_power = EMA_ALPHA * power_dbm + (1.0 - EMA_ALPHA) * avg_power

        line_inst.set_data(freq_mhz, power_dbm)
        line_avg.set_data(freq_mhz, avg_power)
        return line_inst, line_avg

    # ~15 fps
    anim = FuncAnimation(fig, update, interval=66, blit=True, cache_frame_data=False)  # noqa: F841

    plt.tight_layout()
    plt.show()

    # Cleanup
    loop.run_until_complete(source.stop())
    loop.close()


if __name__ == "__main__":
    main()
