"""SENTINEL — Dummy-drone HackRF transmitter.

Plays a profile (synthesized OFDM burst, continuous OFDM, CW tone, or a
captured IQ replay) through a HackRF One to mimic a real drone's RF
emission.  Used to bench and field-test the SENTINEL detection pipeline
without flying actual hardware.

Cadence model
-------------
Every profile is rendered as one full period of IQ on disk
(burst + zero-pad), then ``hackrf_transfer -R`` loops the file.  This
gives sample-accurate burst timing without per-burst subprocess spawn
overhead.

Safety (no attenuator)
----------------------
This runner targets a setup with no inline attenuator on the RX side.
Defaults are conservative:

- ``enable_amp`` is False (no +14 dB front-end amp).
- ``--allow-amp`` must be passed *and* config must enable it for the amp
  to turn on.
- A startup banner prints estimated TX power and the resulting power at
  the B210 input through a +12 dBi Yagi at the configured separation
  distance, flagging if the link exceeds the B210's -15 dBm safe input.

Usage
-----
    python -m tools.hackrf_tx                                  # default profile
    python -m tools.hackrf_tx --profile dji_droneid
    python -m tools.hackrf_tx --profile ocusync_video --gain 30
    python -m tools.hackrf_tx --profile replay_capture --iq drone.cf32
    python -m tools.hackrf_tx --list-profiles
    python -m tools.hackrf_tx --profile tone_2437 --dry-run    # synth only, no TX
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.hackrf_tx.config import (
    HackRFTxConfig,
    HackRFTxProfileConfig,
    load_hackrf_tx_config,
)
from tools.hackrf_tx.profiles import resolve_profile_iq

logger = logging.getLogger("sentinel.hackrf_tx")


# ---------------------------------------------------------------------------
# Link-budget reference values
# ---------------------------------------------------------------------------

# RX-side: PCB Yagi gain + BPF insertion loss (see CLAUDE.md / config.yaml).
RX_YAGI_GAIN_DBI = 12.0
RX_BPF_LOSS_DB = 2.0
B210_MAX_SAFE_INPUT_DBM = -15.0     # USRP B210 datasheet
HACKRF_AMP_GAIN_DB = 14.0           # HackRF front-end amp (when -a 1)

# Approximate HackRF One TX output power vs. ``-x VGA_GAIN`` at 2.4 GHz with
# the front-end amp OFF.  Sourced from Great Scott Gadgets community
# measurements; unit-to-unit variation ±3 dB.  Used only for the safety
# banner — actual operation should be confirmed with a power meter when
# precision matters.
_HACKRF_OUTPUT_DBM_BY_VGA: dict[int, float] = {
    0: -55.0,
    8: -40.0,
    16: -25.0,
    24: -15.0,
    32: -8.0,
    40: -3.0,
    47: 0.0,
}

# Speed of light expression of FSPL is `20·log10(d) + 20·log10(f) - 147.55`
# when d is in meters and f is in Hz.
_FSPL_CONST_DB = 147.55


# ---------------------------------------------------------------------------
# Link-budget helpers
# ---------------------------------------------------------------------------

def estimate_hackrf_output_dbm(vga_gain_db: int, amp_on: bool) -> float:
    """Approximate HackRF One TX output power at 2.4 GHz."""
    keys = sorted(_HACKRF_OUTPUT_DBM_BY_VGA)
    g = max(keys[0], min(keys[-1], int(vga_gain_db)))
    lo = max(k for k in keys if k <= g)
    hi = min(k for k in keys if k >= g)
    if lo == hi:
        out = _HACKRF_OUTPUT_DBM_BY_VGA[lo]
    else:
        frac = (g - lo) / (hi - lo)
        out = (
            _HACKRF_OUTPUT_DBM_BY_VGA[lo]
            + frac * (_HACKRF_OUTPUT_DBM_BY_VGA[hi] - _HACKRF_OUTPUT_DBM_BY_VGA[lo])
        )
    if amp_on:
        out += HACKRF_AMP_GAIN_DB
    return out


def fspl_db(distance_m: float, freq_hz: float) -> float:
    """Free-space path loss in dB at the given distance and carrier."""
    if distance_m <= 0 or freq_hz <= 0:
        raise ValueError(f"distance_m and freq_hz must be > 0 (got {distance_m}, {freq_hz})")
    return 20.0 * math.log10(distance_m) + 20.0 * math.log10(freq_hz) - _FSPL_CONST_DB


def estimate_b210_input_dbm(
    tx_power_dbm: float,
    distance_m: float,
    freq_hz: float,
    yagi_gain_dbi: float = RX_YAGI_GAIN_DBI,
    bpf_loss_db: float = RX_BPF_LOSS_DB,
) -> float:
    """Estimate received power at the B210 input through Yagi + BPF."""
    return tx_power_dbm - fspl_db(distance_m, freq_hz) + yagi_gain_dbi - bpf_loss_db


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _print_banner(
    profile: HackRFTxProfileConfig,
    iq_path: Path,
    tx_vga: int,
    amp_on: bool,
    rx_distance_m: float,
) -> None:
    """Print the startup banner with a link-budget safety summary."""
    tx_dbm = estimate_hackrf_output_dbm(tx_vga, amp_on)
    rx_dbm = estimate_b210_input_dbm(tx_dbm, rx_distance_m, profile.center_freq_hz)
    margin = B210_MAX_SAFE_INPUT_DBM - rx_dbm

    print("=" * 60)
    print("SENTINEL — Dummy Drone TX (HackRF One)")
    print("=" * 60)
    print(f"  Profile:        {profile.name}")
    print(f"  Description:    {profile.description}")
    print(f"  Center freq:    {profile.center_freq_hz / 1e9:.4f} GHz")
    print(f"  Sample rate:    {profile.sample_rate_hz / 1e6:.2f} MS/s")
    print(f"  IQ file:        {iq_path}")
    file_bytes = iq_path.stat().st_size
    print(f"  File size:      {file_bytes / 1024:.1f} KiB "
          f"({file_bytes // 2} cs8 samples, "
          f"{file_bytes / 2 / profile.sample_rate_hz * 1000:.1f} ms loop)")
    if profile.period_s > 0:
        print(f"  Burst:          {profile.burst_duration_s * 1000:.1f} ms "
              f"every {profile.period_s * 1000:.0f} ms")
    else:
        print("  Burst:          continuous (no inter-burst silence)")
    print()
    print(f"  TX VGA gain:    {tx_vga} dB (range: 0–47)")
    print(f"  RF amp:         {'ON  (+14 dB)' if amp_on else 'OFF'}")
    print(f"  Est. TX power:  {tx_dbm:+.1f} dBm")
    print()
    print(f"  RX separation:  {rx_distance_m:.1f} m (assumed)")
    print(f"  At B210 input:  {rx_dbm:+.1f} dBm "
          f"(margin to {B210_MAX_SAFE_INPUT_DBM:+.0f} dBm limit: {margin:+.1f} dB)")
    if margin < 0:
        print("  *** WARNING: estimated B210 input EXCEEDS safe limit. ***")
        print("  Increase --rx-distance, lower --gain, or add inline attenuation.")
    elif margin < 6:
        print("  ! Margin <6 dB — consider more separation or lower gain.")
    print("=" * 60)


def _structured_log(event: str, **fields: object) -> None:
    """Emit a single-line JSON log record to ``logger``."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    logger.info(json.dumps(record))


