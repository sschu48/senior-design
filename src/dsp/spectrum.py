"""Power spectral density estimation for SENTINEL.

Provides Welch-based PSD computation on complex IQ data, returning
frequency axis (Hz) and power per bin (dBm).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from scipy.signal import welch

if TYPE_CHECKING:
    from src.sdr.config import SentinelConfig


def remove_dc_offset(iq: np.ndarray, window: int = 1024) -> np.ndarray:
    """Remove DC offset by subtracting the running mean.

    For short blocks (len <= window), subtracts the full-block mean.
    For longer blocks, subtracts a windowed mean computed over the last
    *window* samples.  This keeps the operation simple and fast.

    Parameters
    ----------
    iq : np.ndarray
        Complex IQ samples.
    window : int
        Number of trailing samples used for mean estimation.

    Returns
    -------
    np.ndarray
        DC-corrected IQ samples (same dtype as input).
    """
    if len(iq) <= window:
        return iq - np.mean(iq)
    return iq - np.mean(iq[-window:])


def compute_psd(
    iq: np.ndarray,
    sample_rate: float,
    fft_size: int = 2048,
    window: str = "hann",
    overlap: float = 0.5,
    center_freq: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute power spectral density of complex IQ samples.

    Uses ``scipy.signal.welch`` with ``return_onesided=False`` for complex
    data, then ``fftshift`` to get natural frequency ordering.

    Parameters
    ----------
    iq : np.ndarray
        Complex IQ samples.
    sample_rate : float
        Sample rate in Hz.
    fft_size : int
        FFT length (nperseg for Welch).
    window : str
        Window function name (e.g. "hann", "hamming", "blackman").
    overlap : float
        Fractional overlap in [0, 1).
    center_freq : float
        Center frequency in Hz.  Added to the frequency axis so output
        is in absolute Hz.

    Returns
    -------
    freq_hz : np.ndarray
        Frequency axis in Hz, length *fft_size*.
    power_dbm : np.ndarray
        Power per bin in dBm, length *fft_size*.
    """
    noverlap = int(fft_size * overlap)

    freqs, psd = welch(
        iq,
        fs=sample_rate,
        nperseg=fft_size,
        noverlap=noverlap,
        window=window,
        return_onesided=False,
        scaling="density",
    )

    # Natural frequency ordering (negative → positive)
    freqs = np.fft.fftshift(freqs)
    psd = np.fft.fftshift(psd)

    # PSD (V²/Hz) → power per bin (V²) → dBm
    # power_per_bin = PSD * (fs / fft_size)  [bin width = fs/N]
    bin_width = sample_rate / fft_size
    power_watts = psd * bin_width

    # dBm = 10 * log10(power_watts) + 30
    # Guard against log of zero
    power_watts = np.maximum(power_watts, 1e-30)
    power_dbm = 10.0 * np.log10(power_watts) + 30.0

    freq_hz = freqs + center_freq

    return freq_hz, power_dbm


def compute_psd_from_config(
    iq: np.ndarray,
    config: SentinelConfig,
    sample_rate: float | None = None,
    center_freq: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Convenience wrapper that pulls DSP params from config.

    Parameters
    ----------
    iq : np.ndarray
        Complex IQ samples.
    config : SentinelConfig
        Loaded SENTINEL configuration.
    sample_rate : float, optional
        Override sample rate.  Defaults to ``config.sdr.rx_a.sample_rate_hz``.
    center_freq : float, optional
        Override center frequency.  Defaults to ``config.sdr.rx_a.center_freq_hz``.
    """
    if sample_rate is None:
        sample_rate = config.sdr.rx_a.sample_rate_hz
    if center_freq is None:
        center_freq = config.sdr.rx_a.center_freq_hz

    iq = remove_dc_offset(iq, window=config.dsp.dc_offset_window)

    return compute_psd(
        iq,
        sample_rate=sample_rate,
        fft_size=config.dsp.fft_size,
        window=config.dsp.window,
        overlap=config.dsp.overlap,
        center_freq=center_freq,
    )
