"""SENTINEL — Bench SNR Sweep

Automated parameter sweep that measures SNR for each combination of
gain, FFT size, and window function. Outputs a CSV log and prints
the best combination.

Requirements:
  - USRP B210 connected
  - ESP32 beacon running FLOOD mode on a fixed channel at fixed position

Usage:
    python -m tools.bench_snr_sweep
    python -m tools.bench_snr_sweep --gains 10,15,20 --fft-sizes 2048,4096
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

from src.dsp.spectrum import compute_psd, remove_dc_offset
from src.sdr.capture import USRPSource
from src.sdr.config import RxChannelConfig, load_config

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_GAINS = [10, 15, 20, 25, 30]
DEFAULT_FFT_SIZES = [1024, 2048, 4096, 8192]
DEFAULT_WINDOWS = ["hann", "hamming", "blackman", "blackmanharris"]
FRAMES_PER_COMBO = 50  # number of PSD frames to average per combination
SETTLE_FRAMES = 5      # frames to discard after gain change


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def measure_snr(
    iq_block: np.ndarray,
    sample_rate: float,
    center_freq: float,
    fft_size: int,
    window: str,
    signal_freq_hz: float,
    signal_bw_hz: float,
) -> dict:
    """Compute average PSD over iq_block and measure SNR.

    Splits iq_block into frames of 2*fft_size (for Welch overlap),
    averages the PSD, then measures peak power in the signal band
    vs median noise outside it.
    """
    frame_len = fft_size * 2
    num_frames = len(iq_block) // frame_len
    if num_frames == 0:
        raise ValueError(f"IQ block too short for fft_size={fft_size}")

    psd_accum = None
    freq_hz = None

    for i in range(num_frames):
        chunk = iq_block[i * frame_len : (i + 1) * frame_len]
        chunk = remove_dc_offset(chunk)
        f, p = compute_psd(
            chunk,
            sample_rate=sample_rate,
            fft_size=fft_size,
            window=window,
            overlap=0.5,
            center_freq=center_freq,
        )
        if psd_accum is None:
            freq_hz = f
            psd_accum = p.copy()
        else:
            psd_accum += p

    power_dbm = psd_accum / num_frames

    # Define signal band: center ± half bandwidth
    sig_lo = signal_freq_hz - signal_bw_hz / 2
    sig_hi = signal_freq_hz + signal_bw_hz / 2

    signal_mask = (freq_hz >= sig_lo) & (freq_hz <= sig_hi)
    noise_mask = ~signal_mask

    # Exclude DC spike region (center ± 0.5 MHz)
    dc_lo = center_freq - 0.5e6
    dc_hi = center_freq + 0.5e6
    dc_mask = (freq_hz >= dc_lo) & (freq_hz <= dc_hi)
    noise_mask = noise_mask & ~dc_mask

    if not np.any(signal_mask):
        peak_power = float("nan")
    else:
        peak_power = float(np.max(power_dbm[signal_mask]))

    if not np.any(noise_mask):
        noise_floor = float("nan")
    else:
        noise_floor = float(np.median(power_dbm[noise_mask]))

    snr = peak_power - noise_floor

    return {
        "peak_power_dbm": round(peak_power, 2),
        "noise_floor_dbm": round(noise_floor, 2),
        "snr_db": round(snr, 2),
        "num_frames_averaged": num_frames,
    }


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

async def run_sweep(
    gains: list[float],
    fft_sizes: list[int],
    windows: list[str],
    signal_freq_hz: float,
    signal_bw_hz: float,
    device_args: str,
    output_path: Path,
) -> list[dict]:
    """Run the full parameter sweep."""

    config = load_config()
    base_rx = config.sdr.rx_a
    sample_rate = base_rx.sample_rate_hz
    center_freq = base_rx.center_freq_hz

    # We need enough IQ for the largest FFT size
    max_fft = max(fft_sizes)
    # Capture enough for FRAMES_PER_COMBO frames at 2x largest FFT
    capture_samples = (max_fft * 2) * (FRAMES_PER_COMBO + SETTLE_FRAMES)

    results = []
    total_combos = len(gains) * len(fft_sizes) * len(windows)
    combo_num = 0

    print(f"\n  Sweep: {len(gains)} gains x {len(fft_sizes)} FFT sizes x {len(windows)} windows = {total_combos} combinations")
    print(f"  Signal band: {signal_freq_hz/1e6:.1f} MHz ± {signal_bw_hz/2/1e6:.1f} MHz")
    print(f"  Frames per combo: {FRAMES_PER_COMBO}")
    print(f"  Output: {output_path}\n")

    for gain in gains:
        # Reconfigure USRP for this gain
        rx_cfg = replace(base_rx, gain_db=gain)
        source = USRPSource(
            channel_config=rx_cfg,
            device_args=device_args,
            channel=0,
        )

        print(f"  --- Gain: {gain} dB ---")
        try:
            await source.start()
        except Exception as e:
            print(f"  [ERROR] Failed to start USRP at gain {gain}: {e}")
            continue

        # Let AGC/gain settle
        try:
            for _ in range(SETTLE_FRAMES):
                await source.read(max_fft * 2)
        except Exception as e:
            print(f"  [ERROR] Settle read failed: {e}")
            await source.stop()
            continue

        # Capture one big IQ block for all FFT/window combos at this gain
        try:
            iq_block = await source.read(capture_samples)
        except Exception as e:
            print(f"  [ERROR] Capture failed: {e}")
            await source.stop()
            continue

        await source.stop()

        # Now run all FFT size / window combos on the captured data
        for fft_size in fft_sizes:
            for window in windows:
                combo_num += 1
                try:
                    m = measure_snr(
                        iq_block,
                        sample_rate=sample_rate,
                        center_freq=center_freq,
                        fft_size=fft_size,
                        window=window,
                        signal_freq_hz=signal_freq_hz,
                        signal_bw_hz=signal_bw_hz,
                    )

                    row = {
                        "gain_db": gain,
                        "fft_size": fft_size,
                        "window": window,
                        **m,
                    }
                    results.append(row)

                    print(f"  [{combo_num:3d}/{total_combos}] gain={gain:2.0f} fft={fft_size:5d} win={window:<16s} "
                          f"SNR={m['snr_db']:6.1f} dB  peak={m['peak_power_dbm']:7.1f}  floor={m['noise_floor_dbm']:7.1f}")

                except Exception as e:
                    print(f"  [{combo_num:3d}/{total_combos}] gain={gain} fft={fft_size} win={window} ERROR: {e}")

    return results


def write_csv(results: list[dict], path: Path) -> None:
    if not results:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)


def print_summary(results: list[dict]) -> None:
    if not results:
        print("\n  No results collected.\n")
        return

    # Sort by SNR descending
    ranked = sorted(results, key=lambda r: r["snr_db"], reverse=True)

    print("\n  ╔══════════════════════════════════════════════════════════╗")
    print("  ║  TOP 5 PARAMETER COMBINATIONS BY SNR                    ║")
    print("  ╠══════════════════════════════════════════════════════════╣")
    print("  ║  Rank  Gain   FFT    Window            SNR     Peak  NF ║")
    print("  ╠══════════════════════════════════════════════════════════╣")

    for i, r in enumerate(ranked[:5]):
        print(f"  ║  #{i+1:<3d}  {r['gain_db']:4.0f}  {r['fft_size']:5d}  {r['window']:<16s}  "
              f"{r['snr_db']:5.1f}  {r['peak_power_dbm']:6.1f}  {r['noise_floor_dbm']:5.1f} ║")

    print("  ╚══════════════════════════════════════════════════════════╝")

    best = ranked[0]
    print(f"\n  BEST: gain={best['gain_db']:.0f} dB, fft={best['fft_size']}, "
          f"window={best['window']} → SNR={best['snr_db']:.1f} dB\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="SENTINEL — Bench SNR Parameter Sweep")
    parser.add_argument("--gains", default=",".join(str(g) for g in DEFAULT_GAINS),
                        help="Comma-separated gain values in dB")
    parser.add_argument("--fft-sizes", default=",".join(str(f) for f in DEFAULT_FFT_SIZES),
                        help="Comma-separated FFT sizes")
    parser.add_argument("--windows", default=",".join(DEFAULT_WINDOWS),
                        help="Comma-separated window functions")
    parser.add_argument("--signal-freq", type=float, default=None,
                        help="Expected signal center freq in Hz (default: center_freq from config)")
    parser.add_argument("--signal-bw", type=float, default=22e6,
                        help="Expected signal bandwidth in Hz (default: 22 MHz for WiFi)")
    parser.add_argument("--device", default="", help="UHD device args")
    parser.add_argument("--output", default=None, help="CSV output path")

    args = parser.parse_args()

    gains = [float(g) for g in args.gains.split(",")]
    fft_sizes = [int(f) for f in args.fft_sizes.split(",")]
    windows = [w.strip() for w in args.windows.split(",")]

    config = load_config()
    signal_freq = args.signal_freq or config.sdr.rx_a.center_freq_hz
    signal_bw = args.signal_bw

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(args.output) if args.output else Path(f"data/bench/snr_sweep_{timestamp}.csv")

    print()
    print("  ╔══════════════════════════════════════════╗")
    print("  ║  SENTINEL — Bench SNR Parameter Sweep     ║")
    print("  ╚══════════════════════════════════════════╝")

    results = asyncio.run(run_sweep(
        gains=gains,
        fft_sizes=fft_sizes,
        windows=windows,
        signal_freq_hz=signal_freq,
        signal_bw_hz=signal_bw,
        device_args=args.device,
        output_path=output_path,
    ))

    write_csv(results, output_path)
    print_summary(results)

    if results:
        print(f"  Full results saved to: {output_path}\n")


if __name__ == "__main__":
    main()
