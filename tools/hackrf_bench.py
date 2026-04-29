"""SENTINEL HackRF bench receiver harness.

This tool is the RX-side companion to ``tools.hackrf_tx``.  The Pi/HackRF
emits one known profile, while this machine listens with the B210 and writes a
small JSON report with per-channel SNR, peak frequency, detector hits, and
dual-RX agreement.

Typical two-terminal workflow:

    # Pi / HackRF side
    python -m tools.hackrf_tx --profile tone_2437 --gain 0 --rx-distance 3

    # SENTINEL / B210 side
    python -m tools.hackrf_bench --live --dual --profile tone_2437
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
from src.pipeline.contracts import ChannelRole, IQChannelFrame
from src.sdr.capture import (
    DualIQSource,
    IQSource,
    SignalDef,
    SyntheticDualSource,
    SyntheticSource,
    USRPDualSource,
    USRPSource,
)
from src.sdr.config import SentinelConfig, load_config
from tools.bench_test import WIFI_CHANNEL_FREQ_HZ
from tools.hackrf_tx.config import load_hackrf_tx_config

logger = logging.getLogger("sentinel.hackrf_bench")


TONE_MEASUREMENT_BW_HZ = 200e3
TONE_RX_CENTER_OFFSET_HZ = -1e6
DEFAULT_CONTINUOUS_CAPTURE_SEC = 1.0
DEFAULT_BURST_CAPTURE_SEC = 5.0
DEFAULT_WARMUP_SEC = 0.2
DEFAULT_TX_GAIN_DB = 0
DEFAULT_RX_DISTANCE_M = 3.0
DEFAULT_MIN_SNR_DB = 8.0
DEFAULT_OUTPUT_DIR = Path("data/bench")


@dataclass(frozen=True)
class BenchProfileSpec:
    """HackRF profile parameters needed by the RX bench harness."""

    name: str
    description: str
    center_freq_hz: float
    sample_rate_hz: float
    measurement_bw_hz: float
    burst_duration_s: float
    period_s: float
    iq_source: str

    @property
    def is_bursty(self) -> bool:
        return self.period_s > 0.0

    @property
    def default_capture_sec(self) -> float:
        if self.is_bursty:
            return DEFAULT_BURST_CAPTURE_SEC
        return DEFAULT_CONTINUOUS_CAPTURE_SEC


@dataclass(frozen=True)
class BandMeasurement:
    """One PSD-frame measurement inside the expected HackRF signal band."""

    peak_freq_hz: float
    peak_power_dbm: float
    noise_floor_dbm: float
    snr_db: float
    freq_error_hz: float


@dataclass
class ChannelAccumulator:
    """Accumulates per-frame measurements for one receive role."""

    role: ChannelRole
    min_snr_db: float
    measurements: list[BandMeasurement] = field(default_factory=list)
    detections: list[dict] = field(default_factory=list)
    iq_blocks: list[np.ndarray] = field(default_factory=list)

    def record_measurement(self, measurement: BandMeasurement) -> None:
        self.measurements.append(measurement)

    def record_detection(self, detection: Detection, frame_index: int) -> None:
        self.detections.append({
            "frame": frame_index,
            "freq_hz": detection.freq_hz,
            "bandwidth_hz": detection.bandwidth_hz,
            "power_dbm": detection.power_dbm,
            "snr_db": detection.snr_db,
            "bin_start": detection.bin_start,
            "bin_end": detection.bin_end,
        })

    def record_iq(self, iq: np.ndarray) -> None:
        self.iq_blocks.append(iq.astype(np.complex64, copy=True))

    def summary(self) -> dict:
        frame_count = len(self.measurements)
        if frame_count == 0:
            return {
                "role": self.role.value,
                "frames": 0,
                "frames_above_min_snr": 0,
                "signal_presence_rate": 0.0,
                "peak_power_dbm": None,
                "noise_floor_dbm": None,
                "snr_db": None,
                "peak_freq_hz": None,
                "mean_abs_freq_error_hz": None,
                "detection_count": len(self.detections),
            }

        snrs = np.array([m.snr_db for m in self.measurements], dtype=float)
        peaks = np.array([m.peak_power_dbm for m in self.measurements], dtype=float)
        floors = np.array([m.noise_floor_dbm for m in self.measurements], dtype=float)
        freqs = np.array([m.peak_freq_hz for m in self.measurements], dtype=float)
        freq_errors = np.array([abs(m.freq_error_hz) for m in self.measurements], dtype=float)

        frames_above = int(np.count_nonzero(snrs >= self.min_snr_db))
        return {
            "role": self.role.value,
            "frames": frame_count,
            "frames_above_min_snr": frames_above,
            "signal_presence_rate": round(frames_above / frame_count, 4),
            "peak_power_dbm": {
                "max": round(float(np.max(peaks)), 2),
                "mean": round(float(np.mean(peaks)), 2),
                "median": round(float(np.median(peaks)), 2),
            },
            "noise_floor_dbm": {
                "mean": round(float(np.mean(floors)), 2),
                "median": round(float(np.median(floors)), 2),
                "std": round(float(np.std(floors)), 2),
            },
            "snr_db": {
                "max": round(float(np.max(snrs)), 2),
                "mean": round(float(np.mean(snrs)), 2),
                "median": round(float(np.median(snrs)), 2),
            },
            "peak_freq_hz": {
                "mean": round(float(np.mean(freqs)), 1),
                "median": round(float(np.median(freqs)), 1),
            },
            "mean_abs_freq_error_hz": round(float(np.mean(freq_errors)), 1),
            "detection_count": len(self.detections),
        }


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger("sentinel")
    if not root.handlers:
        root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def load_bench_profile(profile_name: str, config_path: str | None = None) -> BenchProfileSpec:
    """Load a HackRF TX profile and adapt it for RX-side measurement."""
    tx_config = load_hackrf_tx_config(config_path)
    if profile_name not in tx_config.profiles:
        raise ValueError(
            f"Unknown HackRF profile '{profile_name}'. "
            f"Available: {sorted(tx_config.profiles)}"
        )

    profile = tx_config.profiles[profile_name]
    measurement_bw = profile.bandwidth_hz
    if measurement_bw <= 0:
        measurement_bw = TONE_MEASUREMENT_BW_HZ

    return BenchProfileSpec(
        name=profile.name,
        description=profile.description,
        center_freq_hz=profile.center_freq_hz,
        sample_rate_hz=profile.sample_rate_hz,
        measurement_bw_hz=measurement_bw,
        burst_duration_s=profile.burst_duration_s,
        period_s=profile.period_s,
        iq_source=profile.iq_source,
    )


def build_tx_command(profile_name: str, tx_gain_db: int, rx_distance_m: float) -> str:
    """Return the matching Pi/HackRF command for this bench profile."""
    return (
        "python -m tools.hackrf_tx "
        f"--profile {profile_name} "
        f"--gain {tx_gain_db} "
        f"--rx-distance {rx_distance_m:g}"
    )


def apply_rx_overrides(
    config: SentinelConfig,
    args: argparse.Namespace,
    profile: BenchProfileSpec,
) -> SentinelConfig:
    """Tune both RX channels to the HackRF profile unless CLI overrides say otherwise."""
    center_freq = profile.center_freq_hz
    if profile.iq_source == "synth_tone":
        # A CW tone exactly at the receiver LO lands at DC and can be removed by
        # DC-offset correction.  Tune slightly low while still measuring the
        # transmitted 2.437 GHz tone.
        center_freq = profile.center_freq_hz + TONE_RX_CENTER_OFFSET_HZ
    if args.freq is not None:
        center_freq = float(args.freq)
    elif args.channel is not None:
        if args.channel not in WIFI_CHANNEL_FREQ_HZ:
            raise ValueError(f"Invalid WiFi channel {args.channel} (valid: 1-14)")
        center_freq = WIFI_CHANNEL_FREQ_HZ[args.channel]

    rx_changes: dict[str, float] = {"center_freq_hz": center_freq}
    if args.gain is not None:
        rx_changes["gain_db"] = float(args.gain)
    if args.sample_rate is not None:
        rx_changes["sample_rate_hz"] = float(args.sample_rate)
    if args.bandwidth is not None:
        rx_changes["bandwidth_hz"] = float(args.bandwidth)

    rx_a = dataclasses.replace(config.sdr.rx_a, **rx_changes)
    rx_b = dataclasses.replace(config.sdr.rx_b, **rx_changes)
    config = dataclasses.replace(
        config,
        sdr=dataclasses.replace(config.sdr, rx_a=rx_a, rx_b=rx_b),
    )

    dsp_changes: dict[str, object] = {}
    if args.fft_size is not None:
        dsp_changes["fft_size"] = int(args.fft_size)

    cfar_changes: dict[str, object] = {}
    if args.cfar_threshold is not None:
        cfar_changes["threshold_factor_db"] = float(args.cfar_threshold)

    # Tones are intentionally narrow.  Make the smoke test measure the RF path
    # instead of failing only because the production CFAR bandwidth gate is wide.
    if args.min_detection_bw is not None:
        cfar_changes["min_detection_bw_hz"] = float(args.min_detection_bw)
    elif profile.iq_source == "synth_tone":
        cfar_changes["min_detection_bw_hz"] = 0.0

    if cfar_changes:
        dsp_changes["cfar"] = dataclasses.replace(config.dsp.cfar, **cfar_changes)

    if dsp_changes:
        config = dataclasses.replace(
            config,
            dsp=dataclasses.replace(config.dsp, **dsp_changes),
        )

    return config


def measure_expected_band(
    freq_hz: np.ndarray,
    power_dbm: np.ndarray,
    expected_freq_hz: float,
    measurement_bw_hz: float,
) -> BandMeasurement:
    """Measure peak and noise floor around the expected HackRF signal band."""
    half_bw = measurement_bw_hz / 2.0
    signal_mask = (
        (freq_hz >= expected_freq_hz - half_bw)
        & (freq_hz <= expected_freq_hz + half_bw)
    )
    if not np.any(signal_mask):
        raise ValueError("Expected signal band does not overlap the PSD frequency axis")

    noise_mask = ~signal_mask
    if not np.any(noise_mask):
        noise_mask = np.ones_like(signal_mask, dtype=bool)

    signal_indices = np.flatnonzero(signal_mask)
    local_peak_offset = int(np.argmax(power_dbm[signal_mask]))
    peak_index = int(signal_indices[local_peak_offset])

    peak_freq = float(freq_hz[peak_index])
    peak_power = float(power_dbm[peak_index])
    noise_floor = float(np.median(power_dbm[noise_mask]))

    return BandMeasurement(
        peak_freq_hz=peak_freq,
        peak_power_dbm=peak_power,
        noise_floor_dbm=noise_floor,
        snr_db=peak_power - noise_floor,
        freq_error_hz=peak_freq - expected_freq_hz,
    )


def _compute_channel_psd(
    channel: IQChannelFrame,
    config: SentinelConfig,
) -> tuple[np.ndarray, np.ndarray]:
    iq = remove_dc_offset(channel.iq, window=config.dsp.dc_offset_window)
    return compute_psd(
        iq,
        sample_rate=channel.sample_rate_hz,
        fft_size=config.dsp.fft_size,
        window=config.dsp.window,
        overlap=config.dsp.overlap,
        center_freq=channel.center_freq_hz,
    )


def _single_frame_from_iq(
    iq: np.ndarray,
    frame_index: int,
    timestamp_s: float,
    config: SentinelConfig,
) -> IQChannelFrame:
    return IQChannelFrame(
        role=ChannelRole.OMNI,
        channel_index=0,
        frame_index=frame_index,
        timestamp_s=timestamp_s,
        sample_rate_hz=config.sdr.rx_a.sample_rate_hz,
        center_freq_hz=config.sdr.rx_a.center_freq_hz,
        antenna_port=config.sdr.rx_a.antenna,
        iq=iq,
    )


def _capture_frames(config: SentinelConfig, args: argparse.Namespace, profile: BenchProfileSpec) -> int:
    num_samples = config.dsp.fft_size * 2
    if args.frames is not None:
        return max(1, int(args.frames))
    duration_sec = args.duration if args.duration is not None else profile.default_capture_sec
    return max(1, int(duration_sec * config.sdr.rx_a.sample_rate_hz / num_samples))


def _warmup_frames(config: SentinelConfig, args: argparse.Namespace) -> int:
    if args.warmup_frames is not None:
        return max(0, int(args.warmup_frames))
    num_samples = config.dsp.fft_size * 2
    return max(0, int(args.warmup * config.sdr.rx_a.sample_rate_hz / num_samples))


def _synth_signals_for_profile(profile: BenchProfileSpec, center_freq_hz: float) -> list[SignalDef]:
    offset = profile.center_freq_hz - center_freq_hz
    if profile.iq_source == "synth_tone":
        return [SignalDef(freq_offset_hz=offset, power_dbm=-55.0, signal_type="tone")]
    return [
        SignalDef(
            freq_offset_hz=offset,
            bandwidth_hz=profile.measurement_bw_hz,
            power_dbm=-55.0,
            signal_type="wideband",
            num_subcarriers=256,
        )
    ]


def build_source(
    config: SentinelConfig,
    args: argparse.Namespace,
    profile: BenchProfileSpec,
) -> IQSource | DualIQSource:
    if args.live and args.dual:
        return USRPDualSource(
            rx_a_config=config.sdr.rx_a,
            rx_b_config=config.sdr.rx_b,
            device_args=args.device,
        )
    if args.live:
        return USRPSource(
            channel_config=config.sdr.rx_a,
            device_args=args.device,
            channel=0,
        )

    signals = _synth_signals_for_profile(profile, config.sdr.rx_a.center_freq_hz)
    if args.dual:
        return SyntheticDualSource(
            sample_rate_hz=config.sdr.rx_a.sample_rate_hz,
            center_freq_hz=config.sdr.rx_a.center_freq_hz,
            omni_center_freq_hz=config.sdr.rx_a.center_freq_hz,
            yagi_center_freq_hz=config.sdr.rx_b.center_freq_hz,
            omni_noise_power_dbm=-90.0,
            yagi_noise_power_dbm=-90.0,
            omni_signals=signals,
            yagi_signals=signals,
            seed=42,
        )
    return SyntheticSource(
        sample_rate_hz=config.sdr.rx_a.sample_rate_hz,
        noise_power_dbm=-90.0,
        signals=signals,
        seed=42,
    )


def _save_iq_blocks(
    accumulators: dict[ChannelRole, ChannelAccumulator],
    output_base: Path,
) -> dict[str, str]:
    saved: dict[str, str] = {}
    for role, acc in accumulators.items():
        if not acc.iq_blocks:
            continue
        iq = np.concatenate(acc.iq_blocks).astype(np.complex64)
        path = output_base.with_name(f"{output_base.stem}_{role.value}.cf32")
        path.parent.mkdir(parents=True, exist_ok=True)
        iq.tofile(path)
        saved[role.value] = str(path)
    return saved


def _dual_agreement(channel_summaries: dict[str, dict]) -> dict | None:
    omni = channel_summaries.get(ChannelRole.OMNI.value)
    yagi = channel_summaries.get(ChannelRole.YAGI.value)
    if not omni or not yagi:
        return None
    if not omni["snr_db"] or not yagi["snr_db"]:
        return None

    return {
        "mean_snr_delta_db_yagi_minus_omni": round(
            yagi["snr_db"]["mean"] - omni["snr_db"]["mean"],
            2,
        ),
        "mean_peak_power_delta_db_yagi_minus_omni": round(
            yagi["peak_power_dbm"]["mean"] - omni["peak_power_dbm"]["mean"],
            2,
        ),
        "mean_peak_freq_delta_hz_yagi_minus_omni": round(
            yagi["peak_freq_hz"]["mean"] - omni["peak_freq_hz"]["mean"],
            1,
        ),
    }


def _pass_summary(channel_summaries: dict[str, dict], min_snr_db: float) -> dict:
    channels = []
    passed = True
    for role, summary in channel_summaries.items():
        max_snr = None if summary["snr_db"] is None else summary["snr_db"]["max"]
        role_passed = (
            max_snr is not None
            and max_snr >= min_snr_db
            and summary["frames_above_min_snr"] > 0
        )
        passed = passed and role_passed
        channels.append({
            "role": role,
            "passed": role_passed,
            "max_snr_db": max_snr,
            "frames_above_min_snr": summary["frames_above_min_snr"],
        })
    return {
        "passed": passed,
        "min_snr_db": min_snr_db,
        "channels": channels,
    }


async def run_bench(
    config: SentinelConfig,
    source: IQSource | DualIQSource,
    args: argparse.Namespace,
    profile: BenchProfileSpec,
    output_path: Path | None = None,
) -> dict:
    """Run warmup and collection, then return a JSON-serializable report."""
    min_snr_db = float(args.min_snr)
    accumulators = {
        ChannelRole.OMNI: ChannelAccumulator(ChannelRole.OMNI, min_snr_db),
    }
    if args.dual:
        accumulators[ChannelRole.YAGI] = ChannelAccumulator(ChannelRole.YAGI, min_snr_db)

    tripwire, cfar = create_detectors(config)
    warmup_frames = _warmup_frames(config, args)
    collect_frames = _capture_frames(config, args, profile)
    num_samples = config.dsp.fft_size * 2
    expected_freq_hz = (
        float(args.expect_freq) if args.expect_freq is not None else profile.center_freq_hz
    )

    await source.start()
    start_monotonic = time.monotonic()

    try:
        total_frames = warmup_frames + collect_frames
        for frame_index in range(total_frames):
            if args.dual:
                dual_frame = await source.read(num_samples)  # type: ignore[union-attr]
                channels = dual_frame.channels
            else:
                iq = await source.read(num_samples)  # type: ignore[union-attr]
                channels = (
                    _single_frame_from_iq(
                        iq=iq,
                        frame_index=frame_index,
                        timestamp_s=time.time(),
                        config=config,
                    ),
                )

            is_warmup = frame_index < warmup_frames
            for channel in channels:
                freq_hz, power_dbm = _compute_channel_psd(channel, config)
                measurement = measure_expected_band(
                    freq_hz=freq_hz,
                    power_dbm=power_dbm,
                    expected_freq_hz=expected_freq_hz,
                    measurement_bw_hz=float(args.signal_bw or profile.measurement_bw_hz),
                )

                if channel.role == ChannelRole.OMNI:
                    detections = tripwire.process(power_dbm, freq_hz)
                else:
                    detections = cfar.process(power_dbm, freq_hz)

                if is_warmup:
                    continue

                acc = accumulators[channel.role]
                acc.record_measurement(measurement)
                for detection in deduplicate(detections):
                    acc.record_detection(detection, frame_index)
                if args.save_iq:
                    acc.record_iq(channel.iq)

            await asyncio.sleep(0)

    finally:
        elapsed_sec = time.monotonic() - start_monotonic
        await source.stop()

    channel_summaries = {
        role.value: accumulator.summary()
        for role, accumulator in accumulators.items()
    }

    output_base = output_path if output_path is not None else default_output_path(profile.name)
    iq_files = _save_iq_blocks(accumulators, output_base) if args.save_iq else {}

    report = {
        "test": "hackrf_bench",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "live" if args.live else "synthetic",
        "dual_rx": bool(args.dual),
        "profile": dataclasses.asdict(profile),
        "tx_command": build_tx_command(profile.name, args.tx_gain, args.rx_distance),
        "rx_config": {
            "rx_a_center_freq_hz": config.sdr.rx_a.center_freq_hz,
            "rx_b_center_freq_hz": config.sdr.rx_b.center_freq_hz,
            "sample_rate_hz": config.sdr.rx_a.sample_rate_hz,
            "bandwidth_hz": config.sdr.rx_a.bandwidth_hz,
            "gain_db": config.sdr.rx_a.gain_db,
            "fft_size": config.dsp.fft_size,
            "window": config.dsp.window,
            "cfar_min_detection_bw_hz": config.dsp.cfar.min_detection_bw_hz,
        },
        "collection": {
            "warmup_frames": warmup_frames,
            "frames_collected": collect_frames,
            "samples_per_frame": num_samples,
            "rf_duration_sec": round(collect_frames * num_samples / config.sdr.rx_a.sample_rate_hz, 3),
            "elapsed_sec": round(elapsed_sec, 3),
        },
        "expected_signal": {
            "center_freq_hz": expected_freq_hz,
            "measurement_bw_hz": float(args.signal_bw or profile.measurement_bw_hz),
            "min_snr_db": min_snr_db,
        },
        "channels": channel_summaries,
        "dual_agreement": _dual_agreement(channel_summaries),
        "pass": _pass_summary(channel_summaries, min_snr_db),
    }
    if iq_files:
        report["iq_files"] = iq_files

    return report


def default_output_path(profile_name: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DEFAULT_OUTPUT_DIR / f"hackrf_bench_{profile_name}_{timestamp}.json"


def write_report(report: dict, output: str | Path | None, profile_name: str) -> Path:
    path = Path(output) if output else default_output_path(profile_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    return path


def print_setup(profile: BenchProfileSpec, args: argparse.Namespace) -> None:
    tx_command = build_tx_command(profile.name, args.tx_gain, args.rx_distance)
    print()
    print("SENTINEL HackRF Bench Setup")
    print("===========================")
    print()
    print("Pi / HackRF TX side:")
    print(f"  {tx_command}")
    print()
    print("SENTINEL / B210 RX side:")
    print(
        "  python -m tools.hackrf_bench --live --dual "
        f"--profile {profile.name} --gain {args.gain if args.gain is not None else 10}"
    )
    print()
    print("Recommended order:")
    print("  1. Conducted cable test: HackRF -> 40-60 dB attenuation -> splitter -> B210 RX-A/RX-B.")
    print("  2. Low-power radiated test: HackRF gain 0, amp off, antennas separated by at least 3 m.")
    print("  3. Yagi sweep: fixed HackRF, rotate Yagi and save one report per angle.")
    print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SENTINEL HackRF bench RX harness")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--profile", default="tone_2437",
                        help="HackRF TX profile name from config.yaml")
    parser.add_argument("--setup-only", action="store_true",
                        help="Print the two-terminal setup and exit")

    source = parser.add_argument_group("source")
    source.add_argument("--live", action="store_true",
                        help="Use live USRP B210 hardware. Default is synthetic sanity mode.")
    source.add_argument("--dual", action="store_true",
                        help="Use B210 dual-RX mode and report omni/Yagi agreement")
    source.add_argument("--device", default="", help="UHD device args")

    rx = parser.add_argument_group("RX overrides")
    rx.add_argument("--gain", type=float, default=None,
                    help="RX gain for both B210 channels in dB")
    rx.add_argument("--freq", type=float, default=None,
                    help="RX center frequency in Hz; overrides profile center")
    rx.add_argument("--channel", type=int, default=None,
                    help="Wi-Fi channel shortcut for RX center frequency")
    rx.add_argument("--sample-rate", type=float, default=None,
                    help="RX sample rate in Hz")
    rx.add_argument("--bandwidth", type=float, default=None,
                    help="RX analog bandwidth in Hz")

    dsp = parser.add_argument_group("DSP overrides")
    dsp.add_argument("--fft-size", type=int, default=None, help="FFT size")
    dsp.add_argument("--signal-bw", type=float, default=None,
                     help="Measurement bandwidth around expected signal in Hz")
    dsp.add_argument("--expect-freq", type=float, default=None,
                     help="Expected signal center in Hz")
    dsp.add_argument("--min-snr", type=float, default=DEFAULT_MIN_SNR_DB,
                     help="Minimum SNR for pass/fail")
    dsp.add_argument("--cfar-threshold", type=float, default=None,
                     help="CFAR threshold factor in dB")
    dsp.add_argument("--min-detection-bw", type=float, default=None,
                     help="Override CFAR minimum detection bandwidth in Hz")

    run = parser.add_argument_group("run control")
    run.add_argument("--duration", type=float, default=None,
                     help="RF collection duration in seconds")
    run.add_argument("--frames", type=int, default=None,
                     help="Exact collection frames; overrides duration")
    run.add_argument("--warmup", type=float, default=DEFAULT_WARMUP_SEC,
                     help="Warmup duration in seconds")
    run.add_argument("--warmup-frames", type=int, default=None,
                     help="Exact warmup frames; overrides warmup duration")
    run.add_argument("--save-iq", action="store_true",
                     help="Save collected IQ as per-channel .cf32 files")
    run.add_argument("--output", default=None,
                     help="JSON report path. Default: data/bench/hackrf_bench_*.json")

    tx = parser.add_argument_group("TX command hints")
    tx.add_argument("--tx-gain", type=int, default=DEFAULT_TX_GAIN_DB,
                    help="HackRF TX VGA gain used in printed TX command")
    tx.add_argument("--rx-distance", type=float, default=DEFAULT_RX_DISTANCE_M,
                    help="Assumed TX/RX separation for printed TX command")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    profile = load_bench_profile(args.profile, args.config)

    if args.setup_only:
        print_setup(profile, args)
        return

    config = load_config(args.config)
    config = apply_rx_overrides(config, args, profile)
    setup_logging(config.system.log_level)
    source = build_source(config, args, profile)

    print()
    print("SENTINEL HackRF Bench RX")
    print("========================")
    print(f"  Mode:       {'LIVE' if args.live else 'Synthetic'}")
    print(f"  Dual RX:    {'yes' if args.dual else 'no'}")
    print(f"  Profile:    {profile.name}")
    print(f"  TX Center:  {profile.center_freq_hz / 1e9:.4f} GHz")
    print(f"  RX Center:  {config.sdr.rx_a.center_freq_hz / 1e9:.4f} GHz")
    print(f"  Meas BW:    {float(args.signal_bw or profile.measurement_bw_hz) / 1e6:.3f} MHz")
    print(f"  TX command: {build_tx_command(profile.name, args.tx_gain, args.rx_distance)}")
    print()

    output_path = Path(args.output) if args.output else default_output_path(profile.name)
    report = asyncio.run(run_bench(config, source, args, profile, output_path))
    output_path = write_report(report, output_path, profile.name)

    print("Summary")
    print("-------")
    for role, summary in report["channels"].items():
        snr = summary["snr_db"]
        if snr is None:
            print(f"  {role}: no frames")
            continue
        print(
            f"  {role}: max SNR {snr['max']:.1f} dB, "
            f"presence {summary['signal_presence_rate']:.1%}, "
            f"detections {summary['detection_count']}"
        )
    if report["dual_agreement"]:
        agreement = report["dual_agreement"]
        print(
            "  dual: yagi-omni mean SNR delta "
            f"{agreement['mean_snr_delta_db_yagi_minus_omni']:+.1f} dB"
        )
    print(f"  pass: {report['pass']['passed']}")
    print(f"  report: {output_path}")


if __name__ == "__main__":
    main()
