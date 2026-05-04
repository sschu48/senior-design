"""SENTINEL — Configurable bench test harness (V2).

Runs the V2 detection pipeline with CLI-overridable SDR/DSP parameters and
outputs a structured JSON report. Replaces the V1 tripwire+CFAR direct loop
with the typed-stage Pipeline so detection results match real operation.

Usage:
    python -m tools.bench_test                       # synthetic baseline
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

from src.pipeline.contracts import RFEvent
from src.pipeline.pipeline import Pipeline
from src.sdr.capture import (
    DualIQSource,
    SyntheticDualSource,
    USRPDualSource,
)
from src.sdr.config import SentinelConfig, load_config
from src.sdr.signals import DEFAULT_SIGNALS

logger = logging.getLogger("sentinel.bench")

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
# CLI override helpers
# ---------------------------------------------------------------------------

def apply_cli_overrides(
    config: SentinelConfig, args: argparse.Namespace
) -> SentinelConfig:
    """Apply CLI overrides to a SentinelConfig and return a new copy.

    Overrides land on the same fields the V1 harness used (gain, freq,
    bandwidth, sample rate) plus V2-native fields (burst threshold,
    spectrogram FFT size). Synthetic mode receives a fast-convergence
    burst-detector profile so test runs converge quickly without hardware.
    """
    rx = config.sdr.rx_a
    rx_b = config.sdr.rx_b
    dsp = config.dsp

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
        new_rx_b = dataclasses.replace(rx_b, **rx_changes)
        new_sdr = dataclasses.replace(config.sdr, rx_a=new_rx, rx_b=new_rx_b)
        config = dataclasses.replace(config, sdr=new_sdr)
        rx = config.sdr.rx_a

    burst_changes: dict = {}
    cfar_changes: dict = {}
    spec_changes: dict = {}

    if args.burst_threshold is not None:
        burst_changes["threshold_db"] = float(args.burst_threshold)
    if args.cfar_threshold is not None:
        cfar_changes["threshold_factor_db"] = float(args.cfar_threshold)
    if args.fft_size is not None:
        spec_changes["fft_size"] = int(args.fft_size)

    dsp_changes: dict = {}
    if burst_changes:
        dsp_changes["burst"] = dataclasses.replace(dsp.burst, **burst_changes)
    if cfar_changes:
        dsp_changes["cfar"] = dataclasses.replace(dsp.cfar, **cfar_changes)
    if spec_changes:
        dsp_changes["spectrogram"] = dataclasses.replace(
            dsp.spectrogram, **spec_changes
        )

    if dsp_changes:
        config = dataclasses.replace(config, dsp=dataclasses.replace(dsp, **dsp_changes))
        dsp = config.dsp

    if not args.live:
        new_burst = dataclasses.replace(
            dsp.burst,
            noise_floor_window_sec=0.5,
            min_burst_duration_ms=1.0,
        )
        new_dsp = dataclasses.replace(dsp, burst=new_burst)
        config = dataclasses.replace(config, dsp=new_dsp)

    return config


# ---------------------------------------------------------------------------
# Results accumulator
# ---------------------------------------------------------------------------

@dataclass
class BenchResults:
    """Mutable accumulator for per-frame bench statistics."""

    events: list[dict] = field(default_factory=list)
    noise_floors: list[float] = field(default_factory=list)
    frame_count: int = 0
    warmup_frames: int = 0
    iq_buffer: list[np.ndarray] = field(default_factory=list)

    def record_frame(self, noise_floor_dbm: float) -> None:
        self.frame_count += 1
        self.noise_floors.append(noise_floor_dbm)

    def record_event(self, event: RFEvent, frame: int) -> None:
        self.events.append(
            {
                "frame": frame,
                "freq_hz": event.center_freq_hz,
                "bandwidth_hz": event.bandwidth_hz,
                "power_dbm": event.peak_power_dbm,
                "snr_db": event.snr_db,
                "family": event.family.value,
                "bin_start": event.bin_start,
                "bin_end": event.bin_end,
                "bearing_deg": event.bearing_deg,
            }
        )

    def record_iq(self, iq: np.ndarray) -> None:
        self.iq_buffer.append(iq)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def compute_report(
    results: BenchResults,
    config: SentinelConfig,
    args: argparse.Namespace,
    elapsed_sec: float,
) -> dict:
    """Generate a structured JSON report from bench results."""
    collection_frames = results.frame_count

    report: dict = {
        "test": "bench_test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "center_freq_hz": config.sdr.rx_a.center_freq_hz,
            "sample_rate_hz": config.sdr.rx_a.sample_rate_hz,
            "gain_db": config.sdr.rx_a.gain_db,
            "fft_size": config.dsp.spectrogram.fft_size,
            "burst_threshold_db": config.dsp.burst.threshold_db,
            "cfar_threshold_db": config.dsp.cfar.threshold_factor_db,
            "live": args.live,
        },
        "duration_sec": round(elapsed_sec, 3),
        "warmup_frames": results.warmup_frames,
        "frames_collected": collection_frames,
    }

    events = results.events
    report["detection_count"] = len(events)

    if collection_frames > 0:
        frames_with_events = len({e["frame"] for e in events})
        report["detection_rate"] = round(frames_with_events / collection_frames, 4)
    else:
        report["detection_rate"] = 0.0

    if events:
        snrs = [e["snr_db"] for e in events]
        report["snr_db"] = {
            "min": round(min(snrs), 2),
            "max": round(max(snrs), 2),
            "mean": round(float(np.mean(snrs)), 2),
            "median": round(float(np.median(snrs)), 2),
        }
    else:
        report["snr_db"] = None

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

    if args.expect_freq is not None and events:
        expect_hz = float(args.expect_freq)
        tolerance_hz = float(args.freq_tolerance)
        freq_errors = [abs(e["freq_hz"] - expect_hz) for e in events]
        within_tol = sum(1 for e in freq_errors if e <= tolerance_hz)
        report["freq_accuracy"] = {
            "expect_freq_hz": expect_hz,
            "tolerance_hz": tolerance_hz,
            "within_tolerance": within_tol,
            "total_detections": len(events),
            "accuracy_pct": round(within_tol / len(events) * 100, 1),
            "mean_error_hz": round(float(np.mean(freq_errors)), 1),
            "max_error_hz": round(float(np.max(freq_errors)), 1),
        }

    report["events"] = events
    return report


# ---------------------------------------------------------------------------
# IQ save
# ---------------------------------------------------------------------------

def _save_iq_capture(
    results: BenchResults, output_dir: str = "data/samples"
) -> str | None:
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
# Bench loop
# ---------------------------------------------------------------------------

async def run_bench_test(
    config: SentinelConfig,
    source: DualIQSource,
    args: argparse.Namespace,
) -> dict:
    """Run the V2 pipeline for a fixed number of frames and report."""
    rx = config.sdr.rx_a
    spec = config.dsp.spectrogram
    burst = config.dsp.burst
    cfar = config.dsp.cfar

    # Welch with 50% overlap drives one frame per 2 * fft_size samples.
    frames_per_sec = rx.sample_rate_hz / (2 * spec.fft_size)

    warmup_sec = args.warmup
    warmup_frames = max(1, int(warmup_sec * frames_per_sec))

    if args.frames is not None:
        total_frames = warmup_frames + args.frames
    elif args.duration is not None:
        collection_frames = max(1, int(args.duration * frames_per_sec))
        total_frames = warmup_frames + collection_frames
    else:
        collection_frames = max(1, int(10 * frames_per_sec))
        total_frames = warmup_frames + collection_frames

    results = BenchResults(warmup_frames=warmup_frames)
    pipeline = Pipeline(config=config, source=source)

    print("=" * 60)
    print("SENTINEL — Bench Test (V2)")
    print("=" * 60)
    print(f"  Mode:        {'LIVE (USRP B210)' if args.live else 'Synthetic'}")
    print(f"  Center:      {rx.center_freq_hz / 1e6:.3f} MHz")
    print(f"  Rate:        {rx.sample_rate_hz / 1e6:.2f} MSPS")
    print(f"  Gain:        {rx.gain_db:.1f} dB")
    print(f"  FFT:         {spec.fft_size}")
    print(f"  Burst thr:   {burst.threshold_db:.1f} dB")
    print(f"  CFAR thr:    {cfar.threshold_factor_db:.1f} dB")
    print(f"  Warmup:      {warmup_frames} frames ({warmup_sec}s)")
    print(f"  Collect:     {total_frames - warmup_frames} frames")
    print(f"  Save IQ:     {args.save_iq}")
    print("=" * 60)

    start_time = time.monotonic()
    await pipeline.start()

    try:
        for frame_idx in range(total_frames):
            result = await pipeline.process_one_frame()
            if frame_idx < warmup_frames:
                continue

            psd = result.dual_spectrogram.omni.latest_psd_dbm
            noise_floor = float(np.median(psd))
            results.record_frame(noise_floor)

            for event in result.events:
                results.record_event(event, frame_idx)

            if args.save_iq:
                results.record_iq(result.iq_frame.rx_a.iq)

            await asyncio.sleep(0)

    except KeyboardInterrupt:
        print("\n  [Interrupted]")
    finally:
        elapsed = time.monotonic() - start_time
        await pipeline.stop()

    iq_path = _save_iq_capture(results) if args.save_iq else None

    report = compute_report(results, config, args, elapsed)
    if iq_path:
        report["iq_file"] = iq_path

    total = results.warmup_frames + results.frame_count
    if elapsed > 0:
        report["frame_rate_fps"] = round(total / elapsed, 1)

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SENTINEL — V2 bench test harness",
    )

    parser.add_argument("--live", action="store_true", help="Use USRP B210 hardware (default: synthetic)")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--device", default="", help="UHD device args")

    sdr = parser.add_argument_group("SDR overrides")
    sdr.add_argument("--gain", type=float, default=None, help="RX gain (dB)")
    sdr.add_argument("--freq", type=float, default=None, help="Center freq (Hz)")
    sdr.add_argument("--bandwidth", type=float, default=None, help="RX bandwidth (Hz)")
    sdr.add_argument("--sample-rate", type=float, default=None, help="Sample rate (Hz)")
    sdr.add_argument("--channel", type=int, default=None, help="WiFi channel 1-14")

    dsp = parser.add_argument_group("DSP overrides")
    dsp.add_argument("--burst-threshold", type=float, default=None,
                     help="Stage 1 burst threshold over noise floor (dB)")
    dsp.add_argument("--cfar-threshold", type=float, default=None,
                     help="CFAR threshold factor used by BearingStage (dB)")
    dsp.add_argument("--fft-size", type=int, default=None,
                     help="V2 spectrogram FFT size")

    ctrl = parser.add_argument_group("test control")
    ctrl.add_argument("--duration", type=float, default=None,
                      help="Collection duration (s, default: 10)")
    ctrl.add_argument("--frames", type=int, default=None,
                      help="Exact number of collection frames (overrides --duration)")
    ctrl.add_argument("--warmup", type=float, default=2.0,
                      help="Warmup duration (s, default: 2)")
    ctrl.add_argument("--save-iq", action="store_true",
                      help="Save raw IQ to data/samples/")
    ctrl.add_argument("--output", default=None,
                      help="Write JSON report to file (default: stdout)")

    val = parser.add_argument_group("validation")
    val.add_argument("--expect-freq", type=float, default=None,
                     help="Expected detection frequency (Hz)")
    val.add_argument("--freq-tolerance", type=float, default=1e6,
                     help="Frequency tolerance (Hz, default: 1 MHz)")

    return parser


def _build_source(config: SentinelConfig, args: argparse.Namespace) -> DualIQSource:
    if args.live:
        return USRPDualSource(
            rx_a_config=config.sdr.rx_a,
            rx_b_config=config.sdr.rx_b,
            device_args=args.device,
        )
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


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    config = load_config(args.config)
    config = apply_cli_overrides(config, args)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger("sentinel")
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    source = _build_source(config, args)
    report = asyncio.run(run_bench_test(config, source, args))

    report_json = json.dumps(report, indent=2)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report_json + "\n")
        print(f"\n  Report saved: {args.output}")
    else:
        print("\n" + report_json)

    print("\n" + "=" * 60)
    print(f"  Frames:      {report['warmup_frames']} warmup + {report['frames_collected']} collected")
    print(f"  Events:      {report['detection_count']}")
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
