"""Profile resolution: HackRFTxProfileConfig → on-disk .cs8 file ready for hackrf_transfer.

Synthesized profiles are generated lazily into ``cache_dir`` on first
use.  File-based profiles (``iq_source: file``) are converted from cf32
to cs8 if needed.  Cached files are reused on subsequent runs unless
``force_regenerate=True``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from tools.hackrf_tx.config import HackRFTxProfileConfig
from tools.hackrf_tx.synth import (
    cf32_to_cs8,
    synth_ofdm,
    synth_periodic_burst,
    synth_tone,
)

logger = logging.getLogger("sentinel.hackrf_tx")

# Built-in synthesizers for ``iq_source`` values that don't reference a file.
_SYNTH_SOURCES = frozenset({
    "synth_tone",
    "synth_ofdm_burst",
    "synth_ofdm_continuous",
})


def _synth_profile_iq(profile: HackRFTxProfileConfig) -> np.ndarray:
    """Generate complex64 IQ for a synthesized profile."""
    if profile.iq_source == "synth_tone":
        # Tone at DC; no envelope/burst windowing — looping a CW signal
        # at integer-period boundaries gives no discontinuity.
        burst = synth_tone(
            sample_rate_hz=profile.sample_rate_hz,
            duration_s=profile.burst_duration_s,
        )
    elif profile.iq_source in ("synth_ofdm_burst", "synth_ofdm_continuous"):
        envelope = "hann" if profile.iq_source == "synth_ofdm_burst" else "rect"
        burst = synth_ofdm(
            sample_rate_hz=profile.sample_rate_hz,
            duration_s=profile.burst_duration_s,
            bandwidth_hz=profile.bandwidth_hz,
            num_subcarriers=profile.num_subcarriers,
            envelope=envelope,
        )
    else:
        raise ValueError(f"_synth_profile_iq called with non-synth source '{profile.iq_source}'")

    if profile.period_s > 0:
        return synth_periodic_burst(
            burst_iq=burst,
            sample_rate_hz=profile.sample_rate_hz,
            period_s=profile.period_s,
        )
    return burst


def resolve_profile_iq(
    profile: HackRFTxProfileConfig,
    cache_dir: str | Path,
    iq_file_override: str | Path | None = None,
    force_regenerate: bool = False,
) -> Path:
    """Return path to a ``.cs8`` file ready for ``hackrf_transfer -t``.

    For synthesized profiles, generates and caches the IQ on first call.
    For ``iq_source: file`` profiles, accepts ``.cs8`` directly or
    converts ``.cf32`` → ``.cs8`` into the cache.
    """
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)

    # --- File-based profile (replay a captured IQ recording) ---
    if profile.iq_source == "file":
        src = iq_file_override if iq_file_override else profile.iq_file
        if not src:
            raise ValueError(
                f"profile '{profile.name}': iq_source=file but no iq_file set "
                f"(specify in config.yaml or pass --iq)"
            )
        src_path = Path(src)
        if not src_path.exists():
            raise FileNotFoundError(f"IQ file not found: {src_path}")

        if src_path.suffix == ".cs8":
            return src_path

        # Convert cf32 → cs8 into cache.
        dst = cache / f"{profile.name}_{src_path.stem}.cs8"
        if dst.exists() and not force_regenerate:
            logger.info("profile %s: reusing cached cs8 %s", profile.name, dst)
            return dst

        iq = np.fromfile(src_path, dtype=np.complex64)
        cs8 = cf32_to_cs8(iq)
        cs8.tofile(dst)
        logger.info(
            "profile %s: converted %s → %s (%d samples)",
            profile.name, src_path, dst, len(iq),
        )
        return dst

    # --- Synthesized profile ---
    if profile.iq_source not in _SYNTH_SOURCES:
        raise ValueError(
            f"profile '{profile.name}': unknown iq_source '{profile.iq_source}'"
        )

    dst = cache / f"{profile.name}.cs8"
    if dst.exists() and not force_regenerate:
        logger.info("profile %s: reusing cached cs8 %s", profile.name, dst)
        return dst

    iq = _synth_profile_iq(profile)
    cs8 = cf32_to_cs8(iq)
    cs8.tofile(dst)
    logger.info(
        "profile %s: synthesized %s → %s (%d samples, %.3f s @ %.2f MS/s)",
        profile.name,
        profile.iq_source,
        dst,
        len(iq),
        len(iq) / profile.sample_rate_hz,
        profile.sample_rate_hz / 1e6,
    )
    return dst
