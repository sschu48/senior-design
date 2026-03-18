"""SENTINEL — Configurable bench test harness.

Runs the detection pipeline with CLI-overridable SDR/DSP parameters and
outputs structured JSON test reports.  Designed for validating detection
with real hardware (PCB Yagi → USRP B210) or synthetic signals.

Composes IQ source → PSD → detectors directly (same as DashboardServer)
rather than wrapping PipelineEngine, giving per-frame PSD access for
noise floor statistics.

Usage:
    python -m tools.bench_test                      # synthetic baseline
    python -m tools.bench_test --live --gain 25      # USRP, indoor safe
    python -m tools.bench_test --channel 6 --duration 10 --save-iq
    python -m tools.bench_test --expect-freq 2.437e9 --output report.json
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from src.dsp.detector import Detection, create_detectors, deduplicate
from src.dsp.spectrum import compute_psd, remove_dc_offset
from src.sdr.capture import IQSource, SyntheticSource, USRPSource
from src.sdr.config import SentinelConfig, load_config
from src.sdr.signals import DEFAULT_SIGNALS

logger = logging.getLogger("sentinel.bench")

# ---------------------------------------------------------------------------
# WiFi channel → center frequency mapping (2.4 GHz band)
# ---------------------------------------------------------------------------

WIFI_CHANNEL_FREQ_HZ: dict[int, float] = {
    1: 2.412e9,
    2: 2.417e9,
    3: 2.422e9,
    4: 2.427e9,
    5: 2.432e9,
    6: 2.437e9,
    7: 2.442e9,
    8: 2.447e9,
    9: 2.452e9,
    10: 2.457e9,
    11: 2.462e9,
    12: 2.467e9,
    13: 2.472e9,
    14: 2.484e9,
}

# ---------------------------------------------------------------------------
# Config override helpers
# ---------------------------------------------------------------------------

def apply_cli_overrides(config: SentinelConfig, args: argparse.Namespace) -> SentinelConfig:
    """Apply CLI argument overrides to config via dataclasses.replace() chain.

    Follows the same pattern as _synthetic_config_overrides() in
    sentinel_runner.py — build new frozen dataclass copies with updated fields.
    """
    rx = config.sdr.rx_a
    dsp = config.dsp

    # --- SDR overrides ---
    rx_changes: dict = {}

    if args.gain is not None:
        rx_changes["gain_db"] = float(args.gain)

    if args.freq is not None:
        rx_changes["center_freq_hz"] = float(args.freq)
    elif args.channel is not None:
        if args.channel not in WIFI_CHANNEL_FREQ_HZ:
            raise ValueError(f"Invalid WiFi channel {args.channel} (valid: 1-14)")
        rx_changes["center_freq_hz"] = WIFI_CHANNEL_FREQ_HZ[args.channel]

    if args.bandwidth is not None:
        rx_changes["bandwidth_hz"] = float(args.bandwidth)

    if args.sample_rate is not None:
        rx_changes["sample_rate_hz"] = float(args.sample_rate)

    if rx_changes:
        new_rx = dataclasses.replace(rx, **rx_changes)
        new_sdr = dataclasses.replace(config.sdr, rx_a=new_rx)
        config = dataclasses.replace(config, sdr=new_sdr)

    # --- DSP overrides ---
    dsp_changes: dict = {}
    tw_changes: dict = {}
    cfar_changes: dict = {}

    if args.fft_size is not None:
        dsp_changes["fft_size"] = int(args.fft_size)

    if args.tripwire_threshold is not None:
        tw_changes["threshold_db"] = float(args.tripwire_threshold)

    if args.cfar_threshold is not None:
        cfar_changes["threshold_factor_db"] = float(args.cfar_threshold)

    if tw_changes:
        new_tw = dataclasses.replace(dsp.tripwire, **tw_changes)
        dsp_changes["tripwire"] = new_tw

    if cfar_changes:
        new_cfar = dataclasses.replace(dsp.cfar, **cfar_changes)
        dsp_changes["cfar"] = new_cfar

    if dsp_changes:
        new_dsp = dataclasses.replace(dsp, **dsp_changes)
        config = dataclasses.replace(config, dsp=new_dsp)

    # --- Synthetic mode fast-convergence overrides ---
    if not args.live:
        dsp = config.dsp
        new_tw = dataclasses.replace(
            dsp.tripwire,
            noise_floor_window_sec=0.5,
            min_trigger_duration_ms=10,
        )
        new_cfar = dataclasses.replace(
            dsp.cfar,
            min_detection_bw_hz=30e3,
        )
        new_dsp = dataclasses.replace(dsp, tripwire=new_tw, cfar=new_cfar)
        config = dataclasses.replace(config, dsp=new_dsp)

    return config


# ---------------------------------------------------------------------------
# Results accumulator
# ---------------------------------------------------------------------------

@dataclass
class BenchResults:
    """Mutable accumulator for per-frame bench test statistics."""

    detections: list[dict] = field(default_factory=list)
    noise_floors: list[float] = field(default_factory=list)
    frame_count: int = 0
    warmup_frames: int = 0
    iq_buffer: list[np.ndarray] = field(default_factory=list)

    def record_frame(self, noise_floor_dbm: float) -> None:
        self.frame_count += 1
        self.noise_floors.append(noise_floor_dbm)

    def record_detection(self, d: Detection, frame: int) -> None:
        self.detections.append({
            "frame": frame,
            "freq_hz": d.freq_hz,
            "bandwidth_hz": d.bandwidth_hz,
            "power_dbm": d.power_dbm,
            "snr_db": d.snr_db,
            "bin_start": d.bin_start,
            "bin_end": d.bin_end,
        })

    def record_iq(self, iq: np.ndarray) -> None:
        self.iq_buffer.append(iq)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def compute_report(
    results: BenchResults,
    config: SentinelConfig,
    args: argparse.Namespace,
    elapsed_sec: float,
) -> dict:
    """Generate a structured JSON report from accumulated bench results."""
    # frame_count only includes collection frames (excludes warmup)
    collection_frames = results.frame_count

    report: dict = {
        "test": "bench_test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "center_freq_hz": config.sdr.rx_a.center_freq_hz,
            "sample_rate_hz": config.sdr.rx_a.sample_rate_hz,
            "gain_db": config.sdr.rx_a.gain_db,
            "fft_size": config.dsp.fft_size,
            "cfar_threshold_db": config.dsp.cfar.threshold_factor_db,
            "tripwire_threshold_db": config.dsp.tripwire.threshold_db,
            "live": args.live,
        },
        "duration_sec": round(elapsed_sec, 3),
        "warmup_frames": results.warmup_frames,
        "frames_collected": collection_frames,
    }

    # --- Detection stats ---
    detections = results.detections
    report["detection_count"] = len(detections)

    if collection_frames > 0:
        frames_with_detections = len(set(d["frame"] for d in detections))
        report["detection_rate"] = round(
            frames_with_detections / collection_frames, 4
        )
    else:
        report["detection_rate"] = 0.0

    # --- SNR distribution ---
    if detections:
        snrs = [d["snr_db"] for d in detections]
        report["snr_db"] = {
            "min": round(min(snrs), 2),
            "max": round(max(snrs), 2),
            "mean": round(float(np.mean(snrs)), 2),
            "median": round(float(np.median(snrs)), 2),
        }
    else:
        report["snr_db"] = None

    # --- Noise floor stats ---
    if results.noise_floors:
        nf = results.noise_floors
        report["noise_floor_dbm"] = {
            "min": round(min(nf), 2),
            "max": round(max(nf), 2),
            "mean": round(float(np.mean(nf)), 2),
            "std": round(float(np.std(nf)), 2),
        }
    else:
        report["noise_floor_dbm"] = None

    # --- Frequency accuracy (if --expect-freq given) ---
    if args.expect_freq is not None and detections:
        expect_hz = float(args.expect_freq)
        tolerance_hz = float(args.freq_tolerance)
        freq_errors = [abs(d["freq_hz"] - expect_hz) for d in detections]
        within_tol = sum(1 for e in freq_errors if e <= tolerance_hz)
        report["freq_accuracy"] = {
            "expect_freq_hz": expect_hz,
            "tolerance_hz": tolerance_hz,
            "within_tolerance": within_tol,
            "total_detections": len(detections),
            "accuracy_pct": round(within_tol / len(detections) * 100, 1),
            "mean_error_hz": round(float(np.mean(freq_errors)), 1),
            "max_error_hz": round(float(np.max(freq_errors)), 1),
        }

    # --- Per-detection log ---
    report["detections"] = detections

    return report


# ---------------------------------------------------------------------------
# IQ save
# ---------------------------------------------------------------------------

def _save_iq_capture(results: BenchResults, output_dir: str = "data/samples") -> str | None:
    """Concatenate IQ buffer and save as .cf32 file."""
    if not results.iq_buffer:
        return None

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"bench_{timestamp}.cf32"
    filepath = out_dir / filename

    iq_all = np.concatenate(results.iq_buffer)
    iq_all.astype(np.complex64).tofile(str(filepath))

    logger.info("Saved IQ capture: %s (%d samples)", filepath, len(iq_all))
    return str(filepath)


# ---------------------------------------------------------------------------
# Main bench test loop
# ---------------------------------------------------------------------------

async def run_bench_test(
    config: SentinelConfig,
    source: IQSource,
    args: argparse.Namespace,
) -> dict:
    """Run the bench test: warmup → collect → report."""
    rx = config.sdr.rx_a
    dsp = config.dsp

    tripwire, cfar = create_detectors(config)
    frames_per_sec = rx.sample_rate_hz / dsp.fft_size

    # --- Determine frame limits ---
    warmup_sec = args.warmup
    warmup_frames = max(1, int(warmup_sec * frames_per_sec))

    if args.frames is not None:
        total_frames = warmup_frames + args.frames
    elif args.duration is not None:
        collection_frames = max(1, int(args.duration * frames_per_sec))
        total_frames = warmup_frames + collection_frames
    else:
        # Default: 10 seconds of collection
        collection_frames = max(1, int(10 * frames_per_sec))
        total_frames = warmup_frames + collection_frames

    results = BenchResults(warmup_frames=warmup_frames)
    num_samples = dsp.fft_size * 2  # 2x for Welch overlap

    await source.start()

    print("=" * 60)
    print("SENTINEL — Bench Test")
    print("=" * 60)
    print(f"  Mode:       {'LIVE (USRP B210)' if args.live else 'Synthetic'}")
    print(f"  Center:     {rx.center_freq_hz / 1e6:.3f} MHz")
    print(f"  Rate:       {rx.sample_rate_hz / 1e6:.2f} MSPS")
    print(f"  Gain:       {rx.gain_db:.1f} dB")
    print(f"  FFT:        {dsp.fft_size}")
    print(f"  CFAR thr:   {dsp.cfar.threshold_factor_db:.1f} dB")
    print(f"  Trip thr:   {dsp.tripwire.threshold_db:.1f} dB")
    print(f"  Warmup:     {warmup_frames} frames ({warmup_sec}s)")
    print(f"  Collect:    {total_frames - warmup_frames} frames")
    print(f"  Save IQ:    {args.save_iq}")
    print("=" * 60)

    start_time = time.monotonic()

    try:
        for frame_idx in range(total_frames):
            iq = await source.read(num_samples)
            iq = remove_dc_offset(iq, window=dsp.dc_offset_window)

            freq_hz, psd_dbm = compute_psd(
                iq,
                sample_rate=rx.sample_rate_hz,
                fft_size=dsp.fft_size,
                window=dsp.window,
                overlap=dsp.overlap,
                center_freq=rx.center_freq_hz,
            )

            is_warmup = frame_idx < warmup_frames

            # Run detectors on every frame (they need warmup too)
            tw_dets = tripwire.process(psd_dbm, freq_hz)
            cfar_dets = cfar.process(psd_dbm, freq_hz)

            if not is_warmup:
                noise_floor = float(np.median(psd_dbm))
                results.record_frame(noise_floor)

                all_dets = tw_dets + cfar_dets
                # Deduplicate by SNR (same as PipelineEngine._deduplicate)
                all_dets = deduplicate(all_dets)

                for d in all_dets:
                    results.record_detection(d, frame_idx)

                if args.save_iq:
                    results.record_iq(iq)

            await asyncio.sleep(0)

    except KeyboardInterrupt:
        print("\n  [Interrupted]")
    finally:
        elapsed = time.monotonic() - start_time
        await source.stop()

    # --- Save IQ if requested ---
    iq_path = None
    if args.save_iq:
        iq_path = _save_iq_capture(results)

    # --- Generate report ---
    report = compute_report(results, config, args, elapsed)
    if iq_path:
        report["iq_file"] = iq_path

    total_frames = results.warmup_frames + results.frame_count
    if elapsed > 0:
        report["frame_rate_fps"] = round(total_frames / elapsed, 1)

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SENTINEL — Configurable bench test harness",
    )

    # Source selection
    parser.add_argument("--live", action="store_true",
                        help="Use USRP B210 hardware (default: synthetic)")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--device", default="", help="UHD device args")

    # SDR overrides
    sdr = parser.add_argument_group("SDR overrides")
    sdr.add_argument("--gain", type=float, default=None, help="RX gain (dB)")
    sdr.add_argument("--freq", type=float, default=None, help="Center freq (Hz)")
    sdr.add_argument("--bandwidth", type=float, default=None, help="RX bandwidth (Hz)")
    sdr.add_argument("--sample-rate", type=float, default=None, help="Sample rate (Hz)")
    sdr.add_argument("--channel", type=int, default=None,
                      help="WiFi channel 1-14 (shortcut for --freq)")

    # DSP overrides
    dsp = parser.add_argument_group("DSP overrides")
    dsp.add_argument("--cfar-threshold", type=float, default=None,
                     help="CFAR threshold factor (dB)")
    dsp.add_argument("--tripwire-threshold", type=float, default=None,
                     help="Tripwire threshold (dB)")
    dsp.add_argument("--fft-size", type=int, default=None, help="FFT size")

    # Test control
    ctrl = parser.add_argument_group("test control")
    ctrl.add_argument("--duration", type=float, default=None,
                      help="Collection duration in seconds (default: 10)")
    ctrl.add_argument("--frames", type=int, default=None,
                      help="Exact number of collection frames (overrides --duration)")
    ctrl.add_argument("--warmup", type=float, default=2.0,
                      help="Warmup duration in seconds (default: 2)")
    ctrl.add_argument("--save-iq", action="store_true",
                      help="Save raw IQ to data/samples/")
    ctrl.add_argument("--output", default=None,
                      help="Write JSON report to file (default: stdout)")

    # Validation
    val = parser.add_argument_group("validation")
    val.add_argument("--expect-freq", type=float, default=None,
                     help="Expected detection frequency (Hz)")
    val.add_argument("--freq-tolerance", type=float, default=1e6,
                     help="Frequency tolerance (Hz, default: 1 MHz)")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    config = load_config(args.config)
    config = apply_cli_overrides(config, args)

    # Setup logging
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger("sentinel")
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    # Build source
    if args.live:
        source: IQSource = USRPSource(
            channel_config=config.sdr.rx_a,
            device_args=args.device,
            channel=0,
        )
    else:
        source = SyntheticSource(
            sample_rate_hz=config.sdr.rx_a.sample_rate_hz,
            noise_power_dbm=-90.0,
            signals=DEFAULT_SIGNALS,
            seed=42,
        )

    report = asyncio.run(run_bench_test(config, source, args))

    # --- Output ---
    report_json = json.dumps(report, indent=2)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report_json + "\n")
        print(f"\n  Report saved: {args.output}")
    else:
        print("\n" + report_json)

    # --- Summary ---
    print("\n" + "=" * 60)
    print(f"  Frames:      {report['warmup_frames']} warmup + {report['frames_collected']} collected")
    print(f"  Detections:  {report['detection_count']}")
    print(f"  Det. rate:   {report['detection_rate']:.1%}")
    if report.get("snr_db"):
        print(f"  SNR:         {report['snr_db']['mean']:.1f} dB mean"
              f" ({report['snr_db']['min']:.1f}–{report['snr_db']['max']:.1f})")
    if report.get("noise_floor_dbm"):
        print(f"  Noise floor: {report['noise_floor_dbm']['mean']:.1f} dBm"
              f" (std {report['noise_floor_dbm']['std']:.2f})")
    if report.get("freq_accuracy"):
        fa = report["freq_accuracy"]
        print(f"  Freq acc:    {fa['accuracy_pct']:.1f}% within {fa['tolerance_hz']/1e6:.1f} MHz")
    if report.get("frame_rate_fps"):
        print(f"  Frame rate:  {report['frame_rate_fps']:.0f} fps")
    print("=" * 60)


if __name__ == "__main__":
    main()
