"""HackRF TX configuration loader.

Reads the ``hackrf_tx`` section of config.yaml into frozen dataclasses.
Kept separate from ``src.sdr.config.SentinelConfig`` so the dummy-drone
tooling can evolve without coupling to the receive-pipeline schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


def _project_root() -> Path:
    """Walk up from this file to find the repo root (contains config.yaml)."""
    current = Path(__file__).resolve().parent
    for parent in [current, *current.parents]:
        if (parent / "config.yaml").exists():
            return parent
    raise FileNotFoundError("config.yaml not found in any parent directory")


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

# Allowed values for the ``iq_source`` profile field.
IQ_SOURCE_TYPES = frozenset({
    "synth_tone",
    "synth_ofdm_burst",
    "synth_ofdm_continuous",
    "file",
})


@dataclass(frozen=True)
class HackRFTxProfileConfig:
    """One named profile (e.g. dji_droneid, ocusync_video)."""

    name: str
    description: str
    iq_source: str
    iq_file: str
    center_freq_hz: float
    sample_rate_hz: float
    bandwidth_hz: float
    burst_duration_s: float
    period_s: float
    num_subcarriers: int


@dataclass(frozen=True)
class HackRFTxConfig:
    device_serial: str
    default_profile: str
    tx_vga_gain_db: int
    enable_amp: bool
    min_rx_separation_m: float
    cache_dir: str
    profiles: dict[str, HackRFTxProfileConfig]


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _parse_profile(name: str, raw: dict) -> HackRFTxProfileConfig:
    iq_source = str(raw["iq_source"])
    if iq_source not in IQ_SOURCE_TYPES:
        raise ValueError(
            f"profile '{name}': iq_source '{iq_source}' must be one of "
            f"{sorted(IQ_SOURCE_TYPES)}"
        )
    return HackRFTxProfileConfig(
        name=name,
        description=str(raw.get("description", "")),
        iq_source=iq_source,
        iq_file=str(raw.get("iq_file", "")),
        center_freq_hz=float(raw["center_freq_hz"]),
        sample_rate_hz=float(raw["sample_rate_hz"]),
        bandwidth_hz=float(raw["bandwidth_hz"]),
        burst_duration_s=float(raw["burst_duration_s"]),
        period_s=float(raw["period_s"]),
        num_subcarriers=int(raw["num_subcarriers"]),
    )


def load_hackrf_tx_config(path: str | Path | None = None) -> HackRFTxConfig:
    """Load ``hackrf_tx`` from config.yaml and return a frozen config."""
    if path is None:
        path = _project_root() / "config.yaml"
    else:
        path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if "hackrf_tx" not in raw:
        raise KeyError(
            "config.yaml missing 'hackrf_tx' section — "
            "see tools/hackrf_tx/README.md"
        )

    htx = raw["hackrf_tx"]

    profiles_raw = htx.get("profiles") or {}
    if not profiles_raw:
        raise ValueError("hackrf_tx.profiles is empty — no profiles to use")

    profiles = {
        name: _parse_profile(name, p) for name, p in profiles_raw.items()
    }

    default_profile = str(htx["default_profile"])
    if default_profile not in profiles:
        raise ValueError(
            f"default_profile '{default_profile}' is not defined in "
            f"hackrf_tx.profiles (available: {sorted(profiles)})"
        )

    return HackRFTxConfig(
        device_serial=str(htx.get("device_serial", "")),
        default_profile=default_profile,
        tx_vga_gain_db=int(htx["tx_vga_gain_db"]),
        enable_amp=bool(htx["enable_amp"]),
        min_rx_separation_m=float(htx["min_rx_separation_m"]),
        cache_dir=str(htx["cache_dir"]),
        profiles=profiles,
    )