def _build_hackrf_cmd(
    iq_path: Path,
    profile: HackRFTxProfileConfig,
    tx_vga: int,
    amp_on: bool,
    device_serial: str,
) -> list[str]:
    """Construct the hackrf_transfer command for continuous looped TX."""
    cmd = [
        "hackrf_transfer",
        "-t", str(iq_path),
        "-f", str(int(profile.center_freq_hz)),
        "-s", str(int(profile.sample_rate_hz)),
        "-x", str(int(tx_vga)),
        "-R",  # repeat file forever — this is what gives us cadence
    ]
    if amp_on:
        cmd += ["-a", "1"]
    if device_serial:
        cmd += ["-d", device_serial]
    return cmd


def run_tx(
    config: HackRFTxConfig,
    profile: HackRFTxProfileConfig,
    iq_path: Path,
    tx_vga: int,
    amp_on: bool,
    rx_distance_m: float,
) -> int:
    """Spawn ``hackrf_transfer`` and stream until interrupted.

    Returns the subprocess exit code (0 on clean Ctrl-C).
    """
    if shutil.which("hackrf_transfer") is None:
        raise SystemExit(
            "hackrf_transfer not found on PATH. Install: "
            "`brew install hackrf` (macOS) or `sudo apt install hackrf` (Pi/Ubuntu)."
        )

    cmd = _build_hackrf_cmd(iq_path, profile, tx_vga, amp_on, config.device_serial)
    _print_banner(profile, iq_path, tx_vga, amp_on, rx_distance_m)
    print(f"\n  hackrf_transfer command:\n    {' '.join(cmd)}\n")
    print("  Transmitting — Ctrl-C to stop.\n")

    _structured_log(
        "tx_start",
        profile=profile.name,
        center_freq_hz=profile.center_freq_hz,
        sample_rate_hz=profile.sample_rate_hz,
        tx_vga_gain_db=tx_vga,
        amp_on=amp_on,
        iq_path=str(iq_path),
        rx_distance_m=rx_distance_m,
    )

    try:
        proc = subprocess.Popen(cmd)
        rc = proc.wait()
    except KeyboardInterrupt:
        print("\n  Interrupted — stopping HackRF.")
        proc.terminate()
        try:
            rc = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = proc.wait()

    _structured_log("tx_stop", profile=profile.name, exit_code=rc)
    return rc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _list_profiles(config: HackRFTxConfig) -> None:
    print(f"\n  Available profiles (default: {config.default_profile}):\n")
    name_w = max(len(n) for n in config.profiles) + 2
    for name, p in config.profiles.items():
        marker = "*" if name == config.default_profile else " "
        print(f"  {marker} {name:<{name_w}} {p.description}")
        print(f"     {' ':<{name_w}} freq={p.center_freq_hz/1e9:.3f} GHz, "
              f"rate={p.sample_rate_hz/1e6:.1f} MS/s, "
              f"burst={p.burst_duration_s*1000:.1f}ms, "
              f"period={p.period_s*1000:.0f}ms")
    print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SENTINEL dummy-drone HackRF transmitter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Safety notes:\n"
            "  - This tool defaults to NO RF amp and a moderate VGA gain. With\n"
            "    --rx-distance >= 3m the link is normally safe for the B210.\n"
            "  - --allow-amp is required to enable the +14 dB amp; even then,\n"
            "    config.yaml hackrf_tx.enable_amp must also be true.\n"
            "  - When margin to -15 dBm is <6 dB the banner warns you. Heed it."
        ),
    )
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--profile", default=None,
                        help="Profile name (default: hackrf_tx.default_profile)")
    parser.add_argument("--list-profiles", action="store_true",
                        help="List available profiles and exit")

    # Overrides
    parser.add_argument("--gain", type=int, default=None,
                        help="Override TX VGA gain (0-47 dB)")
    parser.add_argument("--allow-amp", action="store_true",
                        help="Permit enabling the +14 dB RF amp (config must also allow)")
    parser.add_argument("--iq", type=Path, default=None,
                        help="Override iq_file (only for iq_source=file profiles)")
    parser.add_argument("--rx-distance", type=float, default=None,
                        help="Override min_rx_separation_m for the safety estimate (m)")

    # Cache control
    parser.add_argument("--regenerate", action="store_true",
                        help="Force regeneration of cached synthesized IQ")
    parser.add_argument("--dry-run", action="store_true",
                        help="Synthesize/locate IQ and print banner; do not transmit")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Logging — line-oriented; record() formats one JSON object per line.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger("sentinel")
    if not root.handlers:
        root.addHandler(handler)
    root.setLevel(logging.INFO)

    config = load_hackrf_tx_config(args.config)

    if args.list_profiles:
        _list_profiles(config)
        return

    profile_name = args.profile or config.default_profile
    if profile_name not in config.profiles:
        raise SystemExit(
            f"Unknown profile '{profile_name}'. "
            f"Available: {sorted(config.profiles)}"
        )
    profile = config.profiles[profile_name]

    # --- Resolve safety knobs ---
    tx_vga = args.gain if args.gain is not None else config.tx_vga_gain_db
    if not (0 <= tx_vga <= 47):
        raise SystemExit(f"--gain {tx_vga} out of range (0–47)")

    amp_on = config.enable_amp and args.allow_amp
    if config.enable_amp and not args.allow_amp:
        print(
            "  Note: config.yaml has enable_amp=true but --allow-amp not given. "
            "Amp will stay OFF.",
            file=sys.stderr,
        )

    rx_distance_m = (
        args.rx_distance if args.rx_distance is not None else config.min_rx_separation_m
    )

    # --- Resolve IQ file (synth or replay) ---
    iq_path = resolve_profile_iq(
        profile,
        cache_dir=config.cache_dir,
        iq_file_override=args.iq,
        force_regenerate=args.regenerate,
    )

    if args.dry_run:
        _print_banner(profile, iq_path, tx_vga, amp_on, rx_distance_m)
        print("\n  --dry-run: not transmitting.")
        return

    rc = run_tx(
        config=config,
        profile=profile,
        iq_path=iq_path,
        tx_vga=tx_vga,
        amp_on=amp_on,
        rx_distance_m=rx_distance_m,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
