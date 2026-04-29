"""IQ synthesis for the dummy-drone HackRF TX.

Pure functions — no I/O, no logging.  Three profile families:

- ``synth_tone``                  — a single complex sinusoid at DC
- ``synth_ofdm``                  — random-phase OFDM-like wideband
- ``synth_periodic_burst``        — burst + zero-pad to a fixed period
                                    (so ``hackrf_transfer -R`` reproduces
                                    the real burst cadence by looping the
                                    file)

Output is always ``np.complex64``.  Convert to HackRF format with
``cf32_to_cs8``.
"""

from __future__ import annotations

import numpy as np

# --- Amplitude headroom ---
# HackRF is full-scale at ±127.  We leave 5% headroom to avoid clipping
# from peaking after envelope shaping or float→int rounding.
_FULLSCALE_INT8 = 127
_HEADROOM = 1.05

# Fraction of full-scale used after normalization.  0.7 is a good default
# for OFDM (high PAPR) — leaves room for peak excursions above RMS.
_DEFAULT_AMPLITUDE = 0.7


# ---------------------------------------------------------------------------
# Tone
# ---------------------------------------------------------------------------

def synth_tone(
    sample_rate_hz: float,
    duration_s: float,
    freq_offset_hz: float = 0.0,
    amplitude: float = _DEFAULT_AMPLITUDE,
) -> np.ndarray:
    """Single complex sinusoid at ``freq_offset_hz`` from baseband DC."""
    n = int(round(sample_rate_hz * duration_s))
    if n <= 0:
        raise ValueError(f"duration_s={duration_s} too short for rate={sample_rate_hz}")
    t = np.arange(n, dtype=np.float64) / sample_rate_hz
    iq = amplitude * np.exp(1j * 2.0 * np.pi * freq_offset_hz * t)
    return iq.astype(np.complex64)


# ---------------------------------------------------------------------------
# OFDM-like wideband
# ---------------------------------------------------------------------------

def synth_ofdm(
    sample_rate_hz: float,
    duration_s: float,
    bandwidth_hz: float,
    num_subcarriers: int,
    seed: int = 0,
    envelope: str = "hann",
) -> np.ndarray:
    """Sum of random-phase subcarriers across ``bandwidth_hz``.

    Models a wideband OFDM emission spectrally — does *not* implement a
    real OFDM frame structure (no cyclic prefix, no pilots, no Zadoff-Chu
    sync).  Sufficient for exercising CFAR / Welch detectors on a
    DroneID- or OcuSync-shaped signal.

    Parameters
    ----------
    envelope : {"hann", "rect"}
        ``hann`` shapes the burst with a Hann window — clean leading and
        trailing edges, suitable for short bursts whose file will be
        looped on a cadence.
        ``rect`` leaves the signal flat-topped — for continuous streams
        where the looped file boundary should not introduce amplitude
        dips.
    """
    if num_subcarriers <= 0:
        raise ValueError(f"num_subcarriers must be > 0, got {num_subcarriers}")
    if bandwidth_hz > sample_rate_hz:
        raise ValueError(
            f"bandwidth_hz ({bandwidth_hz}) exceeds sample_rate_hz ({sample_rate_hz})"
        )

    n = int(round(sample_rate_hz * duration_s))
    if n <= 0:
        raise ValueError(f"duration_s={duration_s} too short for rate={sample_rate_hz}")

    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=np.float64) / sample_rate_hz

    # Subcarrier spacing centered on baseband DC, spanning the bandwidth.
    freqs = np.linspace(
        -bandwidth_hz / 2.0,
        bandwidth_hz / 2.0,
        num_subcarriers,
        endpoint=False,
    )
    phases = rng.uniform(0.0, 2.0 * np.pi, num_subcarriers)

    # Vectorized sum of complex exponentials (faster than a Python loop).
    # Shape (n, K) intermediate — fine for n*K up to ~1e7.
    arg = 2.0 * np.pi * np.outer(t, freqs) + phases[np.newaxis, :]
    iq = np.exp(1j * arg).sum(axis=1)

    # Normalize so peak ≈ amplitude target with headroom.
    peak = float(np.max(np.abs(iq)))
    if peak > 0:
        iq *= _DEFAULT_AMPLITUDE / (peak * _HEADROOM)

    if envelope == "hann":
        iq *= np.hanning(n)
    elif envelope == "rect":
        pass
    else:
        raise ValueError(f"unknown envelope '{envelope}' (expected 'hann' or 'rect')")

    return iq.astype(np.complex64)


# ---------------------------------------------------------------------------
# Periodic burst (burst + zero-pad)
# ---------------------------------------------------------------------------

def synth_periodic_burst(
    burst_iq: np.ndarray,
    sample_rate_hz: float,
    period_s: float,
) -> np.ndarray:
    """Embed ``burst_iq`` at the start of a ``period_s`` window of zeros.

    Looping this file with ``hackrf_transfer -R`` reproduces the burst
    on the configured cadence with sample-accurate timing.
    """
    n_total = int(round(sample_rate_hz * period_s))
    if n_total < len(burst_iq):
        raise ValueError(
            f"period_s={period_s} too short to hold {len(burst_iq)} burst samples "
            f"at rate={sample_rate_hz}"
        )
    out = np.zeros(n_total, dtype=np.complex64)
    out[: len(burst_iq)] = burst_iq.astype(np.complex64)
    return out


# ---------------------------------------------------------------------------
# Format conversion
# ---------------------------------------------------------------------------

def cf32_to_cs8(iq: np.ndarray) -> np.ndarray:
    """complex64 → interleaved int8 (HackRF cs8 format).

    Normalizes to ±127 with 5% headroom.  Real and imaginary parts are
    interleaved I,Q,I,Q,... in the output (length = 2 × len(iq)).
    """
    if iq.size == 0:
        return np.zeros(0, dtype=np.int8)

    peak = float(np.max(np.abs(iq)))
    if peak == 0.0:
        # All-zero input → all-zero output.
        return np.zeros(iq.size * 2, dtype=np.int8)

    scale = _FULLSCALE_INT8 / (peak * _HEADROOM)
    out = np.empty(iq.size * 2, dtype=np.int8)
    out[0::2] = np.clip(np.real(iq) * scale, -_FULLSCALE_INT8, _FULLSCALE_INT8).astype(np.int8)
    out[1::2] = np.clip(np.imag(iq) * scale, -_FULLSCALE_INT8, _FULLSCALE_INT8).astype(np.int8)
    return out


def cs8_to_cf32(cs8: np.ndarray) -> np.ndarray:
    """interleaved int8 → complex64 (inverse of ``cf32_to_cs8``)."""
    if cs8.size % 2 != 0:
        raise ValueError(f"cs8 array length {cs8.size} must be even")
    real = cs8[0::2].astype(np.float32) / _FULLSCALE_INT8
    imag = cs8[1::2].astype(np.float32) / _FULLSCALE_INT8
    return (real + 1j * imag).astype(np.complex64)
