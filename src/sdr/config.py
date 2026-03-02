"""SENTINEL configuration loader.

Reads config.yaml and returns frozen dataclasses for type-safe access.
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
# Frozen dataclasses — immutable after creation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RxChannelConfig:
    antenna: str
    center_freq_hz: float
    sample_rate_hz: float
    bandwidth_hz: float
    gain_db: float
    agc: bool


@dataclass(frozen=True)
class SDRConfig:
    device: str
    driver: str
    rx_a: RxChannelConfig
    rx_b: RxChannelConfig


@dataclass(frozen=True)
class DSPConfig:
    fft_size: int
    window: str
    overlap: float
    dc_offset_window: int


@dataclass(frozen=True)
class SentinelConfig:
    sdr: SDRConfig
    dsp: DSPConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_rx_channel(raw: dict) -> RxChannelConfig:
    """Build RxChannelConfig with explicit numeric casts.

    PyYAML treats scientific notation without a sign (e.g. ``2.437e9``)
    as a string.  We cast the numeric fields to float here.
    """
    return RxChannelConfig(
        antenna=raw["antenna"],
        center_freq_hz=float(raw["center_freq_hz"]),
        sample_rate_hz=float(raw["sample_rate_hz"]),
        bandwidth_hz=float(raw["bandwidth_hz"]),
        gain_db=float(raw["gain_db"]),
        agc=bool(raw["agc"]),
    )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_config(path: str | Path | None = None) -> SentinelConfig:
    """Load config.yaml and return a frozen SentinelConfig.

    Parameters
    ----------
    path : str or Path, optional
        Explicit path to config.yaml.  When *None*, auto-discovers from
        the project root.

    Raises
    ------
    FileNotFoundError
        If the config file does not exist at the resolved path.
    """
    if path is None:
        path = _project_root() / "config.yaml"
    else:
        path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    sdr_raw = raw["sdr"]
    sdr = SDRConfig(
        device=sdr_raw["device"],
        driver=sdr_raw["driver"],
        rx_a=_parse_rx_channel(sdr_raw["rx_a"]),
        rx_b=_parse_rx_channel(sdr_raw["rx_b"]),
    )

    dsp_raw = raw["dsp"]
    dsp = DSPConfig(
        fft_size=dsp_raw["fft_size"],
        window=dsp_raw["window"],
        overlap=dsp_raw["overlap"],
        dc_offset_window=dsp_raw["dc_offset_window"],
    )

    return SentinelConfig(sdr=sdr, dsp=dsp)
